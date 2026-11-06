import json

import pytest

from engine import suppression
from engine.ingest import finder
from engine.ingest.explorium import BudgetExceeded, CreditBudget, ExploriumClient

SPEC = {"countries": ["us", "gb", "au"], "company_size": ["11-50"], "job_levels": ["founder", "owner", "c-suite"],
        "per_company": 1, "lookback_days": 90, "has_email": True, "naics": ["541810"],
        "events": ["hiring_in_sales_department", "new_funding_round"]}

BIZ = [
    {"business_id": "b1" * 16, "name": "Acme Ads", "domain": "acme.test", "number_of_employees_range": "11-50",
     "country_name": "united states", "naics_description": "Advertising Agencies"},
    {"business_id": "b2" * 16, "name": "Beta Growth", "website": "https://www.beta.test/", "country_name": "united kingdom"},
    {"business_id": "b3" * 16, "name": "Rival", "domain": "rival.test"},
    {"business_id": "b4" * 16, "name": "No Domain Co"},
]
PROSPECTS = [
    {"prospect_id": "p1" * 20, "business_id": "b1" * 16, "first_name": "Jake", "last_name": "Reed",
     "job_title": "Founder", "job_level_main": "founder", "job_level_array": ["founder"],
     "linkedin": "linkedin.com/in/jake", "country_name": "united states"},
    {"prospect_id": "p9" * 20, "business_id": "b1" * 16, "first_name": "Mo", "job_level_array": ["c-suite"]},
    {"prospect_id": "p2" * 20, "business_id": "b2" * 16, "first_name": "Priya", "last_name": "Patel",
     "job_title": "CEO", "job_level_array": ["c-suite"]},
]
CONTACTS = [
    {"prospect_id": "p1" * 20, "data": {"professional_email": "Jake@acme.test", "professional_email_status": "valid"}},
    {"prospect_id": "p2" * 20, "data": [{"professional_email": "priya@beta.test", "professional_email_status": "invalid"}]},
]
EVENTS = [
    {"event_name": "hiring_in_sales_department", "event_time": "2026-10-20T00:00:00+00:00", "event_id": "e1",
     "business_id": "b1" * 16, "data": {"job_count": 2, "job_titles": ["SDR", "Account Executive"]}},
    {"event_name": "new_funding_round", "event_time": "2026-09-01T00:00:00+00:00", "event_id": "e2",
     "business_id": "b1" * 16, "data": {"funding_stage": "Seed", "amount_raised": 2000000}},
]


class FakeTransport:
    def __init__(self, report_credits=True):
        self.calls = []
        self.report = report_credits

    def post(self, path, body):
        self.calls.append((path, body))
        data, n = {}, 0
        if path == "/v2/businesses/stats":
            return {"total_results": 812}
        if path == "/v2/prospects/stats":
            return {"total_results": 2400}
        if path == "/v2/businesses":
            data, n = {"data": BIZ}, len(BIZ)
        elif path == "/v2/businesses/events":
            data, n = {"output_events": EVENTS}, 1
        elif path == "/v2/prospects":
            ids = set(body["filters"]["business_id"]["values"])
            rows = [p for p in PROSPECTS if p["business_id"] in ids]
            data, n = {"data": rows}, len(rows)
        elif path == "/v2/prospects/contact_information/enrich":
            rows = [c for c in CONTACTS if c["prospect_id"] in body["prospect_ids"]]
            data, n = {"data": rows}, 2 * len(rows)
        data["response_context"] = {"correlation_id": "corr-" + path.strip("/").replace("/", "-")}
        if self.report:
            data["credit_usage"] = {"total_results": len(data.get("data") or data.get("output_events") or []),
                                    "total_credits": n}
        return data


@pytest.fixture
def setup(conn):
    suppression.add(conn, "domain", "rival.test", "competitor")
    t = FakeTransport()
    return conn, t, ExploriumClient(t, CreditBudget(1000))


def test_filters_follow_docs_and_exclude_dach():
    f = finder.business_filters(SPEC)
    assert f["events"] == {"values": SPEC["events"], "last_occurrence": 90}
    assert f["country_code"] == {"values": ["us", "gb", "au"]}
    p = finder.prospect_filters(SPEC, ["b1"])
    assert p["has_contact_details"] == {"value": "email"} and p["business_id"] == {"values": ["b1"]}


def test_load_searches_refuses_dach(tmp_path):
    y = tmp_path / "s.yaml"
    y.write_text("defaults: {countries: [us, de], company_size: [], job_levels: [], per_company: 1,"
                 " lookback_days: 90}\nsearches: {x: {niche: X}}\n")
    with pytest.raises(ValueError, match="DACH"):
        finder.load_searches(y)


def test_repo_searches_config_is_valid():
    from engine.paths import CONFIG_DIR
    s = finder.load_searches(CONFIG_DIR / "searches.yaml")
    assert "amazon_agency" in s
    for v in s.values():
        assert 30 <= v["lookback_days"] <= 120


def test_estimate_offline_makes_no_calls():
    est = finder.estimate(None, SPEC, 50)
    assert est.known_credits == 50 + 100 + 100 and est.businesses_matching is None


def test_estimate_uses_only_free_endpoints():
    t = FakeTransport()
    est = finder.estimate(ExploriumClient(t, budget=None), SPEC, 10)
    assert {c[0] for c in t.calls} == {"/v2/businesses/stats", "/v2/prospects/stats"}
    assert est.businesses_matching == 812


def test_paid_call_without_budget_refused():
    with pytest.raises(BudgetExceeded):
        ExploriumClient(FakeTransport(), budget=None).fetch_businesses({}, 10)


def test_budget_stops_before_overspend():
    t = FakeTransport()
    c = ExploriumClient(t, CreditBudget(5))
    with pytest.raises(BudgetExceeded):
        c.fetch_businesses({}, page_size=10)
    assert t.calls == []   # refused before any request went out


def test_live_run_end_to_end(setup):
    conn, t, client = setup
    r = finder.run_live(conn, client, "amazon_agency", SPEC, limit=10)
    assert r.businesses_returned == 4
    assert r.businesses_skipped == {"suppressed": 1, "no domain": 1}
    # founder picked over c-suite at the same company, one per company
    enrich_body = [b for p, b in t.calls if p.endswith("/enrich")][0]
    assert "p1" * 20 in enrich_body["prospect_ids"] and "p9" * 20 not in enrich_body["prospect_ids"]
    assert enrich_body["parameters"] == {"contact_types": ["email"]}
    imp = r.import_result
    assert imp.leads_added == 1 and imp.rejected["email invalid per source"] == 1
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert lead["email"] == "jake@acme.test" and lead["source"] == "explorium"
    assert lead["email_status_source"] == "valid"


def test_every_field_has_source_and_date(setup):
    conn, t, client = setup
    finder.run_live(conn, client, "amazon_agency", SPEC, limit=10)
    rows = conn.execute("SELECT * FROM field_values").fetchall()
    assert rows and all(r["source"] == "explorium" and r["fetched_at"] and r["source_ref"] for r in rows)
    fields = {r["field"] for r in rows if r["entity"] == "lead"}
    assert {"email", "email_status", "title", "linkedin_url", "explorium_prospect_id"} <= fields


def test_events_become_dated_signals(setup):
    conn, t, client = setup
    finder.run_live(conn, client, "amazon_agency", SPEC, limit=10)
    sig = {r["type"]: r for r in conn.execute("SELECT * FROM signals")}
    assert sig["hiring_in_sales_department"]["observed_at"].startswith("2026-10-20")
    assert "SDR" in sig["hiring_in_sales_department"]["value"]
    assert sig["new_funding_round"]["value"] == "Seed $2,000,000"
    assert sig["new_funding_round"]["company_id"] is not None


def test_second_run_skips_known_companies(setup):
    conn, t, client = setup
    finder.run_live(conn, client, "amazon_agency", SPEC, limit=10)
    r2 = finder.run_live(conn, client, "amazon_agency", SPEC, limit=10)
    assert r2.businesses_skipped.get("already in database") == 1


def test_unreported_cost_counts_at_worst_case(conn):
    c = ExploriumClient(FakeTransport(report_credits=False), CreditBudget(1000))
    c.fetch_businesses({}, page_size=10)
    assert c.budget.spent == 10 and c.budget.unknown_cost_calls == 1


def test_cli_dry_run_without_key_spends_nothing(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine.cli import app
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("EXPLORIUM_API_KEY", raising=False)
    monkeypatch.setattr("engine.paths.load_dotenv", lambda *a, **k: None)
    r = CliRunner().invoke(app, ["find", "amazon_agency", "--limit", "20"])
    assert r.exit_code == 0, r.output
    assert "DRY RUN" in r.output and "nothing was pulled or spent" in r.output


def test_cli_live_requires_cap(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine.cli import app
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    r = CliRunner().invoke(app, ["find", "amazon_agency", "--live"])
    assert r.exit_code != 0 and "max-credits" in r.output
