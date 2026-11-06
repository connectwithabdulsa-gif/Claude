"""reply ingest: match each reply to a lead, act at once where the rules say so, queue the
rest for classification. nothing here ever sends anything."""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..db import log_event, now
from ..normalize import norm_domain, norm_email
from ..sequence import scheduler as sch
from .base import IncomingReply

QUOTE_START = re.compile(r"^(on .{3,120}wrote:|-{2,}\s*original message|from:\s.+|sent from my )", re.I)
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
RETURN_RE = re.compile(
    r"\b(?:back|return(?:ing)?|until|till|through)\s+(?:on\s+|in the office on\s+)?"
    r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?"
    r"(?:(\d{4}-\d{2}-\d{2})|(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})|([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?)", re.I)


def load_rules(config_dir: Path) -> dict:
    return yaml.safe_load((config_dir / "replies.yaml").read_text())


def new_text(body: str) -> str:
    """the part of the reply they wrote, without the quoted thread below it."""
    out = []
    for line in (body or "").splitlines():
        if line.strip().startswith(">") or QUOTE_START.match(line.strip()):
            break
        out.append(line)
    return "\n".join(out).strip()


def return_date(text: str, received: date) -> date | None:
    """a return date only if the text states one. never guessed."""
    m = RETURN_RE.search(text or "")
    if not m:
        return None
    iso, d1, mon1, mon2, d2 = m.groups()
    try:
        if iso:
            return date.fromisoformat(iso)
        mon, day = (mon1, d1) if d1 else (mon2, d2)
        month = MONTHS.get(mon.lower()) or MONTHS.get(mon.lower()[:3])
        if not month:
            return None
        out = date(received.year, month, int(day))
        return out if out >= received else date(received.year + 1, month, int(day))
    except ValueError:
        return None


def instant_category(r: IncomingReply, rules: dict) -> str | None:
    inst = rules["instant"]
    frm, subj, text = r.from_address.lower(), (r.subject or "").lower(), new_text(r.body).lower()
    if any(k in frm for k in inst["bounce_from"]) or any(k in subj for k in inst["bounce_subject"]):
        return "auto_bounce"
    if any(re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", text) for k in inst["unsubscribe"]):
        return "unsubscribe"
    if any(k in subj for k in inst["out_of_office_subject"]) or any(k in text for k in inst["out_of_office_body"]):
        return "out_of_office"
    return None


@dataclass
class Match:
    lead_id: int | None
    send_id: int | None
    matched_by: str


def match(conn, r: IncomingReply) -> Match:
    # 1. thread headers against our own Message-IDs
    for mid in [r.in_reply_to, *r.references]:
        if not mid:
            continue
        s = conn.execute("SELECT id, lead_id FROM sends WHERE message_id_header=? ORDER BY id DESC LIMIT 1",
                         (mid.strip(),)).fetchone()
        if s:
            return Match(s["lead_id"], s["id"], "thread")
    # 2. the from address
    email = norm_email(re.sub(r".*<([^>]+)>.*", r"\1", r.from_address))
    lead = conn.execute("SELECT id FROM leads WHERE email=?", (email,)).fetchone()
    if lead:
        return Match(lead["id"], _last_send(conn, lead["id"]), "email")
    # 3. the domain, only if exactly one lead at that company is in sequence
    dom = norm_domain(email)
    rows = conn.execute("""SELECT l.id FROM leads l JOIN sequence_state s ON s.lead_id = l.id
                           WHERE l.email_domain=? AND s.status IN ('active','paused')""", (dom,)).fetchall()
    if len(rows) == 1:
        return Match(rows[0]["id"], _last_send(conn, rows[0]["id"]), "domain")
    return Match(None, None, "unmatched")


def _last_send(conn, lead_id):
    r = conn.execute("SELECT id FROM sends WHERE lead_id=? ORDER BY id DESC LIMIT 1", (lead_id,)).fetchone()
    return r["id"] if r else None


def pause_sequence(conn, lead_id: int, until: date, reason: str) -> bool:
    """pause instead of stop. only when there is a next step left to send."""
    cur = conn.execute("UPDATE sequence_state SET status='paused', paused_until=?, stop_reason=NULL, updated_at=?"
                       " WHERE lead_id=? AND next_step IS NOT NULL AND status IN ('active','stopped','paused')",
                       (until.isoformat(), now(), lead_id))
    if not cur.rowcount:
        return False
    conn.execute("UPDATE leads SET status='in_sequence' WHERE id=?", (lead_id,))
    log_event(conn, "sequence_paused", "lead", lead_id, reason, until=until.isoformat())
    return True


@dataclass
class IngestResult:
    reply_id: int | None
    status: str          # duplicate, unmatched, acted, queued
    category: str | None
    matched_by: str
    alerts: list


def ingest_one(conn, cfg_seq: dict, rules: dict, r: IncomingReply) -> IngestResult:
    if r.message_id and conn.execute("SELECT 1 FROM replies WHERE message_id=?", (r.message_id,)).fetchone():
        return IngestResult(None, "duplicate", None, "-", [])
    received = r.received_at or now()
    m = match(conn, r)
    cur = conn.execute(
        """INSERT INTO replies (lead_id, send_id, received_at, from_address, to_address, subject, body, message_id,
             in_reply_to, matched_by, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (m.lead_id, m.send_id, received, r.from_address, r.to_address, r.subject, r.body, r.message_id,
         r.in_reply_to, m.matched_by, "unmatched" if m.lead_id is None else "new"))
    rid = cur.lastrowid
    log_event(conn, "reply_received", "reply", rid, None, lead_id=m.lead_id, matched_by=m.matched_by)
    if m.lead_id is None:
        conn.commit()
        return IngestResult(rid, "unmatched", None, m.matched_by, [])

    cat = instant_category(r, rules)
    alerts = []
    if cat == "auto_bounce":
        if m.send_id:
            alerts = sch.record_outcome(conn, cfg_seq, m.send_id, "bounce")
        _classify(conn, rid, cat, 1.0, "delivery failure from a mail server", None, "rules")
    elif cat == "unsubscribe":
        if m.send_id:
            alerts = sch.record_outcome(conn, cfg_seq, m.send_id, "unsubscribe")
        else:
            from .. import suppression
            lead = conn.execute("SELECT email FROM leads WHERE id=?", (m.lead_id,)).fetchone()
            suppression.add(conn, "email", lead["email"], "unsubscribed")
        _classify(conn, rid, cat, 1.0, "asked to be removed. suppressed at once.", rules["unsubscribe_confirmation"],
                  "rules")
    elif cat == "out_of_office":
        recv = datetime.fromisoformat(received.replace("Z", "+00:00")).date()
        back = return_date(new_text(r.body), recv)
        until = (back + timedelta(days=1)) if back else recv + timedelta(days=rules["ooo_default_days"])
        pause_sequence(conn, m.lead_id, until, "out of office" + (f", back {back}" if back else ", no date given"))
        conn.execute("UPDATE replies SET follow_up_at=? WHERE id=?", (until.isoformat(), rid))
        _classify(conn, rid, cat, 1.0, f"out of office, sequence paused until {until}"
                  + ("" if back else " (no return date in the text, default used)"), None, "rules")
    else:
        # a human wrote back: stop the sequence now, classify later
        if m.send_id:
            alerts = sch.record_outcome(conn, cfg_seq, m.send_id, "reply")
        else:
            sch._stop(conn, m.lead_id, "reply")
    conn.commit()
    return IngestResult(rid, "acted" if cat else "queued", cat, m.matched_by, alerts)


def _classify(conn, rid, category, confidence, reason, draft, by):
    conn.execute("UPDATE replies SET category=?, confidence=?, reason=?, draft=?, draft_status=?, status='classified',"
                 " classified_by=? WHERE id=?",
                 (category, confidence, reason, draft, "pending" if draft else None, by, rid))
    log_event(conn, "reply_classified", "reply", rid, reason, category=category, by=by)
