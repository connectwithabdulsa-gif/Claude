"""4 email sequence scheduler (CLAUDE.md section 9).

per day: follow ups that are due go first, then new sequences (tue to thu only), tier A then B then C.
every mailbox has a daily cap from its ramp. sends land inside the recipient's 9 to 5, spaced by a
random 3 to 8 minute gap per mailbox. suppression and stop status are checked again right before
each send. one person per company in sequence at a time."""
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .. import suppression
from ..db import log_event, now
from ..personalize import qa
from ..personalize.writer import footer
from ..score.geo import country_code
from ..score.scorer import _company_view
from ..send.base import OutgoingEmail, build_headers
from . import templates
from ..casing import sentence_case

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def load_cfg(config_dir: Path) -> dict:
    rd = lambda n: yaml.safe_load((config_dir / n).read_text())
    return {"sending": rd("sending.yaml"), "sender": rd("sender.yaml"), "rules": rd("copy_rules.yaml"),
            "niches": rd("niches.yaml")}


def mailbox_cap(mb: dict, day: date, ramp: dict, assume_warm: bool = False) -> int:
    if mb["paused"]:
        return 0
    live = mb.get("live_from")
    if not live:
        if not mb.get("warmup_started_at"):
            return 0
        live = (date.fromisoformat(mb["warmup_started_at"][:10]) + timedelta(days=ramp["warmup_days"])).isoformat()
    live_d = date.fromisoformat(live[:10])
    if day < live_d and not assume_warm:
        return 0
    weeks = max(0, (day - live_d).days // 7) if day >= live_d else 0
    return min(ramp["start"] + ramp["weekly_increase"] * weeks, ramp["cap"], mb["daily_cap"] or ramp["cap"])


def _tz(cfg, lead, company):
    cc = country_code(lead.get("country")) or country_code(company.get("country")) or "default"
    return ZoneInfo(cfg["timezones"].get(cc, cfg["timezones"]["default"]))


def _step_day(cfg, step):
    return next(s["day"] for s in cfg["sequence"] if s["step"] == step)


def _thread(cfg, step):
    return next(s["thread"] for s in cfg["sequence"] if s["step"] == step)


@dataclass
class DayPlan:
    day: date
    caps: dict = field(default_factory=dict)          # mailbox address -> cap
    sent: dict = field(default_factory=dict)          # mailbox address -> count
    by_step: dict = field(default_factory=dict)
    skipped: dict = field(default_factory=dict)       # reason -> count
    first_last_local: dict = field(default_factory=dict)  # mailbox -> [first, last] send time (recipient local)


def _skip(plan, why):
    plan.skipped[why] = plan.skipped.get(why, 0) + 1


def plan_and_send(conn, cfg: dict, day: date, sender, rng: random.Random, assume_warm: bool = False) -> DayPlan:
    sc = cfg["sending"]
    plan = DayPlan(day)
    wd = DAYS[day.weekday()]
    # out of office pauses end: the next step goes out from the return date, never earlier
    d = day.isoformat()
    for r in conn.execute("SELECT lead_id FROM sequence_state WHERE status='paused' AND paused_until<=?", (d,)).fetchall():
        conn.execute("UPDATE sequence_state SET status='active', paused_until=NULL, updated_at=?,"
                     " next_due=CASE WHEN next_due IS NULL OR next_due < ? THEN ? ELSE next_due END WHERE lead_id=?",
                     (now(), d, d, r["lead_id"]))
        log_event(conn, "sequence_resumed", "lead", r["lead_id"], "out of office ended")
    mbs = [dict(r) for r in conn.execute("SELECT * FROM mailboxes ORDER BY id")]
    remaining = {}
    cursor = {}                                       # mailbox -> last send time (utc)
    for mb in mbs:
        cap = mailbox_cap(mb, day, sc["ramp"], assume_warm)
        plan.caps[mb["address"]] = cap
        plan.sent[mb["address"]] = 0
        remaining[mb["id"]] = cap
        cursor[mb["id"]] = None
    if not any(remaining.values()):
        _skip(plan, "no mailbox capacity (warming, paused or none)")
        return plan

    jobs = []
    # 1. follow ups due today, sticky to the mailbox that sent email 1
    if wd in sc["window"]["followup_days"]:
        for st in conn.execute("SELECT * FROM sequence_state WHERE status='active' AND next_due<=? ORDER BY next_due",
                               (day.isoformat(),)):
            jobs.append(("followup", dict(st)))
    # 2. new sequences, tuesday to thursday only
    if wd in sc["window"]["first_email_days"]:
        review_sql = " AND m.review='approved'" if sc.get("require_approval", True) else " AND COALESCE(m.review,'')!='held'"
        for r in conn.execute(f"""SELECT l.id AS lead_id, l.company_id, m.id AS message_id FROM leads l
                JOIN messages m ON m.lead_id=l.id AND m.step=1 AND m.status='ready'{review_sql}
                WHERE l.tier IN ('A','B','C') AND l.id NOT IN (SELECT lead_id FROM sequence_state)
                ORDER BY CASE l.tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END, l.score DESC, l.id"""):
            jobs.append(("new", dict(r)))

    active_companies = {r[0] for r in conn.execute("SELECT company_id FROM sequence_state WHERE status='active'")}
    w = sc["window"]

    # pass 1: decide who gets an email today, by priority (follow ups, then tier A, B, C)
    picked = []                                       # (mailbox, kind, job, lead, company, tz, window start, end)
    rr_i = 0
    for kind, job in jobs:
        lead = dict(conn.execute("SELECT * FROM leads WHERE id=?", (job["lead_id"],)).fetchone())
        company = _company_view(conn, lead["company_id"])
        if lead["status"] in ("stopped", "replied", "completed"):
            _skip(plan, "lead stopped")
            continue
        why = suppression.check(conn, lead["email"], company.get("domain"))
        if why:
            _skip(plan, "suppressed at send time")
            if kind == "followup":
                _stop(conn, lead["id"], f"suppressed: {why}")
            continue
        if kind == "new":
            if sc.get("one_active_per_company", True) and lead["company_id"] in active_companies:
                _skip(plan, "another person at this company is in sequence")
                continue
            mb = None
            for _ in range(len(mbs)):
                cand = mbs[rr_i % len(mbs)]
                rr_i += 1
                if remaining[cand["id"]] > 0:
                    mb = cand
                    break
            if mb is None:
                _skip(plan, "daily caps reached")
                continue
            active_companies.add(lead["company_id"])
        else:
            mb = next((m for m in mbs if m["id"] == job["mailbox_id"]), None)
            if mb is None or remaining.get(mb["id"], 0) <= 0:
                _skip(plan, "mailbox cap reached, follow up moves to next day")
                continue
        remaining[mb["id"]] -= 1
        tz = _tz(sc, lead, company)
        ws = datetime.combine(day, time(w["start_hour"]), tz).astimezone(timezone.utc)
        we = datetime.combine(day, time(w["end_hour"]), tz).astimezone(timezone.utc)
        picked.append((mb, kind, job, lead, company, tz, ws, we))

    # pass 2: per mailbox, send in order of when each recipient's window opens, spaced by jitter
    picked.sort(key=lambda x: (x[0]["id"], x[6]))
    for mb, kind, job, lead, company, tz, ws, we in picked:
        gap = timedelta(minutes=rng.uniform(*w["jitter_minutes"]))
        t = max(ws, (cursor[mb["id"]] + gap) if cursor[mb["id"]] else ws + gap)
        if t > we:
            _skip(plan, "outside recipient business hours today")
            if kind == "new":
                active_companies.discard(lead["company_id"])
            continue

        step = 1 if kind == "new" else job["next_step"]
        if kind == "new":
            m = dict(conn.execute("SELECT * FROM messages WHERE id=?", (job["message_id"],)).fetchone())
            subject, body, in_reply_to = m["subject"], m["body"], None
        else:
            subj, text = templates.render(step, lead, company, cfg["niches"].get(lead.get("niche") or "", {}))
            fails = qa.check_followup(text, subj, cfg["rules"])
            if fails:
                _skip(plan, "follow up failed rule check")
                log_event(conn, "followup_held", "lead", lead["id"], "; ".join(fails), step=step)
                continue
            same = _thread(sc, step) == "same"
            subject = job["thread_subject"] if same else sentence_case(subj)
            body = sentence_case(f"{text}\n\n{footer(cfg['sender'])}")
            in_reply_to = job["first_message_id"] if same else None
            conn.execute("INSERT INTO messages (lead_id, step, subject, body, qa_verdict, qa_reasons, created_at, status,"
                         " model, review, writer_mode) VALUES (?,?,?,?,'rules pass','[]',?,'ready','template','auto',"
                         "'template')", (lead["id"], step, subject, body, now()))
            m = {"id": conn.execute("SELECT last_insert_rowid()").fetchone()[0]}

        msg_id = f"<{uuid.uuid4().hex}@{mb['address'].split('@')[1]}>"
        out = OutgoingEmail(lead["id"], step, mb["address"], lead["email"], subject, body, t.isoformat())
        out.headers = build_headers(out, in_reply_to, msg_id, sc["headers"]["list_unsubscribe"])
        provider_id = sender.send(out)
        conn.execute("INSERT INTO sends (message_id, mailbox_id, scheduled_at, sent_at, mode, status, provider_id,"
                     " lead_id, step, message_id_header, headers_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (m["id"], mb["id"], t.isoformat(), t.isoformat(), sender.mode, "sent", provider_id, lead["id"],
                      step, msg_id, json.dumps(out.headers)))

        nxt = step + 1 if step < max(x["step"] for x in sc["sequence"]) else None
        due = None
        if nxt:
            gap_days = max(_step_day(sc, nxt) - _step_day(sc, step), sc["min_gap_days"])
            due = (day + timedelta(days=gap_days)).isoformat()
        if kind == "new":
            conn.execute("INSERT INTO sequence_state (lead_id, company_id, mailbox_id, thread_subject, first_message_id,"
                         " step_done, next_step, next_due, status, started_at, updated_at) VALUES (?,?,?,?,?,1,?,?,"
                         "'active',?,?)", (lead["id"], lead["company_id"], mb["id"], subject, msg_id, nxt, due, now(), now()))
            conn.execute("UPDATE leads SET status='in_sequence' WHERE id=?", (lead["id"],))
        else:
            conn.execute("UPDATE sequence_state SET step_done=?, next_step=?, next_due=?, status=?, updated_at=?"
                         " WHERE lead_id=?", (step, nxt, due, "active" if nxt else "completed", now(), lead["id"]))
            if not nxt:
                conn.execute("UPDATE leads SET status='completed' WHERE id=?", (lead["id"],))
        cursor[mb["id"]] = t
        plan.sent[mb["address"]] += 1
        plan.by_step[step] = plan.by_step.get(step, 0) + 1
        loc = t.astimezone(tz).strftime("%H:%M")
        fl = plan.first_last_local.setdefault(mb["address"], [loc, loc])
        fl[1] = loc
    conn.commit()
    return plan


def _stop(conn, lead_id, reason):
    conn.execute("UPDATE sequence_state SET status='stopped', stop_reason=?, next_due=NULL, updated_at=? WHERE lead_id=?",
                 (reason, now(), lead_id))
    conn.execute("UPDATE leads SET status='stopped' WHERE id=?", (lead_id,))
    log_event(conn, "sequence_stopped", "lead", lead_id, reason)


def record_outcome(conn, cfg: dict, send_id: int, outcome: str) -> list[str]:
    """reply, bounce, unsubscribe or complaint on a send. stops the sequence at once,
    suppresses where the rules say so, then checks mailbox health. returns alerts."""
    assert outcome in ("reply", "bounce", "unsubscribe", "complaint")
    s = conn.execute("SELECT s.*, l.email FROM sends s JOIN leads l ON l.id=s.lead_id WHERE s.id=?", (send_id,)).fetchone()
    conn.execute("UPDATE sends SET outcome=?, outcome_at=? WHERE id=?", (outcome, now(), send_id))
    _stop(conn, s["lead_id"], outcome)
    if outcome == "reply":
        conn.execute("UPDATE leads SET status='replied' WHERE id=?", (s["lead_id"],))
    else:
        suppression.add(conn, "email", s["email"], {"bounce": "bounced", "unsubscribe": "unsubscribed",
                                                    "complaint": "spam complaint"}[outcome])
    alerts = check_health(conn, cfg, s["mailbox_id"])
    conn.commit()
    return alerts


def check_health(conn, cfg: dict, mailbox_id: int) -> list[str]:
    h = cfg["sending"]["health"]
    mb = conn.execute("SELECT * FROM mailboxes WHERE id=?", (mailbox_id,)).fetchone()
    if mb["paused"]:
        return []
    last = conn.execute("SELECT outcome FROM sends WHERE mailbox_id=? ORDER BY id DESC LIMIT ?",
                        (mailbox_id, h["bounce_window"])).fetchall()
    n = len(last)
    bounces = sum(1 for r in last if r["outcome"] == "bounce")
    complaints = sum(1 for r in last if r["outcome"] == "complaint")
    reason = None
    if h.get("pause_on_any_complaint") and complaints:
        reason = f"spam complaint on {mb['address']}"
    elif n and bounces / n * 100 > h["bounce_pause_pct"] and n >= h.get("min_sample", 20):
        reason = f"bounce rate {bounces / n * 100:.1f}% on last {n} sends of {mb['address']} (limit {h['bounce_pause_pct']}%)"
    if reason:
        conn.execute("UPDATE mailboxes SET paused=1, pause_reason=? WHERE id=?", (reason, mailbox_id))
        log_event(conn, "mailbox_paused", "mailbox", mailbox_id, reason)
        return [f"PAUSED {reason}. abdul needs to look before it sends again."]
    return []
