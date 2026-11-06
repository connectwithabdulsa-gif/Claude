from datetime import datetime, timedelta, timezone

import pytest

from engine import suppression
from engine.paths import CONFIG_DIR
from engine.score.scorer import Config, match_niche, score_all, score_lead, signal_points

TODAY = datetime(2026, 11, 6, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def cfg():
    return Config.load(CONFIG_DIR)


def lead(**kw):
    base = {"id": 1, "email": "jake@acme.test", "title": "Founder", "country": "United States",
            "verify_status": "ok", "verify_reason": ""}
    return {**base, **kw}


COMPANY = {"domain": "acme.test", "name": "Acme", "industry": "amazon agency for brands", "headcount": 11,
           "size_range": "11-50", "country": "United States"}


def sig(typ, days):
    return {"type": typ, "observed_at": (TODAY - timedelta(days=days)).isoformat()}


def test_weights_from_yaml_add_to_100(cfg):
    sc = cfg.scoring
    fit = sc["icp_fit"]["niche_strong"] + sc["icp_fit"]["size_ok"] + sc["icp_fit"]["title_top"]
    dq = sc["data_quality"]
    assert fit + max(sc["signal"]["strength_points"].values()) + dq["verify_ok"] + dq["domain_match"] \
        + dq["decision_maker"] + sc["geo"]["points"] == 100


def test_perfect_lead_is_tier_a(cfg):
    s = score_lead(cfg, lead(), COMPANY, [sig("hiring_in_sales_department", 0)], None, TODAY)
    assert s.total == 100 and s.tier == "A" and s.niche == "amazon_agency"
    assert s.reason.startswith("fit 40") and "hiring sales, 0d" in s.reason


def test_signal_decay_and_expiry(cfg):
    assert signal_points(cfg, [sig("hiring_in_sales_department", 0)], TODAY)[0] == 35
    assert signal_points(cfg, [sig("hiring_in_sales_department", 30)], TODAY)[0] == round(35 * 0.5)
    assert signal_points(cfg, [sig("hiring_in_sales_department", 31)], TODAY)[0] == 0
    assert signal_points(cfg, [{"type": "new_funding_round", "observed_at": None}], TODAY)[0] == 0
    # best single signal wins, they do not stack
    pts, why = signal_points(cfg, [sig("new_office", 1), sig("new_funding_round", 1)], TODAY)
    assert "funding" in why and pts < 35


def test_unknown_signal_type_ignored(cfg):
    assert signal_points(cfg, [sig("lawsuits_and_legal_issues", 1)], TODAY)[0] == 0


@pytest.mark.parametrize("total,tier", [(75, "A"), (74, "B"), (55, "B"), (54, "C"), (40, "C"), (39, "dnc")])
def test_tier_boundaries(cfg, total, tier):
    t = cfg.scoring["tiers"]
    got = "A" if total >= t["A"] else "B" if total >= t["B"] else "C" if total >= t["C"] else "dnc"
    assert got == tier


def test_hard_dnc_rules(cfg):
    s = lambda **kw: score_lead(cfg, lead(**kw), COMPANY, [sig("hiring_in_sales_department", 0)], None, TODAY)
    assert s(country="Germany").tier == "dnc"
    assert s(verify_status="fail", verify_reason="role mailbox").tier == "dnc"
    assert s(verify_status=None).tier == "dnc"
    assert s(title="Marketing Coordinator").tier == "dnc"
    sup = score_lead(cfg, lead(), COMPANY, [], "domain suppressed: competitor", TODAY)
    assert sup.tier == "dnc" and "competitor" in sup.reason


def test_dach_company_with_unknown_person_country(cfg):
    s = score_lead(cfg, lead(country=None), {**COMPANY, "country": "Switzerland"}, [], None, TODAY)
    assert s.tier == "dnc"


def test_niche_matching(cfg):
    assert match_niche(cfg, "mortgage broker", None)[0] == "financial"
    key, pts, _ = match_niche(cfg, "", "522310")
    assert key == "financial" and pts == cfg.scoring["icp_fit"]["niche_strong"]
    assert match_niche(cfg, "Marketing & Advertising", None)[1] == cfg.scoring["icp_fit"]["niche_weak"]
    assert match_niche(cfg, "bakery", None)[0] is None


def test_no_signal_strong_fit_lands_tier_c_or_b(cfg):
    s = score_lead(cfg, lead(verify_status="risky"), COMPANY, [], None, TODAY)
    assert s.parts["signal"] == 0 and s.tier in ("B", "C")


def test_score_all_idempotent(conn, cfg):
    conn.execute("INSERT INTO companies (domain, name, industry, headcount, source, first_seen_at, updated_at)"
                 " VALUES ('acme.test','Acme','amazon agency',11,'t','x','x')")
    conn.execute("INSERT INTO leads (company_id, email, email_domain, title, country, verify_status, source,"
                 " imported_at, updated_at) VALUES (1,'jake@acme.test','acme.test','Founder','US','ok','t','x','x')")
    conn.execute("INSERT INTO signals (company_id, type, value, source, source_field, observed_at, detected_at)"
                 " VALUES (1,'new_funding_round','Seed','t','t',?, 'x')", ((TODAY - timedelta(days=3)).isoformat(),))
    a = score_all(conn, cfg, TODAY)
    first = conn.execute("SELECT score, tier, score_reason FROM leads").fetchone()
    b = score_all(conn, cfg, TODAY)
    assert a == b and tuple(first) == tuple(conn.execute("SELECT score, tier, score_reason FROM leads").fetchone())


def test_suppression_rechecked_at_score_time(conn, cfg):
    conn.execute("INSERT INTO companies (domain, source, first_seen_at, updated_at) VALUES ('late.test','t','x','x')")
    conn.execute("INSERT INTO leads (company_id, email, email_domain, title, country, verify_status, source,"
                 " imported_at, updated_at) VALUES (1,'a@late.test','late.test','CEO','US','ok','t','x','x')")
    suppression.add(conn, "domain", "late.test", "became a client")
    score_all(conn, cfg, TODAY)
    assert conn.execute("SELECT tier FROM leads").fetchone()[0] == "dnc"


@pytest.mark.parametrize("title,dm,pts_key", [
    ("Founder & CEO", True, "title_top"),
    ("Product Owner", False, None),
    ("Vice President of Engineering", None, None),
    ("Vice President of Sales", True, "title_lead"),
    ("Microsoft Partner Manager", False, None),
    ("Sales Lead", True, "title_lead"),
    ("CRO", True, "title_lead"),
    ("Executive Assistant to the CEO", False, None),
])
def test_title_matching(cfg, title, dm, pts_key):
    from engine.score.scorer import title_points
    pts, _, is_dm = title_points(cfg, title)
    if dm is None:
        assert is_dm is False and pts == 0
    else:
        assert is_dm is dm
        assert pts == (cfg.scoring["icp_fit"][pts_key] if pts_key else 0)
