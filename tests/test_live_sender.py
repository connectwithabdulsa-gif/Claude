"""phase 8: Spacemail sender, IMAP reply reader, runner gates. fake SMTP and IMAP, no network."""
import json
import random
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from engine import suppression
from engine.paths import CONFIG_DIR
from engine.replies import imap as rimap
from engine.send import dns_check, runner
from engine.send import spacemail as sm
from engine.sequence import scheduler as sch

TUE = date(2026, 11, 10)
AT = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)          # 10:00 in New York
MB = "abdul@send0.test"


class FakeSMTP:
    sent: list = []

    def __init__(self, mode="ok"):
        self.mode = mode

    def __call__(self, host, port, timeout):
        return self

    def login(self, user, pw):
        if self.mode == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad login")

    def send_message(self, msg):
        if self.mode == "reject":
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        if self.mode == "temp":
            raise smtplib.SMTPServerDisconnected("gone")
        FakeSMTP.sent.append(msg)
        return {}

    def quit(self):
        pass


class FakeIMAP:
    def __init__(self, messages: dict | None = None):
        self.messages = messages or {}            # uid -> raw bytes
        self.fetched = []

    def __call__(self, host, port):
        return self

    def login(self, u, p):
        pass

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Junk) "/" "Spam"']

    def select(self, folder, readonly=True):
        self.folder = folder
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code):
        return "UIDVALIDITY", [b"1"]

    def uid(self, cmd, *args):
        msgs = self.messages if self.folder == "INBOX" else {}
        if cmd == "SEARCH":
            lo = int(args[1].split()[1].split(":")[0])
            return "OK", [" ".join(str(u) for u in sorted(msgs) if u >= lo).encode()]
        uid = int(args[0])
        self.fetched.append(uid)
        return "OK", [(b"x", msgs[uid])]

    def logout(self):
        pass


def raw_reply(frm, to, in_reply_to, body, mid="<r1@co0.test>"):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, "Re: co outbound"
    m["Message-ID"], m["In-Reply-To"], m["References"] = mid, in_reply_to, in_reply_to
    m["Date"] = "Tue, 10 Nov 2026 16:00:00 +0000"
    m.set_content(body)
    return m.as_bytes()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = sch.load_cfg(CONFIG_DIR)
    c = json.loads(json.dumps(c))
    lv = c["sending"]["live"]
    for k in ("ok_file", "stop_file", "lock_file"):
        lv[k] = str(tmp_path / k)
    (tmp_path / "ok_file").write_text("ok")
    c["sender"]["physical_address"] = "1 Test St, Testville"
    monkeypatch.setenv(sm.env_key(MB), "pw")
    FakeSMTP.sent = []
    return c


def seed(conn, n=2):
    conn.execute("INSERT INTO domains (domain) VALUES ('send0.test')")
    conn.execute("INSERT INTO mailboxes (address, domain_id, daily_cap, live_from) VALUES (?,1,30,?)",
                 (MB, TUE.isoformat()))
    dns_check.save(conn, dns_check.DomainReport("send0.test", {k: ("ok", "") for k in
                                                              ("mx", "spf", "dkim", "dmarc", "blocklists")}))
    conn.execute("UPDATE domains SET checked_at=? WHERE domain='send0.test'", (AT.isoformat(),))
    for i in range(n):
        conn.execute("INSERT INTO companies (domain, name, source, first_seen_at, updated_at) VALUES (?,?,'t','x','x')",
                     (f"co{i}.test", f"Co{i}"))
        conn.execute("INSERT INTO leads (company_id, email, email_domain, first_name, tier, score, niche, verify_status,"
                     " country, source, imported_at, updated_at) VALUES (?,?,?,?,'A',90,'saas','ok','United States',"
                     "'t','x','x')", (i + 1, f"p{i}@co{i}.test", f"co{i}.test", f"P{i}"))
        conn.execute("INSERT INTO messages (lead_id, step, subject, body, created_at, status, review)"
                     " VALUES (?,1,'Co outbound','Hi P.\n\nBody.\n\nAbdul','x','ready','approved')", (i + 1,))
    conn.commit()


def tick(conn, cfg, at=AT, smtp="ok", imap=None):
    return runner.tick(conn, cfg, at, random.Random(1), smtp_factory=FakeSMTP(smtp), imap_factory=imap or FakeIMAP())


# gates -------------------------------------------------------------------------------------------

def test_global_gates_each_block(conn, cfg, tmp_path):
    seed(conn)
    assert runner.global_gates(cfg) == []
    (tmp_path / "ok_file").unlink()
    assert any("not approved" in g for g in runner.global_gates(cfg))
    (tmp_path / "ok_file").write_text("ok")
    (tmp_path / "stop_file").write_text("x")
    assert any("kill switch" in g for g in runner.global_gates(cfg))
    (tmp_path / "stop_file").unlink()
    cfg["sender"]["physical_address"] = "[ADD PHYSICAL ADDRESS]"
    res = tick(conn, cfg)
    assert res.blocked and res.sent == 0 and not FakeSMTP.sent
    assert conn.execute("SELECT COUNT(*) FROM sends").fetchone()[0] == 0


def test_mailbox_gates(conn, cfg, monkeypatch):
    seed(conn)
    mb = dict(conn.execute("SELECT * FROM mailboxes").fetchone())
    assert runner.mailbox_gate(conn, cfg, mb, AT) is None
    assert "main domain" in runner.mailbox_gate(conn, cfg, {**mb, "address": "sami@dfygtmsystems.com"}, AT)
    assert "old" in runner.mailbox_gate(conn, cfg, mb, AT + timedelta(hours=30))
    monkeypatch.delenv(sm.env_key(MB))
    assert "no password" in runner.mailbox_gate(conn, cfg, mb, AT)
    conn.execute("UPDATE domains SET dns_report=? ", (json.dumps({"ok": False}),))
    monkeypatch.setenv(sm.env_key(MB), "pw")
    assert "DNS check failed" in runner.mailbox_gate(conn, cfg, mb, AT)


# sending -----------------------------------------------------------------------------------------

def test_tick_queues_day_then_sends_one_per_tick_with_headers(conn, cfg):
    seed(conn)
    r1 = tick(conn, cfg)
    assert r1.planned == 2 and r1.sent == 1 and r1.waiting == 1
    msg = FakeSMTP.sent[0]
    assert msg["From"] == f"Abdul <{MB}>" and msg["Message-ID"].endswith("@send0.test>")
    assert msg["List-Unsubscribe"].startswith(f"<mailto:{MB}") and msg["List-Unsubscribe-Post"]
    assert msg.get_content_type() == "text/plain"
    r2 = tick(conn, cfg, AT + timedelta(minutes=5))
    assert r2.planned is None and r2.sent == 1               # the day is planned only once
    rows = conn.execute("SELECT status, sent_at, provider_id, message_id_header FROM sends").fetchall()
    assert all(r["status"] == "sent" and r["sent_at"] and r["provider_id"] == r["message_id_header"] for r in rows)


def test_outside_business_hours_waits(conn, cfg):
    seed(conn)
    early = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)       # 22:00 the night before in New York
    r = tick(conn, cfg, early)
    assert r.sent == 0 and not FakeSMTP.sent


def test_hard_refusal_is_a_bounce_and_suppresses(conn, cfg):
    seed(conn, 1)
    r = tick(conn, cfg, smtp="reject")
    assert r.failed == 1 and r.sent == 0
    s = conn.execute("SELECT status, outcome FROM sends").fetchone()
    assert s["status"] == "failed" and s["outcome"] == "bounce"
    assert suppression.check(conn, "p0@co0.test")


def test_temporary_failure_stays_queued(conn, cfg):
    seed(conn, 1)
    r = tick(conn, cfg, smtp="temp")
    s = conn.execute("SELECT status, attempts FROM sends").fetchone()
    assert r.sent == 0 and s["status"] == "queued" and s["attempts"] == 1


def test_login_refused_pauses_mailbox(conn, cfg):
    seed(conn, 1)
    r = tick(conn, cfg, smtp="auth")
    mb = conn.execute("SELECT paused, pause_reason FROM mailboxes").fetchone()
    assert mb["paused"] == 1 and "login refused" in mb["pause_reason"] and r.sent == 0
    assert conn.execute("SELECT status FROM sends").fetchone()[0] == "queued"


def test_suppressed_after_planning_is_cancelled(conn, cfg):
    seed(conn, 1)
    tick(conn, cfg, datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc))      # planned, not sent (night)
    suppression.add(conn, "domain", "co0.test", "competitor")
    r = tick(conn, cfg)
    assert r.cancelled == 1 and not FakeSMTP.sent


# replies -----------------------------------------------------------------------------------------

def test_imap_reply_stops_sequence_and_is_read_once(conn, cfg):
    seed(conn, 1)
    tick(conn, cfg)
    mid = conn.execute("SELECT message_id_header FROM sends").fetchone()[0]
    imap = FakeIMAP({1: raw_reply("p0@co0.test", MB, mid, "Sounds good, send the 20.\n\n> old"),
                     2: raw_reply(MB, "x@y.test", mid, "our own copy", "<own@send0.test>")})
    r = tick(conn, cfg, AT + timedelta(minutes=5), imap=imap)
    assert r.replies == {"queued": 1}
    assert any("new reply from p0@co0.test" in a for a in r.alerts)
    assert conn.execute("SELECT status FROM sequence_state").fetchone()[0] == "stopped"
    rep = conn.execute("SELECT matched_by, lead_id FROM replies").fetchone()
    assert rep["matched_by"] == "thread" and rep["lead_id"] == 1
    imap.fetched.clear()
    r2 = tick(conn, cfg, AT + timedelta(minutes=10), imap=imap)
    assert r2.replies == {} and imap.fetched == []
    assert conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0] == 1


def test_imap_bounce_message_records_bounce(conn, cfg):
    seed(conn, 1)
    tick(conn, cfg)
    mid = conn.execute("SELECT message_id_header FROM sends").fetchone()[0]
    imap = FakeIMAP({1: raw_reply("mailer-daemon@mail.spacemail.com", MB, mid, "Delivery failed: no such user")})
    tick(conn, cfg, AT + timedelta(minutes=5), imap=imap)
    assert conn.execute("SELECT outcome FROM sends").fetchone()[0] == "bounce"
    assert suppression.check(conn, "p0@co0.test")


def test_parse_plain_and_html():
    r = rimap.parse(raw_reply("A <a@x.test>", MB, "<m@send0.test>", "Hello"))
    assert r.from_address == "a@x.test" and r.in_reply_to == "<m@send0.test>" and "Hello" in r.body


# dns + lock + creds ------------------------------------------------------------------------------

def fake_dns(records):
    def lookup(name, rtype):
        return records.get((name, rtype))
    return lookup


def test_dns_pass_and_fail():
    good = {("a.test", "MX"): ["10 mx1.spacemail.com."],
            ("a.test", "TXT"): ["v=spf1 include:spf.spacemail.com ~all"],
            ("spacemail._domainkey.a.test", "TXT"): ["v=DKIM1; k=rsa; p=MIIB"],
            ("_dmarc.a.test", "TXT"): ["v=DMARC1; p=none"]}
    assert dns_check.check_domain("a.test", fake_dns(good)).ok
    bad = dict(good)
    bad[("a.test", "TXT")] = ["v=spf1 include:_spf.google.com ~all"]
    rep = dns_check.check_domain("a.test", fake_dns(bad))
    assert not rep.ok and rep.checks["spf"][0] == "fail"
    listed = dict(good)
    listed[("a.test.dbl.spamhaus.org", "A")] = ["127.0.1.2"]
    assert not dns_check.check_domain("a.test", fake_dns(listed)).ok

    def down(name, rtype):
        raise dns_check.DnsUnavailable("timeout")
    assert not dns_check.check_domain("a.test", down).ok       # unknown never passes


def test_lock(tmp_path):
    a, b = runner.RunLock(tmp_path / "l"), runner.RunLock(tmp_path / "l")
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_password_only_from_env(monkeypatch):
    monkeypatch.delenv("SPACEMAIL_USER", raising=False)
    assert sm.env_key("abdul@try-dfy.com") == "SPACEMAIL_PASSWORD_ABDUL_TRY_DFY_COM"
    with pytest.raises(sm.MissingPassword):
        sm.password_for("nobody@x.test")
    monkeypatch.setenv("SPACEMAIL_USER", "nobody@x.test")
    monkeypatch.setenv("SPACEMAIL_PASSWORD", "s")
    assert sm.password_for("nobody@x.test") == "s"


def test_main_domain_override_only_that_address(conn, cfg):
    seed(conn)
    mb = dict(conn.execute("SELECT * FROM mailboxes").fetchone())
    cfg["sender"]["main_domain_override"] = {"address": "abdul@dfygtmsystems.com", "max_per_day": 10}
    assert "main domain" in runner.mailbox_gate(conn, cfg, {**mb, "address": "sami@dfygtmsystems.com"}, AT)
    why = runner.mailbox_gate(conn, cfg, {**mb, "address": "abdul@dfygtmsystems.com"}, AT)
    assert why is None or "main domain" not in why
