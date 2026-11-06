import json
import random
from datetime import date, timedelta

import pytest

from engine import suppression
from engine.paths import CONFIG_DIR
from engine.replies import classify as rc
from engine.replies import ingest as ri
from engine.replies.base import FakeReplySource
from engine.send.base import SimulatorSender
from engine.sequence import scheduler as sch

SEQ = sch.load_cfg(CONFIG_DIR)
RULES = ri.load_rules(CONFIG_DIR)
CFG = rc.load_cfg(CONFIG_DIR)
TUE = date(2026, 11, 10)
BOOK = CFG["sender"]["booking_link"]


@pytest.fixture
def world(conn, tmp_path):
    conn.execute("INSERT INTO domains (domain) VALUES ('send0.test')")
    conn.execute("INSERT INTO mailboxes (address, domain_id, warmup_started_at, daily_cap) VALUES ('abdul@send0.test',1,'2026-09-01',30)")
    for i in range(4):
        conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES (?,?,'t','x','x')",
                     (f"co{i}.test", f"Co{i} Ltd"))
        conn.execute("INSERT INTO leads (company_id, email, email_domain, first_name, last_name, title, tier, score,"
                     " niche, verify_status, country, source, imported_at, updated_at) VALUES (?,?,?,?,'Reed','Founder',"
                     "'A',90,'saas','ok','United States','t','x','x')", (i + 1, f"p{i}@co{i}.test", f"co{i}.test", f"P{i}"))
        conn.execute("INSERT INTO messages (lead_id, step, subject, body, created_at, status, review) VALUES "
                     "(?,1,'co outbound','hey P,\n\nbody\n\nabdul\nfooter','x','ready','approved')", (i + 1,))
    conn.commit()
    sch.plan_and_send(conn, SEQ, TUE, SimulatorSender(tmp_path / "out.jsonl"), random.Random(1))
    return conn


def ingest(conn, **kw):
    d = {"from": "p0@co0.test", "subject": "re: co outbound", "body": "sounds good", "received_at": "2026-11-11T15:00:00+00:00"}
    d.update(kw)
    return ri.ingest_one(conn, SEQ, RULES, next(FakeReplySource([d]).replies()))


def mid(conn, lead_id):
    return conn.execute("SELECT message_id_header FROM sends WHERE lead_id=?", (lead_id,)).fetchone()[0]


def seq_status(conn, lead_id):
    return conn.execute("SELECT status FROM sequence_state WHERE lead_id=?", (lead_id,)).fetchone()[0]


def test_match_by_thread_email_domain_and_unmatched(world):
    assert ingest(world, **{"from": "someone@else.test", "in_reply_to": mid(world, 2)}).matched_by == "thread"
    assert ingest(world, **{"from": "P0 Reed <P0@co0.test>"}).matched_by == "email"
    assert ingest(world, **{"from": "boss@co3.test"}).matched_by == "domain"
    r = ingest(world, **{"from": "random@nowhere.test"})
    assert r.status == "unmatched" and r.matched_by == "unmatched"


def test_human_reply_stops_sequence(world):
    r = ingest(world)
    assert r.status == "queued" and seq_status(world, 1) == "stopped"
    assert world.execute("SELECT outcome FROM sends WHERE lead_id=1").fetchone()[0] == "reply"


def test_duplicate_message_id_ignored(world):
    ingest(world, message_id="<a@x>")
    assert ingest(world, message_id="<a@x>").status == "duplicate"


def test_bounce_and_unsubscribe_act_before_classification(world):
    b = ingest(world, **{"from": "MAILER-DAEMON@co1.test", "subject": "Undeliverable: co outbound",
                         "in_reply_to": mid(world, 2), "body": "550 user unknown"})
    assert b.category == "auto_bounce" and suppression.check(world, "p1@co1.test")
    u = ingest(world, body="please remove me from your list\n\nOn Tue abdul wrote:\n> hey")
    assert u.category == "unsubscribe" and suppression.check(world, "p0@co0.test")
    row = dict(world.execute("SELECT * FROM replies WHERE id=?", (u.reply_id,)).fetchone())
    assert row["status"] == "classified" and row["draft_status"] == "pending"   # confirmation waits for abdul
    assert row["draft"] == CFG["replies"]["unsubscribe_confirmation"]


def test_unsubscribe_word_in_quoted_thread_is_ignored():
    r = next(FakeReplySource([{"from": "a@b.test", "body": "yes please\n\n> to unsubscribe reply stop"}]).replies())
    assert ri.instant_category(r, RULES) is None


def test_out_of_office_pauses_and_scheduler_resumes(world, tmp_path):
    r = ingest(world, subject="Automatic reply: co outbound", body="I am out of the office, back on November 20.")
    assert r.category == "out_of_office"
    st = world.execute("SELECT * FROM sequence_state WHERE lead_id=1").fetchone()
    assert st["status"] == "paused" and st["paused_until"] == "2026-11-21"
    snd = SimulatorSender(tmp_path / "o.jsonl")
    for i in range(1, 11):                                 # nothing for lead 1 while paused
        sch.plan_and_send(world, SEQ, TUE + timedelta(days=i), snd, random.Random(i))
    assert world.execute("SELECT COUNT(*) FROM sends WHERE lead_id=1").fetchone()[0] == 1
    sch.plan_and_send(world, SEQ, date(2026, 11, 23), snd, random.Random(9))   # monday after the return
    assert world.execute("SELECT COUNT(*) FROM sends WHERE lead_id=1").fetchone()[0] == 2
    assert seq_status(world, 1) == "active"


def test_ooo_without_date_uses_default():
    assert ri.return_date("I'm away with limited access to email", date(2026, 11, 11)) is None
    assert ri.return_date("back on 3rd December", date(2026, 11, 11)) == date(2026, 12, 3)
    assert ri.return_date("returning 2027-01-05", date(2026, 11, 11)) == date(2027, 1, 5)


def export(world, tmp_path):
    path, n = rc.export_batch(world, CFG, tmp_path / "ex")
    return json.loads(path.read_text()), n


def answer(tmp_path, data, answers):
    p = tmp_path / "ans.json"
    p.write_text(json.dumps({"export_id": data["export_id"], "answers": answers}))
    return p


GOOD_DRAFT = ("sure. short version: i build the system that finds the right people for Co0, writes to them from real "
              f"signals and sends it. you take the calls.\n\neasiest is 15 minutes where i map it out live. here's my "
              f"calendar: {BOOK}")


def test_export_import_round_trip_interested(world, tmp_path):
    r = ingest(world, body="this looks interesting, tell me more")
    data, n = export(world, tmp_path)
    assert n == 1 and data["replies"][0]["lead"]["company"] == "Co0" and "reply_scripts" in data
    assert data["replies"][0]["we_sent"]["step"] == 1
    out = rc.import_file(world, CFG, answer(tmp_path, data, [{"reply_id": r.reply_id, "category": "interested",
                                                                "confidence": 0.93, "reason": "asks for more",
                                                                "draft": GOOD_DRAFT}]))
    assert out == {"classified": 1, "needs_abdul": 0, "unknown": 0, "already": 0}
    box = rc.inbox(world, CFG)
    assert box[0]["urgent"] and box[0]["group"] == "urgent"
    assert rc.set_draft(world, r.reply_id, "approved").startswith("approved")
    assert world.execute("SELECT draft_status FROM replies").fetchone()[0] == "approved"
    assert world.execute("SELECT COUNT(*) FROM sends").fetchone()[0] == 4        # nothing sent by this phase


@pytest.mark.parametrize("ans,expect", [
    ({"category": "maybe_later", "confidence": 0.9, "draft": "ok"}, "not one of the 10"),
    ({"category": "interested", "confidence": 0.4, "draft": GOOD_DRAFT}, "confidence 0.4 under"),
    ({"category": "interested", "confidence": 2, "draft": GOOD_DRAFT}, "0 to 1"),
    ({"category": "interested", "confidence": 0.9, "draft": GOOD_DRAFT + " we leverage clay."}, "banned phrase"),
    ({"category": "interested", "confidence": 0.9, "draft": GOOD_DRAFT.replace("15 minutes", "we booked 300 meetings for Acme")},
     "number '300'"),
    ({"category": "interested", "confidence": 0.9, "draft": GOOD_DRAFT + " see https://evil.test/x"}, "not the booking link"),
    ({"category": "question", "confidence": 0.9, "draft": ""}, "draft missing"),
])
def test_bad_answers_go_to_abdul(world, tmp_path, ans, expect):
    r = ingest(world, body="how much is it?")
    data, _ = export(world, tmp_path)
    out = rc.import_file(world, CFG, answer(tmp_path, data, [{"reply_id": r.reply_id, "reason": "x", **ans}]))
    assert out["needs_abdul"] == 1
    row = world.execute("SELECT * FROM replies WHERE id=?", (r.reply_id,)).fetchone()
    assert any(expect in x for x in json.loads(row["check_reasons"])), row["check_reasons"]
    assert rc.set_draft(world, r.reply_id, "approved") == "only a checked draft can be approved"


def test_checks_use_stored_brief_not_the_file(world, tmp_path):
    r = ingest(world, body="what does it cost?")
    data, _ = export(world, tmp_path)
    data["replies"][0]["facts"].append({"id": "x", "text": "we booked 300 meetings"})   # edited file
    out = rc.import_file(world, CFG, answer(tmp_path, data, [{"reply_id": r.reply_id, "category": "question",
                                                               "confidence": 0.9, "reason": "price",
                                                               "draft": "we booked 300 meetings last month."}]))
    assert out["needs_abdul"] == 1


def test_referral_becomes_linked_lead_and_respects_suppression(world, tmp_path):
    r = ingest(world, body="not me. talk to Sam Lee, our head of growth, sam@co0.test")
    data, _ = export(world, tmp_path)
    rc.import_file(world, CFG, answer(tmp_path, data, [{
        "reply_id": r.reply_id, "category": "wrong_person_referral", "confidence": 0.9, "reason": "points to sam",
        "draft": "thanks for letting me know, Sam Lee sounds right. i'll keep it short with them.",
        "referral": {"name": "Sam Lee", "email": "sam@co0.test", "title": "Head of Growth"}}]))
    new = world.execute("SELECT * FROM leads WHERE email='sam@co0.test'").fetchone()
    assert new["referred_by_lead_id"] == 1 and new["source"] == "referral" and new["company_id"] == 1
    fv = world.execute("SELECT source_ref FROM field_values WHERE entity='lead' AND entity_id=?", (new["id"],)).fetchall()
    assert fv and all(x[0] == f"reply:{r.reply_id}" for x in fv)
    # a referral to a suppressed address is not added
    suppression.add(world, "email", "kim@co1.test", "existing client")
    r2 = ingest(world, **{"from": "p1@co1.test", "body": "ask Kim, kim@co1.test"})
    data2, _ = export(world, tmp_path)
    rc.import_file(world, CFG, answer(tmp_path, data2, [{
        "reply_id": r2.reply_id, "category": "wrong_person_referral", "confidence": 0.9, "reason": "kim",
        "draft": "thanks, i'll reach Kim.", "referral": {"name": "Kim", "email": "kim@co1.test"}}]))
    assert world.execute("SELECT COUNT(*) FROM leads WHERE email='kim@co1.test'").fetchone()[0] == 0


def test_made_up_referral_rejected(world, tmp_path):
    r = ingest(world, body="not my area, sorry")
    data, _ = export(world, tmp_path)
    out = rc.import_file(world, CFG, answer(tmp_path, data, [{
        "reply_id": r.reply_id, "category": "wrong_person_referral", "confidence": 0.9, "reason": "x",
        "draft": "thanks, who's the right person?", "referral": {"name": "Pat", "email": "pat@co0.test"}}]))
    assert out["needs_abdul"] == 1


def test_not_now_date_and_not_interested_suppressed(world, tmp_path):
    a = ingest(world, body="ping me in january")
    b = ingest(world, **{"from": "p1@co1.test", "body": "no thanks"})
    data, _ = export(world, tmp_path)
    rc.import_file(world, CFG, answer(tmp_path, data, [
        {"reply_id": a.reply_id, "category": "not_now", "confidence": 0.9, "reason": "january",
         "draft": "all good. i'll check back in january.", "follow_up_date": "2027-01-05"},
        {"reply_id": b.reply_id, "category": "not_interested", "confidence": 0.95, "reason": "clear no", "draft": ""}]))
    assert world.execute("SELECT follow_up_at FROM leads WHERE id=1").fetchone()[0] == "2027-01-05"
    assert "not interested" in suppression.check(world, "p1@co1.test")


def test_api_mode_uses_same_checks(world):
    from engine.personalize.llm import LLMResult
    ingest(world, body="is this written by AI?")

    class FakeClassifier:
        def json_call(self, *, role, system, user, schema):
            assert role == "classifier" and "categories" in system
            return LLMResult(data={"category": "asks_if_automated", "confidence": 0.9, "reason": "asks about ai",
                                   "draft": "yes. the research and sending run on a system i built."},
                             model="claude-haiku-5-5", input_tokens=10, output_tokens=10)
    assert rc.classify_api(world, CFG, FakeClassifier()) == {"classified": 1, "needs_abdul": 0}


def test_cli_inbox_and_add(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine.cli import app
    monkeypatch.setenv("ENGINE_DB", str(tmp_path / "t.db"))
    r = CliRunner().invoke(app, ["replies", "add", "--from", "x@nowhere.test", "--text", "who is this?"])
    assert r.exit_code == 0 and "unmatched 1" in r.output
    box = CliRunner().invoke(app, ["replies", "inbox"])
    assert "NEEDS ABDUL" in box.output and "no match" in box.output
