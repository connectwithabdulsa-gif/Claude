"""turns DataSource rows into companies + leads. dedupe, suppression, source tracking."""
import json
from collections import Counter
from dataclasses import dataclass, field

from ..db import log_event, now
from ..normalize import norm_domain, norm_email, parse_int
from .. import suppression
from .base import DataSource


@dataclass
class ImportResult:
    import_id: int
    rows_read: int = 0
    leads_added: int = 0
    companies_added: int = 0
    companies_updated: int = 0
    rejected: Counter = field(default_factory=Counter)
    unmapped_columns: list = field(default_factory=list)
    lead_ids: dict = field(default_factory=dict)      # source row line -> lead id
    company_ids: dict = field(default_factory=dict)   # source row line -> company id


def _split_name(fields: dict) -> tuple[str, str]:
    first, last = fields.get("first_name", ""), fields.get("last_name", "")
    if not first and fields.get("full_name"):
        parts = fields["full_name"].split()
        first, last = parts[0], " ".join(parts[1:])
    return first, last


def _upsert_company(conn, domain, fields, info, import_id, result) -> int:
    row = conn.execute("SELECT id FROM companies WHERE domain=?", (domain,)).fetchone()
    ts = now()
    if row:
        # fill blanks only. never overwrite data we already have.
        conn.execute(
            """UPDATE companies SET
                 name=COALESCE(NULLIF(name,''), ?), industry=COALESCE(NULLIF(industry,''), ?),
                 headcount=COALESCE(headcount, ?), country=COALESCE(NULLIF(country,''), ?),
                 linkedin_url=COALESCE(NULLIF(linkedin_url,''), ?), updated_at=?
               WHERE id=?""",
            (fields.get("company_name"), fields.get("industry"), parse_int(fields.get("headcount")),
             fields.get("company_country"), fields.get("company_linkedin_url"), ts, row["id"]),
        )
        result.companies_updated += 1
        return row["id"]
    cur = conn.execute(
        """INSERT INTO companies (domain, name, industry, headcount, country, linkedin_url,
             source, source_file, import_id, first_seen_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (domain, fields.get("company_name"), fields.get("industry"),
         parse_int(fields.get("headcount")), fields.get("company_country"),
         fields.get("company_linkedin_url"), info.source, info.source_file, import_id, ts, ts),
    )
    result.companies_added += 1
    return cur.lastrowid


def run_import(conn, source: DataSource) -> ImportResult:
    info = source.info
    cur = conn.execute(
        "INSERT INTO imports (source, source_file, started_at) VALUES (?,?,?)",
        (info.source, info.source_file, now()),
    )
    result = ImportResult(import_id=cur.lastrowid)
    seen_in_file: set[str] = set()

    def reject(reason: str, row, email=""):
        result.rejected[reason] += 1
        log_event(conn, "import_rejected", "row", f"{info.source_file}:{row.line}", reason,
                  email=email, import_id=result.import_id)

    for row in source.rows():
        result.rows_read += 1
        f = row.fields
        email = norm_email(f.get("email"))
        if not email or "@" not in email:
            reject("missing email", row, email)
            continue
        email_domain = norm_domain(email)
        company_domain = norm_domain(f.get("company_domain")) or email_domain

        if email in seen_in_file:
            reject("duplicate in file", row, email)
            continue
        seen_in_file.add(email)

        if conn.execute("SELECT 1 FROM leads WHERE email=?", (email,)).fetchone():
            reject("already in database", row, email)
            continue

        if (f.get("email_status") or "").strip().lower() == "invalid":
            reject("email invalid per source", row, email)
            continue

        why = suppression.check(conn, email, company_domain)
        if why:
            reject("suppressed", row, email)
            log_event(conn, "suppressed_on_import", "lead", email, why)
            continue

        company_id = _upsert_company(conn, company_domain, f, info, result.import_id, result)
        first, last = _split_name(f)
        ts = now()
        cur = conn.execute(
            """INSERT INTO leads (company_id, email, email_domain, first_name, last_name, title,
                 linkedin_url, country, email_status_source, source, source_file, import_id,
                 imported_at, updated_at, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company_id, email, email_domain, first, last, f.get("title"), f.get("linkedin_url"),
             f.get("person_country") or f.get("company_country"), f.get("email_status"),
             info.source, info.source_file, result.import_id, ts, ts,
             json.dumps(row.raw, ensure_ascii=False)),
        )
        result.leads_added += 1
        result.lead_ids[row.line] = cur.lastrowid
        result.company_ids[row.line] = company_id

    result.unmapped_columns = info.unmapped_columns
    conn.execute(
        """UPDATE imports SET rows_read=?, leads_added=?, companies_added=?, rejected_json=?,
             unmapped_columns=? WHERE id=?""",
        (result.rows_read, result.leads_added, result.companies_added,
         json.dumps(dict(result.rejected)), json.dumps(result.unmapped_columns), result.import_id),
    )
    log_event(conn, "import_done", "import", result.import_id, None,
              rows=result.rows_read, leads=result.leads_added, rejected=dict(result.rejected))
    conn.commit()
    return result
