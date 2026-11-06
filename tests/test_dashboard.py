import json
import random
import re
from datetime import date, datetime, timedelta, timezone

from engine.paths import CONFIG_DIR
from engine.report import summary as sm
from engine.report.dashboard import build_data, write_dashboard
from engine.send.base import SimulatorSender
from engine.sequence import scheduler as sch

CFG = sch.load_cfg(CONFIG_DIR)
TUE = date(2026, 11, 10)


def seed(conn, n=4):
    conn.execute("INSERT INTO domains (domain) VALUES ('send0.test')")
    conn.execute("INSERT INTO mailboxes (address, domain_id, warmup_started_at, daily_cap) VALUES ('abdul@send0.test',1,'2026-09-01',30)")
    for i in range(n):
        conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES (?,?,'explorium','x','x')",
                     (f"co{i}.test", f"Co{i} Ltd"))
        conn.execute("INSERT INTO leads (company_id, email, email_domain, first_name, last_name, title, tier, score, niche,"
                     " verify_status, country, source, imported_at, updated_at, score_reason) VALUES (?,?,?,?,'Reed','Founder',"
                     "'A',?,'saas','ok','United States','explorium','2026-11-01','x','fit 40 + signal 30')",
                     (i + 1, f"p{i}@co{i}.test", f"co{i}.test", f"P{i}", 90 - i))
        conn.execute("INSERT INTO signals (company_id, type, signal_class, value, source, source_field, observed_at, detected_at)"
                     " VALUES (?,'hiring_in_sales_department','hiring_sales','1 open role: SDR','explorium',"
                     "'/v2/businesses/events:e1','2026-11-01','x')", (i + 1,))
        conn.execute("INSERT INTO signal_picks (lead_id, signal_id, rank, signal_class, points, age_days, saw_line,"
                     " matters_line, picked_at) VALUES (?,?,1,'hiring_sales',30,5,?,'a new hire takes a while.','x')",
                     (i + 1, i + 1, f"saw Co{i} is hiring an SDR."))
    # lead 1 and 2 approved and sent, lead 3 waiting for review, lead 4 nothing yet
    for lid, review in ((1, "approved"), (2, "approved"), (3, "pending")):
        conn.execute("INSERT INTO messages (lead_id, step, subject, body, created_at, status, review, writer_mode, variant,"
                     " qa_reasons) VALUES (?,1,'co outbound','hey P,\n\nbody','2026-11-05','ready',?,'session','v1','[]')",
                     (lid, review))
    conn.commit()
    sch.plan_and_send(conn, CFG, TUE, SimulatorSender(conn_path_dummy()), random.Random(1))


def conn_path_dummy():
    import tempfile, pathlib
    return pathlib.Path(tempfile.mkdtemp()) / "out.jsonl"


def by_id(data):
    return {r["id"]: r for r in data["prospects"]}


def test_rows_status_step_and_signal(conn):
    seed(conn)
    d = build_data(conn, CFG["sending"])
    rows = by_id(d)
    assert len(rows) == 4 and d["mode"] == "dry run"
    assert rows[1]["status"] == "in sequence" and rows[1]["step"].startswith("1 of 4, next 11-13")
    assert rows[3]["status"] == "ready for review" and rows[4]["status"] == "new"
    assert rows[1]["signal"] == "hiring_sales" and rows[1]["niche"] == "saas" and rows[1]["company"] == "Co0 Ltd"


def test_history_has_signal_source_email_sends_replies_notes(conn):
    seed(conn)
    sid = conn.execute("SELECT id FROM sends WHERE lead_id=1").fetchone()[0]
    sch.record_outcome(conn, CFG, sid, "reply")
    conn.execute("INSERT INTO replies (lead_id, send_id, received_at, body, category, confidence) VALUES "
                 "(1, ?, '2026-11-11T15:00:00+00:00', 'sure, send it', 'interested', 0.95)", (sid,))
    conn.execute("INSERT INTO notes (lead_id, text, created_at) VALUES (1, 'met at a meetup', '2026-11-11T16:00:00+00:00')")
    conn.commit()
    r = by_id(build_data(conn, CFG["sending"]))[1]
    assert r["status"] == "replied"
    assert r["pick"]["source"] == "explorium" and r["pick"]["source_field"].startswith("/v2/businesses/events")
    assert r["signals"][0]["observed_at"] == "2026-11-01"
    assert r["email1"]["review"] == "approved" and r["email1"]["writer_mode"] == "session"
    assert len(r["sends"]) == 1 and r["sends"][0]["step"] == 1 and r["sends"][0]["outcome"] == "reply"
    assert r["sends"][0]["sent_local"].startswith("2026-11-10 09") and r["sends"][0]["tz"] == "America/New_York"
    assert r["replies"][0]["category"] == "interested"
    assert r["notes"][0]["text"] == "met at a meetup"
    assert any(e["action"] == "sequence_stopped" for e in r["events"])


def test_dry_run_until_live(conn):
    seed(conn)
    assert build_data(conn, CFG["sending"])["mode"] == "dry run"
    conn.execute("INSERT INTO sends (message_id, mailbox_id, sent_at, mode, status, lead_id, step)"
                 " VALUES (1, 1, '2026-12-01T15:00:00+00:00', 'live', 'sent', 4, 1)")
    d = build_data(conn, CFG["sending"])
    assert d["mode"] == "live"
    rows = by_id(d)
    assert rows[1]["sends"] == [] and len(rows[4]["sends"]) == 1         # simulated sends hidden once live
    assert build_data(conn, CFG["sending"], include_dry_run=True)["mode"] == "live + dry run"


def test_html_is_self_contained_and_escapes_data(conn, tmp_path):
    seed(conn)
    conn.execute("UPDATE companies SET name='</script><script>alert(1)</script>' WHERE id=1")
    conn.commit()
    write_dashboard(conn, CONFIG_DIR, tmp_path / "d.html")
    h = (tmp_path / "d.html").read_text()
    assert "<script src" not in h and "<link" not in h and "@import" not in h
    # the only <script> tags are our own two, the company name cannot add a third
    assert len(re.findall(r"<script\b", h)) == 2
    blob = re.search(r'<script type="application/json" id="data">(.*?)</script>', h, re.S).group(1)
    assert "<" not in blob
    assert json.loads(blob)["prospects"][0]["company"].startswith("</script>")
    assert "DRY RUN DATA" in h
    # rendering uses textContent, never innerHTML
    assert "innerHTML" not in h
    # no outside urls anywhere outside the data blob
    outside = h.replace(blob, "")
    assert not re.search(r"https?://", outside)


def test_dashboard_in_browser_filters_and_panel(conn, tmp_path):
    import pytest
    pw = pytest.importorskip("playwright.sync_api")
    seed(conn)
    out = tmp_path / "d.html"
    write_dashboard(conn, CONFIG_DIR, out)
    import os
    exe = "/opt/pw-browsers/chromium"
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()
        except Exception as e:  # noqa: BLE001  no browser on this machine
            pytest.skip(f"no chromium available: {e}")
        pg = b.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(out.as_uri())
        assert pg.locator("#rows tr").count() == 4 and pg.text_content("#count") == "4 of 4"
        pg.select_option("#f-status", "new")
        assert pg.locator("#rows tr").count() == 1
        pg.select_option("#f-status", "")
        pg.fill("#q", "co2")
        assert pg.locator("#rows tr").count() == 1
        pg.fill("#q", "")
        pg.locator("#rows tr").first.click()
        assert "open" in pg.get_attribute("#panel", "class")
        panel = pg.text_content("#panel-body")
        assert "source explorium" in panel and "step 1" in panel and "emails sent" in panel
        b.close()
        assert errors == []


def test_since_last_time(conn, tmp_path):
    seed(conn)
    first = sm.full_summary(conn)
    assert first.startswith("since last time: first run")
    # one more lead, one more day of sends
    conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES ('new.test','New','clay','x','x')")
    conn.execute("INSERT INTO leads (company_id, email, email_domain, tier, source, imported_at, updated_at)"
                 " VALUES (5,'a@new.test','new.test','B','clay','x','x')")
    conn.commit()
    sid = conn.execute("SELECT id FROM sends LIMIT 1").fetchone()[0]
    sch.record_outcome(conn, CFG, sid, "bounce")
    peek = sm.full_summary(conn, save=False)
    assert peek.startswith("since last time (") and "new leads      +1  (clay +1)" in peek
    assert "bounces        +1" in peek and "tiers          B +1" in peek
    again = sm.full_summary(conn, save=False)          # --no-save kept the baseline
    assert "new leads      +1" in again
    sm.full_summary(conn)                              # now it moves
    assert "new leads      +0" in sm.full_summary(conn, save=False)


def test_since_text_hours_and_paused_mailbox():
    old = sm.collect.__wrapped__ if hasattr(sm.collect, "__wrapped__") else None
    base = {"leads": 1, "by_source": {}, "by_tier": {}, "email1_review": {}, "sends": 0, "sends_by_step": {},
            "outcomes": {}, "sequences": {}, "mailboxes": [{"address": "a@x.test", "paused": 0, "pause_reason": None}],
            "explorium_credits": 0, "anthropic_usd": 0.0, "alerts": [], "notes": 0}
    new = {**base, "mailboxes": [{"address": "a@x.test", "paused": 1, "pause_reason": "spam complaint"}],
           "alerts": [{"ts": "2026-11-06T10:00:00", "reason": "spam complaint"}]}
    ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(timespec="seconds")
    t = sm.since_text(new, {"ts": ts, "data": base})
    assert "5h ago" in t and "newly paused: a@x.test (spam complaint)" in t and "NEW ALERT" in t


def test_note_cli_by_id_and_email(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine.cli import app
    from engine.db import connect
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    c = connect(tmp_path / "t.db")
    c.execute("INSERT INTO leads (email, email_domain, source, imported_at, updated_at) VALUES ('jake@a.test','a.test','t','x','x')")
    c.commit()
    assert CliRunner().invoke(app, ["note", "1", "called, wants a map"]).exit_code == 0
    assert CliRunner().invoke(app, ["note", "Jake@A.test", "sent the map"]).exit_code == 0
    bad = CliRunner().invoke(app, ["note", "nobody@x.test", "x"])
    assert bad.exit_code != 0
    assert [r[0] for r in c.execute("SELECT text FROM notes ORDER BY id")] == ["called, wants a map", "sent the map"]


def test_summary_cli_writes_latest_file(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine.cli import app
    import engine.paths
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(engine.paths, "ROOT", tmp_path)
    r = CliRunner().invoke(app, ["summary"])
    assert r.exit_code == 0 and r.output.startswith("since last time: first run")
    assert (tmp_path / "data" / "summary-latest.txt").read_text().startswith("since last time")
