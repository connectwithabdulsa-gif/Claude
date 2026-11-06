import json
from datetime import datetime, timezone

import pytest

from engine.paths import CONFIG_DIR
from engine.personalize import session
from engine.personalize import writer as w

TODAY = datetime(2026, 11, 6, tzinfo=timezone.utc)
CFG = w.load_cfg(CONFIG_DIR)
WHO = ("i'm abdul. i build outbound systems in clay. it finds the right people, checks the data, writes to them "
       "and sends it, so you only take the calls.")
SIGNAL_BODY = (f"hey Jake,\n\nsaw Acme is hiring an SDR. a new hire takes a while to ramp before meetings show up.\n\n"
               f"{WHO}\n\nwant me to send a one page map of how it would run for Acme?")
FALLBACK_BODY = (f"hey Jake,\n\nif most of Acme's clients come from referrals, outbound can sit next to that and fill "
                 f"the gaps.\n\n{WHO}\n\nwant me to send a one page map of how it would run for Acme?")


def seed(conn, n=1, tier="A", with_signal=True):
    conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES ('acme.test','Acme Ltd','explorium','x','x')")
    for i in range(n):
        conn.execute("INSERT INTO leads (company_id, email, email_domain, first_name, title, tier, score, niche,"
                     " verify_status, source, imported_at, updated_at) VALUES (1,?,'acme.test','Jake','Founder',?,90,"
                     "'saas','ok','explorium','x','x')", (f"j{i}@acme.test", tier))
        if with_signal:
            conn.execute("INSERT INTO signals (company_id, type, value, source, source_field, observed_at, detected_at,"
                         " data_json) VALUES (1,'hiring_in_sales_department','v','explorium',"
                         "'/v2/businesses/events:e1','2026-11-01','x','{\"job_titles\": [\"SDR\"]}')")
            conn.execute("INSERT INTO signal_picks (lead_id, signal_id, rank, signal_class, points, age_days, saw_line,"
                         " matters_line, picked_at) VALUES (?,?,1,'hiring_sales',30,5,'saw Acme is hiring an SDR.',"
                         "'a new hire takes a while to ramp before meetings show up.','x')", (i + 1, i + 1))
    conn.commit()


def do_export(conn, tmp_path):
    leads = w.eligible_leads(conn, ["A", "B", "C"], 10)
    path, n = session.export_batch(conn, leads, CFG["niches"], tmp_path, TODAY)
    return json.loads(path.read_text()), n


def answer(tmp_path, export, emails):
    p = tmp_path / "answer.json"
    p.write_text(json.dumps({"export_id": export["export_id"], "emails": emails}))
    return p


def em(body, used, subject="acme outbound"):
    return {"subject": subject, "body": body, "facts_used": used}


def test_export_has_same_facts_as_api_writer(conn, tmp_path):
    seed(conn)
    data, n = do_export(conn, tmp_path)
    assert n == 1 and data["export_id"].startswith("exp-")
    lead = data["leads"][0]
    assert lead["needs"] == ["signal_email", "fallback_email"] and lead["variant"] == "v1"
    sig = next(f for f in lead["facts"] if f["id"] == "signal")
    assert sig["source"] == "explorium" and sig["date"] == "2026-11-01"
    api_ctx = w.build_context(conn, dict(conn.execute("SELECT * FROM leads").fetchone()), CFG["niches"], TODAY)
    assert lead["facts"] == api_ctx["facts"]
    assert "how_to_write" in data and "answer_format" in data
    # a second export does not hand out the same lead again
    assert do_export(conn, tmp_path)[1] == 0


def test_export_rotates_variants_within_a_batch(conn, tmp_path):
    seed(conn, 3)
    data, _ = do_export(conn, tmp_path)
    assert [l["variant"] for l in data["leads"]] == ["v1", "v2", "v3"]


def test_tier_c_and_no_signal_only_need_fallback(conn, tmp_path):
    seed(conn, 1, tier="C")
    data, _ = do_export(conn, tmp_path)
    assert data["leads"][0]["needs"] == ["fallback_email"] and data["leads"][0]["signal"] is None
    assert all(f["id"] != "signal" for f in data["leads"][0]["facts"])


def test_import_good_signal_email(conn, tmp_path):
    seed(conn)
    data, _ = do_export(conn, tmp_path)
    p = answer(tmp_path, data, [{"lead_id": 1, "signal_email": em(SIGNAL_BODY, ["first_name", "company", "signal"]),
                                 "fallback_email": em(FALLBACK_BODY, ["first_name", "company"])}])
    r = session.import_file(conn, p, CFG)
    assert r["ready"] == 1 and r["fallback"] == 0
    m = conn.execute("SELECT * FROM messages").fetchone()
    assert m["review"] == "pending" and m["writer_mode"] == "session" and m["cost_usd"] == 0
    assert m["body"].endswith("not for you? just say so and i'll stop.") and m["signal_class"] == "hiring_sales"
    # importing the same file twice does nothing
    assert session.import_file(conn, p, CFG)["already_imported"] == 1


def test_import_rejects_fact_we_did_not_provide(conn, tmp_path):
    seed(conn)
    data, _ = do_export(conn, tmp_path)
    p = answer(tmp_path, data, [{"lead_id": 1,
                                 "signal_email": em(SIGNAL_BODY, ["company", "signal", "revenue_2025"]),
                                 "fallback_email": em(FALLBACK_BODY, ["company", "made_up_case_study"])}])
    r = session.import_file(conn, p, CFG)
    assert r["held"] == 1
    reasons = json.loads(conn.execute("SELECT qa_reasons FROM messages").fetchone()[0])
    assert any("revenue_2025" in x for x in reasons) and any("made_up_case_study" in x for x in reasons)


def test_import_checks_against_db_not_edited_file(conn, tmp_path):
    seed(conn)
    data, _ = do_export(conn, tmp_path)
    # someone adds a fact to the export file and cites it: still rejected
    data["leads"][0]["facts"].append({"id": "big_claim", "text": "300 meetings booked"})
    p = answer(tmp_path, data, [{"lead_id": 1, "signal_email": em(SIGNAL_BODY, ["company", "signal", "big_claim"]),
                                 "fallback_email": None}])
    assert session.import_file(conn, p, CFG)["held"] == 1


def test_bad_signal_email_falls_back(conn, tmp_path):
    seed(conn)
    data, _ = do_export(conn, tmp_path)
    bad = SIGNAL_BODY.replace("i build", "we leverage clay to build")
    p = answer(tmp_path, data, [{"lead_id": 1, "signal_email": em(bad, ["company", "signal"]),
                                 "fallback_email": em(FALLBACK_BODY, ["first_name", "company"])}])
    r = session.import_file(conn, p, CFG)
    assert r["ready"] == 1 and r["fallback"] == 1
    m = conn.execute("SELECT * FROM messages").fetchone()
    assert m["signal_class"] == "fallback" and "leverage" in m["qa_reasons"] and m["attempts"] == 2
    assert "referrals" in m["body"]


def test_both_fail_are_held_and_cannot_be_approved(conn, tmp_path):
    seed(conn)
    data, _ = do_export(conn, tmp_path)
    p = answer(tmp_path, data, [{"lead_id": 1, "signal_email": em(SIGNAL_BODY + "!", ["company", "signal"]),
                                 "fallback_email": em("too short?", ["company"])}])
    assert session.import_file(conn, p, CFG)["held"] == 1
    mid = conn.execute("SELECT id FROM messages").fetchone()[0]
    assert session.set_review(conn, [mid], "approved") == 0
    assert conn.execute("SELECT review FROM messages").fetchone()[0] == "held"


def test_unknown_lead_in_answer_skipped(conn, tmp_path):
    seed(conn)
    data, _ = do_export(conn, tmp_path)
    p = answer(tmp_path, data, [{"lead_id": 999, "fallback_email": em(FALLBACK_BODY, ["company"])}])
    assert session.import_file(conn, p, CFG)["unknown_lead"] == 1


def test_review_approve_and_hold(conn, tmp_path):
    seed(conn, 2)
    data, _ = do_export(conn, tmp_path)
    p = answer(tmp_path, data, [
        {"lead_id": i, "signal_email": em(SIGNAL_BODY, ["company", "signal"]), "fallback_email": None} for i in (1, 2)])
    session.import_file(conn, p, CFG)
    assert len(session.review_rows(conn)) == 2
    assert session.set_review(conn, [1], "approved") == 1
    assert session.set_review(conn, [2], "held", "tone is off") == 1
    assert session.review_rows(conn) == []
    assert conn.execute("SELECT review_note FROM messages WHERE id=2").fetchone()[0] == "tone is off"


def test_cli_session_default_exports_without_api(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine.cli import app
    from engine.db import connect
    import engine.personalize.llm as llm_mod
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    called = []
    monkeypatch.setattr(llm_mod.AnthropicLLM, "__init__", lambda self, *a, **k: called.append(1))
    seed(connect(tmp_path / "t.db"))
    r = CliRunner().invoke(app, ["write", "--out-dir", str(tmp_path / "ex")])
    assert r.exit_code == 0, r.output
    assert "exported 1 leads" in r.output and called == []
    assert len(list((tmp_path / "ex").glob("exp-*.json"))) == 1
