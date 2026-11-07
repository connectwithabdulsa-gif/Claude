"""reply classification and drafts.

session mode: export a brief, Claude classifies and drafts in a Claude Code session, import checks
every answer against what was stored at export. api mode: the same answers come from the
`classifier` role (Claude Haiku) through the LLM adapter. both go through apply_answer, so the
checks and actions are identical. nothing is ever sent."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from .. import suppression
from ..db import log_event, now
from ..ingest.base import SourceInfo, SourceRow
from ..ingest.importer import run_import
from ..normalize import norm_domain, norm_email
from ..paths import ROOT
from ..personalize import qa
from ..signals.render import clean_company
from .ingest import new_text, pause_sequence
from ..casing import sentence_case

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "draft": {"type": "string"},
        "follow_up_date": {"type": "string"},
        "referral": {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"},
                                                       "title": {"type": "string"}},
                     "additionalProperties": False},
    },
    "required": ["category", "confidence", "reason", "draft"],
    "additionalProperties": False,
}


def load_cfg(config_dir: Path) -> dict:
    rd = lambda n: yaml.safe_load((config_dir / n).read_text())
    return {"replies": rd("replies.yaml"), "sender": rd("sender.yaml"), "rules": rd("copy_rules.yaml"),
            "sending": rd("sending.yaml"), "llm": rd("llm.yaml")}


def reply_scripts(root: Path = ROOT) -> str:
    lib = (root / "message-library.md").read_text()
    m = re.search(r"^## 6\. .*?(?=^## \d+\. |\Z)", lib, re.S | re.M)
    return m.group(0).strip() if m else ""


def build_context(conn, reply: dict, cfg: dict) -> dict:
    lead = conn.execute("""SELECT l.*, c.name AS company FROM leads l LEFT JOIN companies c ON c.id = l.company_id
                           WHERE l.id=?""", (reply["lead_id"],)).fetchone()
    sent = conn.execute("""SELECT s.step, s.sent_at, m.subject, m.body FROM sends s JOIN messages m ON m.id = s.message_id
                           WHERE s.id=?""", (reply["send_id"],)).fetchone() if reply["send_id"] else None
    company = clean_company(lead["company"]) or lead["email_domain"]
    s = cfg["sender"]
    facts = [
        {"id": "first_name", "text": lead["first_name"] or ""},
        {"id": "company", "text": company},
        {"id": "booking_link", "text": s["booking_link"]},
        {"id": "price", "text": "$2,000 a month flat for the whole setup and running it. the infrastructure "
                                "(domains, mailboxes, tools) is billed at cost on top."},
        {"id": "proof", "text": "we did this for an amazon agency"},
    ]
    return {
        "reply_id": reply["id"], "received_at": reply["received_at"], "from": reply["from_address"],
        "subject": reply["subject"], "text": new_text(reply["body"]),
        "lead": {"first_name": lead["first_name"], "last_name": lead["last_name"], "title": lead["title"],
                 "email": lead["email"], "company": company},
        "we_sent": {"step": sent["step"], "subject": sent["subject"], "body": sent["body"].split("\n\nabdul\n")[0]}
                   if sent else None,
        "facts": facts,
    }


BRIEF = {
    "how_to_answer": [
        "pick exactly one category from `categories`. give a confidence from 0 to 1 and a one line reason.",
        "write `draft` from the matching script in `reply_scripts` (message-library.md section 6). normal "
        "sentence case, no em dashes, no exclamation marks, at most one question. only use the lead's facts, the "
        "booking link, the price line and the proof line from `facts`. no other numbers, names or claims.",
        "not_interested, out_of_office and auto_bounce need no draft: use an empty string.",
        "wrong_person_referral: fill `referral` only with a name and email the reply actually contains.",
        "not_now: fill `follow_up_date` (YYYY-MM-DD) only if the reply states a time, else leave it out.",
        "nothing you write is sent. abdul approves every draft first.",
    ],
    "answer_format": {"export_id": "<same id>", "answers": [
        {"reply_id": 0, "category": "", "confidence": 0.0, "reason": "", "draft": "",
         "referral": {"name": "", "email": "", "title": ""}, "follow_up_date": ""}]},
}


def export_batch(conn, cfg: dict, out_dir: Path) -> tuple[Path, int]:
    export_id = "rexp-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    items = []
    for r in conn.execute("SELECT * FROM replies WHERE status='new' ORDER BY received_at").fetchall():
        ctx = build_context(conn, dict(r), cfg)
        conn.execute("INSERT INTO reply_exports (export_id, reply_id, ctx_json, created_at) VALUES (?,?,?,?)",
                     (export_id, r["id"], json.dumps(ctx), now()))
        conn.execute("UPDATE replies SET status='exported', export_id=? WHERE id=?", (export_id, r["id"]))
        items.append(ctx)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{export_id}.json"
    path.write_text(json.dumps({"export_id": export_id, "created_at": now(), **BRIEF,
                                "categories": cfg["replies"]["categories"], "reply_scripts": reply_scripts(),
                                "replies": items}, indent=2, ensure_ascii=False))
    log_event(conn, "replies_exported", "export", export_id, None, replies=len(items))
    conn.commit()
    return path, len(items)


# ---- checks -----------------------------------------------------------------

LINK = re.compile(r"https?://\S+|www\.\S+", re.I)


def check_draft(draft: str, ctx: dict, cfg: dict) -> list[str]:
    fails = []
    booking = cfg["sender"]["booking_link"]
    links = LINK.findall(draft)
    for l in links:
        if l.rstrip(".,)") != booking:
            fails.append(f"link that is not the booking link: {l}")
    stripped = draft.replace(booking, "")
    fails += qa.check_followup(stripped, None, cfg["rules"])     # booking link removed first, so it never counts as a link
    allowed_text = " ".join([f["text"] for f in ctx["facts"]] + [ctx["text"], ctx["subject"] or ""])
    allowed_nums = set(re.findall(r"\d[\d,]*", allowed_text)) | set(cfg["replies"]["draft_allowed_numbers"])
    for n in re.findall(r"\d[\d,]*", stripped):
        if n.rstrip(",") not in allowed_nums:
            fails.append(f"number '{n}' is not in the facts or the reply")
    allowed_caps = {w.lower() for w in (cfg["rules"].get("proper_nouns") or [])} | {"i", "abdul", "dfy", "gtm"}
    for w in re.findall(r"[A-Za-z][A-Za-z0-9']*", allowed_text):
        allowed_caps.add(w.lower())
    for w in re.findall(r"\b[A-Z][A-Za-z0-9']*\b", stripped):
        if re.sub(r"'s$", "", w).lower() not in allowed_caps:
            fails.append(f"name or claim '{w}' is not in the facts or the reply")
            break
    return fails


def _valid_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


# ---- applying an answer -----------------------------------------------------

def apply_answer(conn, cfg: dict, reply: dict, ctx: dict, ans: dict, by: str) -> dict:
    """validate one answer, then act. returns {status, category, reasons}."""
    rc = cfg["replies"]
    reasons = []
    cat = (ans.get("category") or "").strip()
    if cat not in rc["categories"]:
        reasons.append(f"category '{cat}' is not one of the 10")
    try:
        conf = float(ans.get("confidence"))
        if not 0 <= conf <= 1:
            raise ValueError
    except (TypeError, ValueError):
        conf = 0.0
        reasons.append("confidence must be a number from 0 to 1")
    draft = (ans.get("draft") or "").strip()
    if cat in rc["needs_draft"]:
        if not draft:
            reasons.append("draft missing")
        else:
            reasons += [f"draft: {f}" for f in check_draft(draft, ctx, cfg)]
    referral = ans.get("referral") or {}
    if cat == "wrong_person_referral" and referral.get("email"):
        email = norm_email(referral["email"])
        if email not in ctx["text"].lower():
            reasons.append(f"referral email {email} is not in the reply text")
        if referral.get("name") and referral["name"].split()[0].lower() not in ctx["text"].lower():
            reasons.append(f"referral name {referral['name']} is not in the reply text")
    if conf < rc["min_confidence"] and not reasons:
        reasons.append(f"confidence {conf} under {rc['min_confidence']}")

    status = "needs_abdul" if reasons else "classified"
    conn.execute("""UPDATE replies SET category=?, confidence=?, reason=?, draft=?, draft_status=?, status=?,
                    check_reasons=?, classified_by=?, referral_json=? WHERE id=?""",
                 (cat or None, conf, ans.get("reason"), sentence_case(draft) or None,
                  ("pending" if draft else None) if status == "classified" else ("held" if draft else None),
                  status, json.dumps(reasons), by, json.dumps(referral) if referral else None, reply["id"]))
    log_event(conn, "reply_classified", "reply", reply["id"], "; ".join(reasons) or ans.get("reason"),
              category=cat, confidence=conf, status=status, by=by)
    if status == "classified":
        _act(conn, cfg, reply, ctx, cat, ans)
    return {"status": status, "category": cat, "reasons": reasons}


def _act(conn, cfg, reply, ctx, cat, ans):
    from ..sequence import scheduler as sch
    rc = cfg["replies"]
    lead_id = reply["lead_id"]
    received = datetime.fromisoformat(reply["received_at"].replace("Z", "+00:00")).date()
    if cat == "unsubscribe":
        suppression.add(conn, "email", ctx["lead"]["email"], "unsubscribed")
        if reply["send_id"]:
            sch.record_outcome(conn, {"sending": cfg["sending"]}, reply["send_id"], "unsubscribe")
    elif cat == "auto_bounce" and reply["send_id"]:
        sch.record_outcome(conn, {"sending": cfg["sending"]}, reply["send_id"], "bounce")
    elif cat == "not_interested":
        if rc.get("suppress_not_interested", True):
            suppression.add(conn, "email", ctx["lead"]["email"], "said not interested")
    elif cat == "not_now":
        d = _valid_date(ans.get("follow_up_date")) or received + timedelta(days=rc["not_now_default_days"])
        conn.execute("UPDATE leads SET follow_up_at=? WHERE id=?", (d.isoformat(), lead_id))
        conn.execute("UPDATE replies SET follow_up_at=? WHERE id=?", (d.isoformat(), reply["id"]))
    elif cat == "out_of_office":
        until = _valid_date(ans.get("follow_up_date")) or received + timedelta(days=rc["ooo_default_days"])
        pause_sequence(conn, lead_id, until, "out of office (classified)")
    elif cat == "wrong_person_referral":
        ref = ans.get("referral") or {}
        if ref.get("email"):
            _add_referral(conn, reply, ctx, ref)


class _OneRow:
    def __init__(self, row, name):
        self._row = row
        self.info = SourceInfo(source="referral", source_file=name)

    def rows(self):
        yield self._row


def _add_referral(conn, reply, ctx, ref):
    parts = (ref.get("name") or "").split()
    email = norm_email(ref["email"])
    referrer = conn.execute("SELECT l.*, c.domain AS cdomain FROM leads l LEFT JOIN companies c ON c.id=l.company_id"
                            " WHERE l.id=?", (reply["lead_id"],)).fetchone()
    same_company = norm_domain(email) == (referrer["cdomain"] or referrer["email_domain"])
    fields = {"first_name": parts[0] if parts else "", "last_name": " ".join(parts[1:]), "email": email,
              "title": ref.get("title") or "", "person_country": referrer["country"] or "",
              "company_domain": referrer["cdomain"] if same_company else norm_domain(email),
              "company_name": ctx["lead"]["company"] if same_company else ""}
    raw = {"referred_by": ctx["lead"]["email"], "reply_id": reply["id"], "reply_received_at": reply["received_at"]}
    res = run_import(conn, _OneRow(SourceRow(fields=fields, raw=raw, line=1), f"reply:{reply['id']}"))
    for lid in res.lead_ids.values():
        conn.execute("UPDATE leads SET referred_by_lead_id=? WHERE id=?", (reply["lead_id"], lid))
        for f, v in (("first_name", fields["first_name"]), ("email", email), ("title", fields["title"])):
            if v:
                conn.execute("INSERT INTO field_values (entity, entity_id, field, value, source, source_ref, observed_at,"
                             " fetched_at) VALUES ('lead',?,?,?,'referral',?,?,?)",
                             (lid, f, v, f"reply:{reply['id']}", reply["received_at"], now()))
        log_event(conn, "referral_added", "lead", lid, f"referred by lead {reply['lead_id']}")
    if not res.lead_ids:
        log_event(conn, "referral_not_added", "reply", reply["id"], "; ".join(res.rejected) or "rejected")


def import_file(conn, cfg: dict, path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    export_id = data.get("export_id")
    out = {"classified": 0, "needs_abdul": 0, "unknown": 0, "already": 0}
    for ans in data.get("answers") or []:
        row = conn.execute("SELECT * FROM reply_exports WHERE export_id=? AND reply_id=?",
                           (export_id, ans.get("reply_id"))).fetchone()
        if not row:
            out["unknown"] += 1
            continue
        if row["imported_at"]:
            out["already"] += 1
            continue
        reply = dict(conn.execute("SELECT * FROM replies WHERE id=?", (row["reply_id"],)).fetchone())
        r = apply_answer(conn, cfg, reply, json.loads(row["ctx_json"]), ans, "session")
        conn.execute("UPDATE reply_exports SET imported_at=? WHERE id=?", (now(), row["id"]))
        out[r["status"]] += 1
    conn.commit()
    return out


def classify_api(conn, cfg: dict, llm) -> dict:
    """api mode: the classifier role answers each new reply. same checks as session mode."""
    out = {"classified": 0, "needs_abdul": 0}
    system = "\n\n".join(["you sort replies to cold emails for abdul and draft an answer. json only.",
                          "\n".join(BRIEF["how_to_answer"]),
                          "categories:\n" + "\n".join(f"- {k}: {v}" for k, v in cfg["replies"]["categories"].items()),
                          reply_scripts()])
    for r in conn.execute("SELECT * FROM replies WHERE status='new'").fetchall():
        reply = dict(r)
        ctx = build_context(conn, reply, cfg)
        res = llm.json_call(role="classifier", system=system, user=json.dumps(ctx, sort_keys=True), schema=ANSWER_SCHEMA)
        ans = res.data or {"category": "", "confidence": 0, "reason": "classifier returned nothing", "draft": ""}
        out[apply_answer(conn, cfg, reply, ctx, ans, "api")["status"]] += 1
    conn.commit()
    return out


# ---- review -----------------------------------------------------------------

def inbox(conn, cfg: dict, limit: int = 50) -> list[dict]:
    urgent = cfg["replies"]["urgent"]
    rows = [dict(r) for r in conn.execute(
        """SELECT r.*, l.first_name, l.last_name, l.email AS lead_email, c.name AS company FROM replies r
           LEFT JOIN leads l ON l.id = r.lead_id LEFT JOIN companies c ON c.id = l.company_id
           WHERE COALESCE(r.draft_status, '') NOT IN ('approved') AND r.handled = 0
           ORDER BY r.received_at""")]

    def rank(r):
        if r["status"] == "classified" and r["category"] in urgent and r["draft_status"] == "pending":
            return 0
        if r["status"] in ("needs_abdul", "unmatched"):
            return 1
        if r["status"] in ("new", "exported"):
            return 2
        return 3
    rows.sort(key=lambda r: (rank(r), r["received_at"]))
    for r in rows:
        r["urgent"] = rank(r) == 0
        r["group"] = ["urgent", "needs abdul", "waiting to be sorted", "drafts to review, not urgent"][rank(r)]
    return [r for r in rows if rank(r) < 3 or r["draft_status"] == "pending"][:limit]


def set_draft(conn, reply_id: int, decision: str, note: str | None = None, new_draft: str | None = None,
              cfg: dict | None = None) -> str:
    r = conn.execute("SELECT * FROM replies WHERE id=?", (reply_id,)).fetchone()
    if not r:
        return "no such reply"
    if decision == "edit":
        ctx = json.loads(conn.execute("SELECT ctx_json FROM reply_exports WHERE reply_id=? ORDER BY id DESC LIMIT 1",
                                      (reply_id,)).fetchone()["ctx_json"]) if cfg else None
        fails = check_draft(new_draft, ctx, cfg) if (cfg and ctx) else []
        if fails:
            return "edit refused: " + "; ".join(fails)
        conn.execute("UPDATE replies SET draft=?, draft_status='pending' WHERE id=?", (new_draft, reply_id))
        decision_done = "edited"
    elif decision == "approved":
        if r["status"] != "classified" or not r["draft"]:
            return "only a checked draft can be approved"
        conn.execute("UPDATE replies SET draft_status='approved', reviewed_at=?, review_note=? WHERE id=?",
                     (now(), note, reply_id))
        decision_done = "approved (queued for phase 8 sending, nothing sent)"
    else:
        conn.execute("UPDATE replies SET draft_status='held', reviewed_at=?, review_note=? WHERE id=?",
                     (now(), note, reply_id))
        decision_done = "held"
    log_event(conn, f"reply_draft_{decision}", "reply", reply_id, note)
    conn.commit()
    return decision_done
