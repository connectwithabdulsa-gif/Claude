"""`engine run`: one tick of the live sender. cron runs it every 5 minutes on the sending machine.

each tick, in order:
  1. gates (kill switch, live_ok file, real address, per mailbox: not a main domain, DNS passed, password)
  2. read new replies over IMAP for every usable mailbox -> replies.ingest (stops, suppresses, pauses)
  3. plan the day once (scheduler, queue only)
  4. deliver what is due: lead still active, not suppressed, recipient inside business hours, mailbox
     not paused. at most N per mailbox per tick so sends stay spread out.
  5. health check per mailbox (auto pause), alerts
nothing here ever replies to a human (CLAUDE.md hard rule 4).
"""
import fcntl
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import suppression
from ..db import log_event, now
from ..normalize import norm_domain
from ..paths import ROOT
from ..replies import ingest as rin
from ..replies.imap import ImapReplySource
from ..score.scorer import _company_view
from ..sequence import scheduler as sch
from . import spacemail as sm

PLACEHOLDER = "[ADD PHYSICAL ADDRESS]"


@dataclass
class TickResult:
    blocked: list = field(default_factory=list)        # global gate failures (nothing sent)
    mailbox_blocked: dict = field(default_factory=dict)
    replies: dict = field(default_factory=dict)        # status -> count
    planned: int | None = None
    sent: int = 0
    failed: int = 0
    cancelled: int = 0
    waiting: int = 0
    alerts: list = field(default_factory=list)

    def lines(self) -> list[str]:
        out = []
        if self.blocked:
            out.append("BLOCKED, nothing sent: " + "; ".join(self.blocked))
        for mb, why in self.mailbox_blocked.items():
            out.append(f"mailbox {mb} skipped: {why}")
        if self.replies:
            out.append("replies read: " + ", ".join(f"{k} {v}" for k, v in self.replies.items()))
        if self.planned is not None:
            out.append(f"planned today: {self.planned} queued")
        out.append(f"sent {self.sent}  failed {self.failed}  cancelled {self.cancelled}  still waiting {self.waiting}")
        out += [f"ALERT {a}" for a in self.alerts]
        return out


def _p(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def global_gates(cfg: dict) -> list[str]:
    lv = cfg["sending"]["live"]
    out = []
    if _p(lv["stop_file"]).exists():
        out.append("kill switch is on (engine start to resume)")
    if not _p(lv["ok_file"]).exists():
        out.append("not approved for live (engine go-live after abdul's ok in chat)")
    if (cfg["sender"].get("physical_address") or PLACEHOLDER).strip() in ("", PLACEHOLDER):
        out.append("physical address is still the placeholder in config/sender.yaml")
    return out


def mailbox_gate(conn, cfg: dict, mb: dict, at: datetime) -> str | None:
    dom = norm_domain(mb["address"])
    main = set(cfg["sender"].get("main_domains") or []) | {"gmail.com", "googlemail.com"}
    if dom in main:
        return "main domain, cold email never goes out from here (rule 2)"
    if mb["paused"]:
        return f"paused: {mb['pause_reason']}"
    d = conn.execute("SELECT * FROM domains WHERE domain=?", (dom,)).fetchone()
    if not d or not d["checked_at"]:
        return "DNS never checked (engine deliverability)"
    rep = json.loads(d["dns_report"] or "{}")
    if not rep.get("ok"):
        return "DNS check failed (engine deliverability for details)"
    age = at - datetime.fromisoformat(d["checked_at"])
    if age > timedelta(hours=cfg["sending"]["live"]["dns_max_age_hours"]):
        return f"DNS check is {int(age.total_seconds() // 3600)}h old (engine deliverability)"
    try:
        sm.password_for(mb["address"])
    except sm.MissingPassword as e:
        return str(e)
    return None


def _in_hours(cfg, lead, company, at: datetime) -> bool:
    w = cfg["sending"]["window"]
    loc = at.astimezone(sch._tz(cfg["sending"], lead, company))
    return loc.weekday() < 5 and w["start_hour"] <= loc.hour < w["end_hour"]


def _cancel(conn, send_id, why):
    conn.execute("UPDATE sends SET status='cancelled', error=? WHERE id=?", (why, send_id))
    log_event(conn, "send_cancelled", "send", send_id, why)


def read_replies(conn, cfg, mb, rules, res: TickResult, imap_factory=None):
    src = ImapReplySource(conn, mb["address"], sm.password_for(mb["address"]),
                          port=cfg["sending"]["live"]["imap_port"], imap_factory=imap_factory)
    for r in src.replies():
        out = rin.ingest_one(conn, cfg, rules, r)
        res.replies[out.status] = res.replies.get(out.status, 0) + 1
        res.alerts += out.alerts
        if out.status in ("queued", "unmatched"):
            res.alerts.append(f"new reply from {r.from_address} in {mb['address']} (engine replies inbox)")


def deliver(conn, cfg, mbs: dict, at: datetime, res: TickResult, smtp_factory=None):
    lv = cfg["sending"]["live"]
    due = [dict(r) for r in conn.execute("""SELECT s.*, m.subject, m.body FROM sends s JOIN messages m ON m.id=s.message_id
                                            WHERE s.status='queued' AND s.mode='live' AND s.scheduled_at<=?
                                            ORDER BY s.scheduled_at, s.id""", (at.isoformat(),))]
    by_mb: dict[int, list] = {}
    for s in due:
        by_mb.setdefault(s["mailbox_id"], []).append(s)
    for mb_id, items in by_mb.items():
        mb = mbs.get(mb_id)
        if mb is None:                       # mailbox blocked or paused this tick: wait
            res.waiting += len(items)
            continue
        todo = []
        for s in items:
            lead = dict(conn.execute("SELECT * FROM leads WHERE id=?", (s["lead_id"],)).fetchone())
            company = _company_view(conn, lead["company_id"])
            st = conn.execute("SELECT status FROM sequence_state WHERE lead_id=?", (lead["id"],)).fetchone()
            if lead["status"] in ("stopped", "replied") or (st and st["status"] == "stopped"):
                _cancel(conn, s["id"], f"lead {lead['status']} before send")
                res.cancelled += 1
                continue
            why = suppression.check(conn, lead["email"], company.get("domain"))
            if why:
                _cancel(conn, s["id"], why)
                sch._stop(conn, lead["id"], f"suppressed: {why}")
                res.cancelled += 1
                continue
            if not _in_hours(cfg, lead, company, at):
                res.waiting += 1             # goes out when their business hours open again
                continue
            todo.append((s, lead))
        rest = todo[lv["max_per_tick_per_mailbox"]:]
        res.waiting += len(rest)
        todo = todo[: lv["max_per_tick_per_mailbox"]]
        if not todo:
            continue
        try:
            with sm.SmtpSession(mb["address"], sm.password_for(mb["address"]), port=lv["smtp_port"],
                                smtp_factory=smtp_factory) as smtp:
                for s, lead in todo:
                    msg = sm.build_message(mb["address"], cfg["sender"].get("from_name") or "", lead["email"],
                                           s["subject"], s["body"], json.loads(s["headers_json"] or "{}"))
                    try:
                        smtp.send(msg)
                    except sm.PermanentFailure as e:
                        conn.execute("UPDATE sends SET status='failed', error=?, sent_at=? WHERE id=?",
                                     (str(e), at.isoformat(), s["id"]))
                        res.failed += 1
                        res.alerts += sch.record_outcome(conn, cfg, s["id"], "bounce")
                        continue
                    except sm.TemporaryFailure as e:
                        _retry(conn, s, str(e), lv, res)
                        continue
                    conn.execute("UPDATE sends SET status='sent', sent_at=?, provider_id=?, error=NULL WHERE id=?",
                                 (at.isoformat(), s["message_id_header"], s["id"]))
                    log_event(conn, "sent_live", "send", s["id"], None, mailbox=mb["address"], step=s["step"])
                    res.sent += 1
                    conn.commit()
        except sm.PermanentFailure as e:     # login refused: the mailbox is the problem, not the lead
            conn.execute("UPDATE mailboxes SET paused=1, pause_reason=? WHERE id=?", (str(e), mb["id"]))
            log_event(conn, "mailbox_paused", "mailbox", mb["address"], str(e))
            res.alerts.append(f"{mb['address']} paused: {e}")
            res.waiting += len(todo)
        except sm.TemporaryFailure as e:
            for s, _ in todo:
                _retry(conn, s, str(e), lv, res)
        res.alerts += sch.check_health(conn, cfg, mb["id"])
    conn.commit()


def _retry(conn, s, err, lv, res):
    n = (s.get("attempts") or 0) + 1
    if n >= lv["max_attempts"]:
        conn.execute("UPDATE sends SET status='failed', attempts=?, error=? WHERE id=?", (n, err, s["id"]))
        res.failed += 1
        res.alerts.append(f"send {s['id']} failed {n} times: {err}")
    else:
        conn.execute("UPDATE sends SET attempts=?, error=? WHERE id=?", (n, err, s["id"]))
        res.waiting += 1


def tick(conn, cfg: dict, at: datetime | None = None, rng: random.Random | None = None,
         smtp_factory=None, imap_factory=None, rules: dict | None = None) -> TickResult:
    """the caller (cli) already required --live. this function sends real email when the gates pass."""
    at = at or datetime.now(timezone.utc)
    rng = rng or random.Random()
    res = TickResult()
    res.blocked = global_gates(cfg)
    if res.blocked:
        log_event(conn, "run_blocked", None, None, "; ".join(res.blocked))
        conn.commit()
        return res

    ok = {}
    for r in conn.execute("SELECT * FROM mailboxes ORDER BY id"):
        mb = dict(r)
        why = mailbox_gate(conn, cfg, mb, at)
        if why:
            res.mailbox_blocked[mb["address"]] = why
        else:
            ok[mb["id"]] = mb

    from ..paths import CONFIG_DIR
    rules = rules or rin.load_rules(CONFIG_DIR)
    for mb in ok.values():                  # replies first, so nobody who answered gets the next step
        try:
            read_replies(conn, cfg, mb, rules, res, imap_factory)
        except Exception as e:  # noqa: BLE001  reading replies must never crash sending
            res.alerts.append(f"could not read replies for {mb['address']}: {type(e).__name__}: {e}")
            log_event(conn, "imap_error", "mailbox", mb["address"], f"{type(e).__name__}: {e}")

    day = at.date()
    planned = conn.execute("SELECT 1 FROM events WHERE action='live_day_planned' AND reason=?",
                           (day.isoformat(),)).fetchone()
    if not planned and ok:
        plan = sch.plan_and_send(conn, cfg, day, sm.QueueSender(), rng, mailbox_ids=set(ok))
        res.planned = sum(plan.sent.values())
        log_event(conn, "live_day_planned", None, None, day.isoformat(), queued=res.planned, skipped=plan.skipped)
        conn.commit()

    # pick up any pause from a bounce read in the replies step
    ok = {k: dict(conn.execute("SELECT * FROM mailboxes WHERE id=?", (k,)).fetchone()) for k in ok}
    ok = {k: v for k, v in ok.items() if not v["paused"]}
    deliver(conn, cfg, ok, at, res, smtp_factory)
    log_event(conn, "run_tick", None, None, None, sent=res.sent, failed=res.failed, cancelled=res.cancelled,
              waiting=res.waiting, replies=res.replies)
    conn.commit()
    return res


class RunLock:
    """file lock so two cron ticks never run at once. returns False from acquire() if busy."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.f = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = self.path.open("w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.f.close()
            self.f = None
            return False

    def release(self):
        if self.f:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()
            self.f = None


def send_alert(cfg, alerts: list[str], mbs: list[dict], smtp_factory=None) -> bool:
    """one short alert email to abdul's own inbox. never to a prospect."""
    to = (cfg["sender"].get("alerts_to") or "").strip()
    if not to or not alerts or not mbs:
        return False
    mb = mbs[0]
    msg = sm.build_message(mb["address"], "Outbound engine", to, f"Engine: {len(alerts)} alert(s)",
                           "\n".join(alerts), {})
    try:
        with sm.SmtpSession(mb["address"], sm.password_for(mb["address"]),
                            port=cfg["sending"]["live"]["smtp_port"], smtp_factory=smtp_factory) as s:
            s.send(msg)
        return True
    except Exception:  # noqa: BLE001
        return False
