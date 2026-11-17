"""csv with the whole sequence already written (First Name, Last Name, Work Email, Company Name,
Email1 Subject/Body ... Email4 Subject/Body). Abdul's own copy, sent as written (his call, Nov 17):
the footer (sign off, address, opt out) is added, capitals at sentence starts are fixed, nothing else
is changed. dedupe, suppression and the free email check still apply."""
import csv
import json
from pathlib import Path
from typing import Iterator

from ..casing import sentence_case
from ..db import log_event, now
from ..normalize import norm_domain, norm_email
from ..personalize.writer import footer
from .base import SourceInfo, SourceRow

STEPS = (1, 2, 3, 4)


def _get(raw: dict, *names) -> str:
    low = {k.strip().lower(): v for k, v in raw.items() if k}
    for n in names:
        v = low.get(n.lower())
        if v:
            return v.strip()
    return ""


class PrewrittenCsvSource:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.info = SourceInfo(source="prewritten", source_file=self.path.name)
        self._rows: list[SourceRow] | None = None

    def rows(self) -> Iterator[SourceRow]:
        if self._rows is None:
            self._rows = []
            with self.path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
                for i, raw in enumerate(csv.DictReader(f), start=1):
                    email = _get(raw, "Work Email", "Email")
                    fields = {"first_name": _get(raw, "First Name"), "last_name": _get(raw, "Last Name"),
                              "email": email, "company_name": _get(raw, "Company Name", "Company"),
                              "company_domain": norm_domain(email), "title": _get(raw, "Job Title", "Title")}
                    self._rows.append(SourceRow(fields=fields, raw=dict(raw), line=i))
            self.info.unmapped_columns = []
        return iter(self._rows)


def add_messages(conn, result, source: PrewrittenCsvSource, sender: dict) -> dict:
    """email 1 approved and ready; steps 2 to 4 stored so the scheduler sends them as written."""
    out = {"leads": 0, "missing_email1": 0}
    rows = {r.line: r for r in source.rows()}
    for line, lead_id in result.lead_ids.items():
        raw = rows[line].raw
        e1s, e1b = _get(raw, "Email1 Subject"), _get(raw, "Email1 Body")
        if not (e1s and e1b):
            out["missing_email1"] += 1
            conn.execute("UPDATE leads SET tier='dnc', score_reason='no email 1 in the csv' WHERE id=?", (lead_id,))
            continue
        for step in STEPS:
            subj, body = _get(raw, f"Email{step} Subject"), _get(raw, f"Email{step} Body")
            if not body:
                continue
            first = step == 1
            conn.execute(
                """INSERT INTO messages (lead_id, step, variant, subject, body, signal_ids, qa_verdict, qa_reasons,
                     created_at, status, model, cost_usd, uses_fallback, facts_json, signal_class, attempts, review,
                     writer_mode, reviewed_at, review_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lead_id, step, "csv", sentence_case(subj) if subj else None,
                 sentence_case(f"{body}\n\n{footer(sender)}"), "[]", "abdul's copy, sent as written", "[]", now(),
                 "ready" if first else "prewritten", "csv", 0.0, 1,
                 json.dumps({"source_file": source.info.source_file, "csv_line": line}), "prewritten", 1,
                 "approved", "prewritten", now(), "abdul's own copy (csv), approved by uploading it"))
        conn.execute("UPDATE leads SET tier='B', score=60, score_reason='prewritten csv (abdul''s copy)',"
                     " niche=COALESCE(niche, 'prewritten') WHERE id=?", (lead_id,))
        out["leads"] += 1
    log_event(conn, "prewritten_import", "import", result.import_id, None, **out)
    conn.commit()
    return out
