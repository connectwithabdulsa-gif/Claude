import random
from datetime import date

from engine.linkedin import daily
from engine.paths import CONFIG_DIR
from engine.report import periodic
from engine.send.base import SimulatorSender
from engine.sequence import scheduler as sch
import yaml

CFG = sch.load_cfg(CONFIG_DIR)
SEND = CFG["sending"]
TUE = date(2026, 11, 10)


def seed(conn, tmp_path, n=25):
    conn.execute("INSERT INTO domains (domain) VALUES ('send0.test')")
    conn.execute("INSERT INTO mailboxes (address, domain_id, warmup_started_at, daily_cap) VALUES ('abdul@send0.test',1,'2026-09-01',30)")
    for i in range(n):
        conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES (?,?,'t','x','x')",
                     (f"co{i}.test", f"Co{i} Ltd"))
        conn.execute("INSERT INTO leads (company_id, email, email_domain, first_name, last_name, title, tier, score, niche,"
                     " verify_status, country, linkedin_url, source, imported_at, updated_at) VALUES (?,?,?,?,'Reed',"
                     "'Founder',?,?,'saas','ok','United States',?,'t','x','x')",
                     (i + 1, f"p{i}@co{i}.test", f"co{i}.test", f"P{i}", "A" if i < 22 else "B", 99 - i,
                      None if i == 3 else f"linkedin.com/in/p{i}"))
        conn.execute("INSERT INTO messages (lead_id, step, subject, body, created_at, status, review, variant, signal_class)"
                     " VALUES (?,1,?,'hey','x','ready','approved',?,'hiring_sales')",
                     (i + 1, f"Co{i} outbound", "v1" if i % 2 else "v2"))
    conn.commit()


def test_linkedin_list_max_20_tier_a_with_links_and_short_notes(conn, tmp_path):
    seed(conn, tmp_path)
    out = daily.build(conn, SEND, CFG["niches"], CFG["rules"], TUE)
    assert len(out["connect"]) == 20 and out["skipped"] == {"no profile link": 1}
    assert all(x["url"].startswith("https://linkedin.com/in/") for x in out["connect"])
    assert all(len(x["note"]) <= 200 and "—" not in x["note"] for x in out["connect"])
    assert 3 + 1 not in {x["lead_id"] for x in out["connect"]}         # lead without a link skipped
    # tomorrow: the 21st+ tier A shows up, nobody is listed twice
    out2 = daily.build(conn, SEND, CFG["niches"], CFG["rules"], date(2026, 11, 11))
    assert {x["lead_id"] for x in out2["connect"]} == {22}
    md = daily.markdown(out, TUE)
    assert "send these by hand" in md and "linkedin.com/in/p0" in md


def test_linkedin_followup_after_accept_and_suppression(conn, tmp_path):
    from engine import suppression
    seed(conn, tmp_path, n=3)
    suppression.add(conn, "domain", "co2.test", "existing client")
    out = daily.build(conn, SEND, CFG["niches"], CFG["rules"], TUE)
    assert {x["lead_id"] for x in out["connect"]} == {1, 2} and out["skipped"]["suppressed"] == 1
    assert daily.mark(conn, 1, "accepted") == 1
    out2 = daily.build(conn, SEND, CFG["niches"], CFG["rules"], date(2026, 11, 11))
    assert [x["lead_id"] for x in out2["followup"]] == [1]
    assert out2["followup"][0]["note"].startswith("Thanks for connecting P0")


def test_reports_and_ab_gate(conn, tmp_path):
    seed(conn, tmp_path)
    sch.plan_and_send(conn, CFG, TUE, SimulatorSender(tmp_path / "o.jsonl"), random.Random(1))
    conn.execute("INSERT INTO replies (lead_id, received_at, category, status) VALUES (1,'2026-11-11T10:00:00+00:00','interested','classified')")
    conn.execute("UPDATE leads SET booked_at='2026-11-12T10:00:00+00:00' WHERE id=1")
    conn.commit()
    rep = periodic.build(conn, SEND, "weekly", date(2026, 11, 14))
    t = rep["totals"]
    assert t["sends"] == 25 and t["replies"] == 1 and t["positive"] == 1 and t["booked"] == 1
    subj = {g["k"] for g in rep["splits"]["subject"]}
    assert subj == {"{company} outbound"}                    # wording, not company names
    assert "not enough data" in rep["ab"]["variant"] and "no change yet" in rep["ab"]["suggestion"]
    day = periodic.build(conn, SEND, "daily", date(2026, 11, 13))
    assert day["totals"]["sends"] == 0
    assert "weekly report" in periodic.text(rep)


def test_ab_suggests_one_change_once_enough_data():
    splits = {"variant": [{"k": "v1", "sends": 400, "positive_pct": 2.0, "reply_pct": 5.0},
                          {"k": "v2", "sends": 350, "positive_pct": 0.5, "reply_pct": 3.0}],
              "subject": [{"k": "a", "sends": 900, "positive_pct": 1.0, "reply_pct": 4.0},
                          {"k": "b", "sends": 300, "positive_pct": 0.1, "reply_pct": 1.0}]}
    ab = periodic.ab_readout(splits, 300)
    assert ab["variant"]["best"] == "v1" and ab["variant"]["worst"] == "v2"
    assert ab["suggestion"].startswith("change one thing: stop variant 'v2'")    # exactly one suggestion
