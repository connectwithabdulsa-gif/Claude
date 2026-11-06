"""find prospects through Explorium. dry run = free stats calls only. live = paid, capped.

live pipeline for one niche:
  1. fetch businesses with niche filters + events in the lookback window   (1 credit each)
  2. drop companies already in the db or suppressed                         (free, local)
  3. fetch the dated event records for those businesses                     (cost from credit_usage)
  4. fetch decision makers at those businesses, with an email on file       (1 credit each)
  5. keep `per_company` people per company, best job level first            (free, local)
  6. enrich emails only                                                     (2 credits each)
  7. drop invalid emails, import through the normal importer (dedupe + suppression),
     store every field with source and date, store events as signals
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .. import suppression
from ..db import log_event, now
from ..normalize import norm_domain, norm_email
from .base import SourceInfo, SourceRow
from .explorium import ExploriumClient
from .importer import run_import

SOURCE = "explorium"
LEVEL_RANK = ["founder", "owner", "c-suite", "president", "partner", "vice president"]


def load_searches(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text()) or {}
    defaults = cfg.get("defaults", {})
    out = {}
    for key, spec in (cfg.get("searches") or {}).items():
        merged = {**defaults, **(spec or {})}
        bad = {c.lower() for c in merged["countries"]} & {"de", "at", "ch"}
        if bad:
            raise ValueError(f"search {key}: DACH countries {sorted(bad)} are off by default")
        out[key] = merged
    return out


def business_filters(s: dict) -> dict:
    f = {
        "country_code": {"values": s["countries"]},
        "company_size": {"values": s["company_size"]},
        "has_website": {"value": True},
    }
    if s.get("naics"):
        f["naics_category"] = {"values": s["naics"]}
    if s.get("website_keywords"):
        f["website_keywords"] = {"values": s["website_keywords"]}
    if s.get("events"):
        f["events"] = {"values": s["events"], "last_occurrence": int(s["lookback_days"])}
    return f


def prospect_filters(s: dict, business_ids: list[str] | None = None) -> dict:
    f = {"job_level": {"values": s["job_levels"]},
         "company_country_code": {"values": s["countries"]},
         "company_size": {"values": s["company_size"]}}
    if s.get("has_email", True):
        f["has_contact_details"] = {"value": "email"}
    if business_ids:
        f["business_id"] = {"values": business_ids}
    elif s.get("naics"):
        f["naics_category"] = {"values": s["naics"]}
    return f


@dataclass
class Estimate:
    businesses_matching: int | None
    prospects_matching: int | None
    limit: int
    per_company: int
    known_credits: int
    events_calls: int
    notes: list = field(default_factory=list)


def estimate(client: ExploriumClient | None, s: dict, limit: int) -> Estimate:
    """free. worst case credits for a live run of `limit` companies."""
    per = int(s["per_company"])
    # businesses (1 each) + prospects page of up to 2x per company (1 each) + emails (2 each)
    known = limit + limit * per * 2 + limit * per * 2
    events_calls = -(-limit // 40) if s.get("events") else 0
    est = Estimate(None, None, limit, per, known, events_calls)
    if client is None:
        est.notes.append("no EXPLORIUM_API_KEY, so no live counts. numbers are worst case only.")
        return est
    b = client.businesses_stats(business_filters(s))
    p = client.prospects_stats(prospect_filters(s))
    est.businesses_matching = b.get("total_results")
    est.prospects_matching = p.get("total_results")
    if events_calls:
        est.notes.append("events cost is not in the docs. the real cost is read from each response.")
    return est


def _event_value(ev: dict) -> str:
    d = ev.get("data") or {}
    name = ev.get("event_name") or d.get("event_name", "")
    if name.startswith("hiring_in_"):
        titles = d.get("job_titles") or []
        return f"{d.get('job_count') or len(titles)} open roles: {', '.join(titles[:5])}".strip()
    if name == "new_funding_round":
        stage = d.get("funding_stage") or d.get("founding_stage") or ""
        amt = d.get("amount_raised")
        return " ".join(x for x in [stage, f"${amt:,.0f}" if isinstance(amt, (int, float)) else ""] if x) or name
    if name == "executive_joined_company":
        return f"{d.get('full_name', '')} joined as {d.get('job_role_title', '')}".strip()
    if name == "new_office" and d.get("office_location"):
        return f"new office in {d['office_location']}"
    if name == "new_partnership" and d.get("partner_company"):
        return f"partnership with {d['partner_company']}"
    if name.startswith("increase_in_") and d.get("department_change") is not None:
        return f"{d.get('department', name)} headcount change {d['department_change']}"
    for k in ("product_name", "title", "snippet", "link"):
        if d.get(k):
            return str(d[k])
    return name


def _email_status(raw) -> str:
    if isinstance(raw, dict):
        raw = raw.get("status") or raw.get("value") or next(iter(raw.values()), "")
    return str(raw or "").strip().lower()


class _ListSource:
    def __init__(self, rows, source_file):
        self._rows = rows
        self.info = SourceInfo(source=SOURCE, source_file=source_file)

    def rows(self):
        yield from self._rows


@dataclass
class FindResult:
    run_id: str
    businesses_returned: int = 0
    businesses_skipped: dict = field(default_factory=dict)
    events_found: int = 0
    prospects_returned: int = 0
    emails_found: int = 0
    emails_dropped: dict = field(default_factory=dict)
    import_result: object = None
    credits_spent: int = 0
    unknown_cost_calls: int = 0


def run_live(conn, client: ExploriumClient, niche: str, s: dict, limit: int) -> FindResult:
    run_id = f"find-{niche}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    res = FindResult(run_id=run_id)
    fetched_at = now()
    log_event(conn, "find_started", "run", run_id, None, niche=niche, limit=limit)

    # 1 + 2. businesses
    resp = client.fetch_businesses(business_filters(s), page_size=limit)
    businesses = resp.get("data") or []
    res.businesses_returned = len(businesses)
    keep, skipped = [], {}
    for b in businesses:
        dom = norm_domain(b.get("domain") or b.get("website"))
        if not dom:
            skipped["no domain"] = skipped.get("no domain", 0) + 1
        elif conn.execute("SELECT 1 FROM companies WHERE domain=?", (dom,)).fetchone():
            skipped["already in database"] = skipped.get("already in database", 0) + 1
        elif suppression.check(conn, f"x@{dom}", dom):
            skipped["suppressed"] = skipped.get("suppressed", 0) + 1
        else:
            b["_domain"] = dom
            keep.append(b)
    res.businesses_skipped = skipped
    by_bid = {b["business_id"]: b for b in keep}
    biz_ref = f"/v2/businesses#{(resp.get('response_context') or {}).get('correlation_id', '')}"

    # 3. events with dates
    events_by_bid: dict[str, list] = {}
    if s.get("events") and by_bid:
        ts_from = (datetime.now(timezone.utc) - timedelta(days=int(s["lookback_days"]))).strftime("%Y-%m-%dT00:00:00Z")
        evs = client.business_events(list(by_bid), s["events"], ts_from, worst_case_per_call=40)
        for ev in evs:
            events_by_bid.setdefault(ev.get("business_id"), []).append(ev)
        res.events_found = len(evs)

    # 4 + 5. decision makers
    people_by_bid: dict[str, list] = {}
    if by_bid:
        presp = client.fetch_prospects(prospect_filters(s, list(by_bid)),
                                       page_size=min(500, len(by_bid) * int(s["per_company"]) * 2))
        prospects = presp.get("data") or []
        res.prospects_returned = len(prospects)
        rank = lambda p: min((LEVEL_RANK.index(l) for l in (p.get("job_level_array") or [p.get("job_level_main")])
                              if l in LEVEL_RANK), default=99)
        for p in sorted(prospects, key=rank):
            if p.get("business_id") not in by_bid or not p.get("prospect_id"):
                continue
            lst = people_by_bid.setdefault(p.get("business_id"), [])
            if len(lst) < int(s["per_company"]):
                lst.append(p)

    # 6. emails
    chosen = [p for lst in people_by_bid.values() for p in lst]
    contacts = {}
    if chosen:
        for row in client.enrich_emails([p["prospect_id"] for p in chosen]):
            d = row.get("data")
            d = d[0] if isinstance(d, list) and d else d
            if d:
                contacts[row.get("prospect_id")] = d

    # 7. build rows, import, provenance
    rows, meta = [], {}
    dropped = {}
    for i, p in enumerate(chosen, start=1):
        c = contacts.get(p["prospect_id"]) or {}
        email = norm_email(c.get("professional_email"))
        status = _email_status(c.get("professional_email_status"))
        if not email:
            dropped["no email returned"] = dropped.get("no email returned", 0) + 1
            continue
        res.emails_found += 1
        b = by_bid[p["business_id"]]
        fields = {
            "first_name": p.get("first_name") or "", "last_name": p.get("last_name") or "",
            "title": p.get("job_title") or "", "email": email, "email_status": status,
            "linkedin_url": p.get("linkedin") or "", "person_country": p.get("country_name") or "",
            "company_name": b.get("name") or p.get("company_name") or "", "company_domain": b["_domain"],
            "company_linkedin_url": b.get("linkedin_profile") or "", "industry": b.get("naics_description") or "",
            "headcount": b.get("number_of_employees_range") or "", "company_country": b.get("country_name") or "",
        }
        raw = {"business": {k: v for k, v in b.items() if not k.startswith("_")}, "prospect": p, "contact": c}
        rows.append(SourceRow(fields=fields, raw=raw, line=i))
        meta[i] = (p, b, c)
    res.emails_dropped = dropped

    imp = run_import(conn, _ListSource(rows, run_id))
    res.import_result = imp

    for line, lead_id in imp.lead_ids.items():
        p, b, c = meta[line]
        company_id = imp.company_ids[line]
        _store_fields(conn, "lead", lead_id, {
            "first_name": p.get("first_name"), "last_name": p.get("last_name"), "title": p.get("job_title"),
            "job_level": p.get("job_level_main"), "linkedin_url": p.get("linkedin"),
            "country": p.get("country_name"), "explorium_prospect_id": p.get("prospect_id")},
            "/v2/prospects", fetched_at)
        _store_fields(conn, "lead", lead_id, {
            "email": c.get("professional_email"), "email_status": _email_status(c.get("professional_email_status"))},
            "/v2/prospects/contact_information/enrich", fetched_at)
        _store_fields(conn, "company", company_id, {
            "name": b.get("name"), "domain": b["_domain"], "naics": b.get("naics"),
            "naics_description": b.get("naics_description"), "size_range": b.get("number_of_employees_range"),
            "revenue_range": b.get("yearly_revenue_range"), "country": b.get("country_name"),
            "description": b.get("business_description"), "linkedin": b.get("linkedin_profile"),
            "explorium_business_id": b.get("business_id")}, biz_ref, fetched_at)
        for ev in events_by_bid.get(b["business_id"], []):
            _store_signal(conn, company_id, ev, fetched_at)
        events_by_bid.pop(b["business_id"], None)   # store once per company

    res.credits_spent = client.budget.spent if client.budget else 0
    res.unknown_cost_calls = client.budget.unknown_cost_calls if client.budget else 0
    log_event(conn, "find_done", "run", run_id, None, credits=res.credits_spent,
              leads=imp.leads_added, rejected=dict(imp.rejected), dropped=dropped)
    conn.commit()
    return res


def _store_fields(conn, entity, entity_id, values: dict, source_ref, fetched_at, observed_at=None):
    for k, v in values.items():
        if v in (None, "", []):
            continue
        conn.execute(
            "INSERT INTO field_values (entity, entity_id, field, value, source, source_ref, observed_at, fetched_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (entity, entity_id, k, v if isinstance(v, str) else json.dumps(v), SOURCE, source_ref,
             observed_at, fetched_at))


def _store_signal(conn, company_id, ev, fetched_at):
    name = ev.get("event_name") or (ev.get("data") or {}).get("event_name") or "unknown"
    conn.execute(
        "INSERT INTO signals (company_id, type, value, source, source_field, observed_at, detected_at, data_json)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (company_id, name, _event_value(ev), SOURCE, f"/v2/businesses/events:{ev.get('event_id', '')}",
         ev.get("event_time"), fetched_at, json.dumps(ev.get("data") or {})))
