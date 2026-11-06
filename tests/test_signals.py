import json
from datetime import datetime, timedelta, timezone

import pytest

from engine.paths import CONFIG_DIR
from engine.score.scorer import Config
from engine.signals import engine as se
from engine.signals.render import a_or_an, clean_company, fill, template_vars

TODAY = datetime(2026, 11, 6, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def cfg():
    return Config.load(CONFIG_DIR)


def ago(days):
    return (TODAY - timedelta(days=days)).isoformat()


def sig(id_, typ, days, data, lead_id=None):
    return {"id": id_, "type": typ, "observed_at": ago(days) if days is not None else None,
            "data_json": json.dumps(data), "lead_id": lead_id}


LEAD = {"id": 1, "first_name": "Jake", "last_name": "Reed"}
CO = {"name": "Acme Growth Ltd"}


def test_round_wording(cfg):
    for stage, want in [("Seed", "a seed round"), ("Series A", "a Series A round"), ("Angel", "an angel round")]:
        p = se.pick_for_lead(cfg, LEAD, CO, [sig(1, "new_funding_round", 2, {"funding_stage": stage})], TODAY)[0]
        assert p.saw == f"saw Acme Growth raised {want}."


def test_helpers():
    assert clean_company("Acme Growth Ltd") == "Acme Growth"
    assert clean_company("Beta, Inc.") == "Beta"
    assert a_or_an("SDR") == "an SDR" and a_or_an("BDR") == "a BDR"
    assert a_or_an("account executive") == "an account executive" and a_or_an("growth lead") == "a growth lead"
    assert fill("saw {company} raised a {round}.", {"company": "Acme"}) is None
    assert fill(None, {}) is None


def test_every_catalog_template_only_uses_known_vars(cfg):
    known = {"company", "role_title", "a_role_title", "round", "a_round", "place", "product", "partner", "tool", "show",
             "timeframe", "first_name"}
    import string
    for key, spec in cfg.signal_classes.items():
        for f in ("saw", "saw_you", "matters", "short"):
            for _, var, _, _ in string.Formatter().parse(spec.get(f) or ""):
                assert var is None or var in known, (key, f, var)
            assert "—" not in (spec.get(f) or "")


def test_hiring_line_prefers_sales_title(cfg):
    s = sig(1, "hiring_in_sales_department", 5, {"job_titles": ["Designer", "SDR"]})
    p = se.pick_for_lead(cfg, LEAD, CO, [s], TODAY)[0]
    assert p.saw == "saw Acme Growth is hiring an SDR."
    assert p.matters.startswith("a new hire takes a while")
    assert p.age_days == 5


def test_missing_data_means_no_line(cfg):
    s = sig(1, "new_product", 3, {})   # no product_name
    assert se.pick_for_lead(cfg, LEAD, CO, [s], TODAY) == []


def test_new_leader_you_vs_company(cfg):
    mine = sig(1, "executive_joined_company", 10, {"full_name": "Jake Reed", "job_role_title": "Head of Sales"})
    other = sig(2, "executive_joined_company", 10, {"full_name": "Pat Doe", "job_role_title": "Head of Sales"})
    assert se.pick_for_lead(cfg, LEAD, CO, [mine], TODAY)[0].saw.startswith("saw you took over as head of sales")
    assert se.pick_for_lead(cfg, LEAD, CO, [other], TODAY)[0].saw.startswith("saw Acme Growth brought in a new head of sales")


def test_one_signal_by_default_best_first(cfg):
    sigs = [sig(1, "new_office", 2, {"office_location": "Austin"}),
            sig(2, "new_funding_round", 2, {"funding_stage": "Seed"}),
            sig(3, "hiring_in_sales_department", 40, {"job_titles": ["SDR"]})]   # stale, ignored
    picks = se.pick_for_lead(cfg, LEAD, CO, sigs, TODAY)
    assert len(picks) == 1 and picks[0].signal_class == "funding"
    assert picks[0].saw == "saw Acme Growth raised a seed round."


def test_two_signals_only_when_enabled_and_different(cfg):
    two = Config(cfg.scoring, cfg.niches, {**cfg.signals, "max_signals_per_email": 2})
    sigs = [sig(1, "hiring_in_sales_department", 1, {"job_titles": ["SDR"]}),
            sig(2, "hiring_in_sales_department", 2, {"job_titles": ["BDR"]}),
            sig(3, "new_funding_round", 3, {"funding_stage": "Seed"})]
    picks = se.pick_for_lead(two, LEAD, CO, sigs, TODAY)
    assert [p.signal_class for p in picks] == ["hiring_sales", "funding"]


def _seed(conn):
    conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES ('a.test','Acme Ltd','t','x','x')")
    conn.execute("INSERT INTO leads (company_id, email, email_domain, first_name, tier, source, imported_at, updated_at, raw_json)"
                 " VALUES (1,'jake@a.test','a.test','Jake','A','clay','2026-11-01T00:00:00+00:00','x', ?)",
                 (json.dumps({"Job Openings": "SDR, Designer", "Job Posted Date": "2026-10-30",
                              "Tech Stack": "HubSpot, Smartlead"}),))


def test_expire_and_undated(conn, cfg):
    _seed(conn)
    for typ, obs in [("hiring_in_sales_department", ago(5)), ("hiring_in_sales_department", ago(45)),
                     ("new_funding_round", None), ("lawsuits_and_legal_issues", ago(1))]:
        conn.execute("INSERT INTO signals (company_id, type, value, source, source_field, observed_at, detected_at)"
                     " VALUES (1,?,?,?,?,?, 'x')", (typ, "v", "t", "t", obs))
    r = se.classify_and_expire(conn, cfg, TODAY)
    assert r == {"classified": 2, "expired": 1, "undated": 1, "unknown_type": 1}
    active = conn.execute("SELECT signal_class, expires_at FROM signals WHERE active=1 AND signal_class IS NOT NULL").fetchall()
    assert len(active) == 1 and active[0][1].startswith((TODAY - timedelta(days=5) + timedelta(days=30)).date().isoformat())
    assert conn.execute("SELECT COUNT(*) FROM events WHERE action='signal_expired'").fetchone()[0] == 2


def test_detect_csv_needs_date_and_is_idempotent(conn, tmp_path):
    _seed(conn)
    conn.execute("INSERT INTO leads (company_id, email, email_domain, source, imported_at, updated_at, raw_json)"
                 " VALUES (1,'b@a.test','a.test','clay','x','x', ?)", (json.dumps({"Job Openings": "SDR"}),))
    r = se.detect_csv(conn, CONFIG_DIR / "csv_signals.yaml")
    assert r["added"] == 2 and r["skipped_no_date"] == 1
    rows = {s["type"]: dict(s) for s in conn.execute("SELECT * FROM signals")}
    assert rows["hiring_in_sales_department"]["observed_at"] == "2026-10-30"
    assert json.loads(rows["outbound_tool_in_stack"]["data_json"]) == {"tool": "Smartlead"}
    assert rows["outbound_tool_in_stack"]["source"] == "csv:clay"
    assert se.detect_csv(conn, CONFIG_DIR / "csv_signals.yaml")["added"] == 0


def test_pick_all_stores_lines_and_fallback(conn, cfg):
    _seed(conn)
    conn.execute("INSERT INTO leads (company_id, email, email_domain, tier, source, imported_at, updated_at)"
                 " VALUES (1,'dnc@a.test','a.test','dnc','t','x','x')")
    se.detect_csv(conn, CONFIG_DIR / "csv_signals.yaml")
    se.classify_and_expire(conn, cfg, TODAY)
    r = se.pick_all(conn, cfg, TODAY)
    assert r == {"with_signal": 1, "fallback": 0, "skipped_dnc": 1}
    p = conn.execute("SELECT * FROM signal_picks").fetchone()
    assert p["saw_line"] == "saw Acme is hiring an SDR." and p["rank"] == 1
    # rerun replaces, does not duplicate
    se.pick_all(conn, cfg, TODAY)
    assert conn.execute("SELECT COUNT(*) FROM signal_picks").fetchone()[0] == 1
