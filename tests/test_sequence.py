import json
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from engine import suppression
from engine.paths import CONFIG_DIR
from engine.send.base import SimulatorSender
from engine.sequence import scheduler as sch

CFG = sch.load_cfg(CONFIG_DIR)
TUE = date(2026, 11, 10)          # a tuesday
BODY = "hey X,\n\nemail one body.\n\nabdul"


def seed(conn, n=10, companies=None, mailboxes=2, review="approved", country="United States", warm_from="2026-09-01"):
    for i in range(mailboxes):
        conn.execute("INSERT INTO domains (domain) VALUES (?)", (f"send{i}.test",))
        conn.execute("INSERT INTO mailboxes (address, domain_id, warmup_started_at, daily_cap) VALUES (?,?,?,30)",
                     (f"abdul@send{i}.test", i + 1, warm_from))
    companies = companies or n
    for c in range(companies):
        conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES (?,?,'t','x','x')",
                     (f"co{c}.test", f"Co{c} Ltd"))
    for i in range(n):
        cid = i % companies + 1
        conn.execute("INSERT INTO leads (company_id, email, email_domain, first_name, tier, score, niche, verify_status,"
                     " country, source, imported_at, updated_at) VALUES (?,?,?,?,?,?,'saas','ok',?,'t','x','x')",
                     (cid, f"p{i}@co{cid - 1}.test", f"co{cid - 1}.test", f"P{i}", "A" if i % 3 == 0 else "B",
                      90 - i, country))
        conn.execute("INSERT INTO messages (lead_id, step, subject, body, created_at, status, review)"
                     " VALUES (?,1,'co outbound',?,'x','ready',?)", (i + 1, BODY, review))
    conn.commit()


def run(conn, day, tmp_path, warm=False, seed_=1):
    return sch.plan_and_send(conn, CFG, day, SimulatorSender(tmp_path / "out.jsonl"), random.Random(seed_), warm)


def test_ramp_and_warmup():
    ramp = CFG["sending"]["ramp"]
    mb = {"paused": 0, "warmup_started_at": "2026-10-01", "live_from": None, "daily_cap": 30}
    assert sch.mailbox_cap(mb, date(2026, 10, 15), ramp) == 0            # still warming
    assert sch.mailbox_cap(mb, date(2026, 10, 22), ramp) == 10           # live day 1
    assert sch.mailbox_cap(mb, date(2026, 10, 29), ramp) == 15           # +5 a week
    assert sch.mailbox_cap(mb, date(2027, 3, 1), ramp) == 30             # capped
    assert sch.mailbox_cap({**mb, "paused": 1}, date(2027, 3, 1), ramp) == 0


def test_caps_respected_and_spread(conn, tmp_path):
    seed(conn, n=80)
    p = run(conn, TUE, tmp_path)
    for mb, n in p.sent.items():
        assert n <= p.caps[mb]
    assert sum(p.sent.values()) == sum(p.caps.values())
    assert p.skipped.get("daily caps reached", 0) > 0


def test_sends_inside_recipient_hours_with_jitter(conn, tmp_path):
    seed(conn, n=20, country="Australia")
    run(conn, TUE, tmp_path)
    tz = ZoneInfo("Australia/Sydney")
    rows = conn.execute("SELECT mailbox_id, sent_at FROM sends ORDER BY mailbox_id, sent_at").fetchall()
    last = {}
    for r in rows:
        t = datetime.fromisoformat(r["sent_at"])
        loc = t.astimezone(tz)
        assert 9 <= loc.hour < 17
        if r["mailbox_id"] in last:
            gap = (t - last[r["mailbox_id"]]).total_seconds() / 60
            assert 3 <= gap <= 8.01
        last[r["mailbox_id"]] = t


def test_new_sequences_only_tue_to_thu(conn, tmp_path):
    seed(conn, n=5)
    mon = TUE - timedelta(days=1)
    assert sum(run(conn, mon, tmp_path).sent.values()) == 0
    assert sum(run(conn, TUE, tmp_path).sent.values()) == 5


def test_unapproved_or_held_never_scheduled(conn, tmp_path):
    seed(conn, n=3, review="pending")
    assert sum(run(conn, TUE, tmp_path).sent.values()) == 0


def test_one_person_per_company(conn, tmp_path):
    seed(conn, n=6, companies=2)
    p = run(conn, TUE, tmp_path)
    assert sum(p.sent.values()) == 2 and p.skipped["another person at this company is in sequence"] == 4


def test_full_sequence_days_gaps_threads(conn, tmp_path):
    seed(conn, n=1, mailboxes=2)
    for i in range(20):
        run(conn, TUE + timedelta(days=i), tmp_path, seed_=i)
    sends = [dict(r) for r in conn.execute("SELECT * FROM sends ORDER BY id")]
    assert [s["step"] for s in sends] == [1, 2, 3, 4]
    days = [date.fromisoformat(s["sent_at"][:10]) for s in sends]
    assert [(d - days[0]).days for d in days] == [0, 3, 7, 14]
    assert len({s["mailbox_id"] for s in sends}) == 1                  # sticky mailbox
    h = [json.loads(s["headers_json"]) for s in sends]
    assert h[1]["In-Reply-To"] == h[0]["Message-ID"] == h[2]["In-Reply-To"]   # same thread, no fake re:
    assert "In-Reply-To" not in h[3]                                    # email 4 is a new thread
    assert all("List-Unsubscribe" in x for x in h)
    subjects = [conn.execute("SELECT subject FROM messages WHERE id=?", (s["message_id"],)).fetchone()[0] for s in sends]
    assert subjects[0] == subjects[1] == subjects[2] and subjects[3] == "Co0 pipeline"
    assert conn.execute("SELECT status FROM sequence_state").fetchone()[0] == "completed"
    bodies = [conn.execute("SELECT body FROM messages WHERE id=?", (s["message_id"],)).fetchone()[0] for s in sends[1:]]
    assert all("not for you? just say so and i'll stop." in b for b in bodies)


def test_weekend_followup_slides_and_keeps_min_gap(conn, tmp_path):
    seed(conn, n=1)
    thu = TUE + timedelta(days=2)
    run(conn, thu, tmp_path)                    # email 1 thursday, email 2 due sunday
    for i in range(1, 6):
        run(conn, thu + timedelta(days=i), tmp_path)
    d = [date.fromisoformat(r[0][:10]) for r in conn.execute("SELECT sent_at FROM sends ORDER BY id")]
    assert (d[1] - d[0]).days >= 3 and d[1].weekday() < 5


def test_reply_and_bounce_stop_and_suppress(conn, tmp_path):
    seed(conn, n=2)
    run(conn, TUE, tmp_path)
    s1, s2 = [r[0] for r in conn.execute("SELECT id FROM sends ORDER BY id")]
    sch.record_outcome(conn, CFG, s1, "reply")
    sch.record_outcome(conn, CFG, s2, "bounce")
    st = {r["lead_id"]: r["status"] for r in conn.execute("SELECT * FROM sequence_state")}
    assert st == {1: "stopped", 2: "stopped"}
    assert suppression.check(conn, "p1@co1.test") and not suppression.check(conn, "p0@co0.test")
    for i in range(1, 10):
        run(conn, TUE + timedelta(days=i), tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM sends").fetchone()[0] == 2   # no follow ups after a stop


def test_suppressed_after_start_is_stopped_at_send_time(conn, tmp_path):
    seed(conn, n=1)
    run(conn, TUE, tmp_path)
    suppression.add(conn, "domain", "co0.test", "became a client")
    p = run(conn, TUE + timedelta(days=3), tmp_path)
    assert p.skipped == {"suppressed at send time": 1}
    assert conn.execute("SELECT status FROM sequence_state").fetchone()[0] == "stopped"


def test_complaint_pauses_mailbox(conn, tmp_path):
    seed(conn, n=4, mailboxes=1)
    run(conn, TUE, tmp_path)
    sid = conn.execute("SELECT id FROM sends LIMIT 1").fetchone()[0]
    alerts = sch.record_outcome(conn, CFG, sid, "complaint")
    assert alerts and "spam complaint" in alerts[0]
    assert conn.execute("SELECT paused FROM mailboxes").fetchone()[0] == 1
    assert sum(run(conn, TUE + timedelta(days=1), tmp_path).sent.values()) == 0


def test_bounce_rate_pause_needs_min_sample(conn, tmp_path):
    seed(conn, n=40, mailboxes=1)
    for i in range(3):
        run(conn, TUE + timedelta(days=i), tmp_path)
    ids = [r[0] for r in conn.execute("SELECT id FROM sends ORDER BY id")]
    assert len(ids) >= 20
    assert sch.record_outcome(conn, CFG, ids[0], "bounce") == []             # 1 of 30: under 3%
    alerts = sch.record_outcome(conn, CFG, ids[1], "bounce")                 # 2 of 30: over 3%
    assert alerts and "bounce rate" in alerts[0]


def test_simulator_writes_outbox(conn, tmp_path):
    seed(conn, n=2)
    run(conn, TUE, tmp_path)
    lines = (tmp_path / "out.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["provider_id"].startswith("sim-")
    assert {r[0] for r in conn.execute("SELECT mode FROM sends")} == {"dry_run"}


def test_followup_templates_pass_rules():
    from engine.personalize import qa
    from engine.sequence import templates
    for step in (2, 3, 4):
        subj, body = templates.render(step, {"first_name": "Jake"}, {"name": "Acme Ltd"},
                                      {"target_description": "amazon brands that fit your clients"})
        assert qa.check_followup(body, subj, CFG["rules"]) == [], step
        assert "Acme" in body and "—" not in body


def test_main_domain_mailbox_refused(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine.cli import app
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    r = CliRunner().invoke(app, ["mailboxes", "add", "abdul@dfygtmsystems.com", "--warmup-start", "2026-10-01"])
    assert r.exit_code != 0 and "secondary" in r.output
    ok = CliRunner().invoke(app, ["mailboxes", "add", "abdul@getdfy.test", "--warmup-start", "2026-10-01"])
    assert ok.exit_code == 0, ok.output


def test_summary_and_dashboard(conn, tmp_path):
    from engine.report.summary import collect, dashboard_html, text
    seed(conn, n=3)
    run(conn, TUE, tmp_path)
    s = collect(conn)
    assert s["sends"] == 3 and s["sequences"]["active"] == 3
    t = text(s)
    assert "sends        3" in t and "bounce 0.0%" in t
    h = dashboard_html(s)
    assert h.startswith("<!doctype html>") and "prefers-color-scheme: dark" in h and "abdul@send0.test" in h


def test_mixed_timezones_fill_the_cap(conn, tmp_path):
    seed(conn, n=30, mailboxes=1)
    for i, c in enumerate(["United States", "Australia", "United Kingdom"] * 10):
        conn.execute("UPDATE leads SET country=? WHERE id=?", (c, i + 1))
    p = run(conn, TUE, tmp_path)
    assert sum(p.sent.values()) == 30 and "outside recipient business hours today" not in p.skipped
