"""session writing mode: no api, nothing leaves the machine.

export  -> one json brief per batch with every fact (source + date) the api writer would get.
           the facts are also stored in the db, so import checks against what was really given,
           not against whatever is in the file.
write   -> Claude (in a Claude Code session) fills in `emails` following CLAUDE.md section 7
           and message-library.md, listing the fact ids each email used.
import  -> same free rule checks as api mode. signal email first, plain fallback second,
           otherwise held. footer added by code. no Claude QA pass: `engine review` instead.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from ..db import log_event, now
from . import prompts, qa
from .writer import build_context, footer, next_variant

BRIEF = {
    "how_to_write": [
        "follow CLAUDE.md section 7 (writing rules) and message-library.md (anatomy, variants, niche lines).",
        "use only the facts in each lead's `facts`. no other fact, number, client name or timeline.",
        "list the id of every fact you used in `facts_used`.",
        "write `signal_email` only when `needs` lists it: open with what we saw (the signal fact), then why it matters.",
        "always write `fallback_email`: the plain fallback from message-library.md, no signal.",
        "body: 50 to 90 words, exactly one question mark (the ask), no links, no sign off, no footer.",
        "subject: 2 to 6 words, under 50 characters, lowercase except names, no re: or fwd:.",
        "follow the lead's `variant_instruction` for shape. vary wording between leads.",
    ],
    "answer_format": {"export_id": "<same id>", "emails": [
        {"lead_id": 0, "signal_email": {"subject": "", "body": "", "facts_used": []},
         "fallback_email": {"subject": "", "body": "", "facts_used": []}}]},
}


def _pending_variant_counts(conn, signal_class):
    """variants already handed out in open exports, so one batch rotates too."""
    rows = conn.execute("SELECT variant, ctx_json FROM email_exports WHERE imported_at IS NULL").fetchall()
    out = {}
    for r in rows:
        if (json.loads(r["ctx_json"]).get("signal") or {}).get("class", "fallback") == signal_class:
            out[r["variant"]] = out.get(r["variant"], 0) + 1
    return out


def _variant(conn, signal_class):
    base = {v: 0 for v in prompts.VARIANTS}
    for r in conn.execute("SELECT variant, COUNT(*) n FROM messages WHERE step=1 AND signal_class IS ? GROUP BY variant",
                          (signal_class,)):
        if r["variant"] in base:
            base[r["variant"]] += r["n"]
    for v, n in _pending_variant_counts(conn, signal_class).items():
        base[v] = base.get(v, 0) + n
    return min(base, key=lambda v: (base[v], v))


def export_batch(conn, leads: list[dict], niches: dict, out_dir: Path, today: datetime) -> tuple[Path, int]:
    export_id = "exp-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    items = []
    for lead in leads:
        if conn.execute("SELECT 1 FROM email_exports WHERE lead_id=? AND imported_at IS NULL", (lead["id"],)).fetchone():
            continue                                   # already waiting in another export
        ctx = build_context(conn, lead, niches, today)
        has_signal = bool(ctx["signal"]) and lead.get("tier") != "C"
        if not has_signal:
            ctx["signal"] = None
            ctx["facts"] = [f for f in ctx["facts"] if f["id"] != "signal"]
        cls = ctx["signal"]["class"] if has_signal else "fallback"
        variant = _variant(conn, cls)
        needs = ["signal_email", "fallback_email"] if has_signal else ["fallback_email"]
        conn.execute("INSERT INTO email_exports (export_id, lead_id, variant, needs, ctx_json, created_at)"
                     " VALUES (?,?,?,?,?,?)", (export_id, lead["id"], variant, json.dumps(needs), json.dumps(ctx), now()))
        items.append({
            "lead_id": lead["id"], "first_name": ctx["first_name"], "company": ctx["company"]["name"],
            "title": ctx["title"], "tier": lead.get("tier"), "niche": ctx["niche"],
            "variant": variant, "variant_instruction": prompts.VARIANTS[variant].format(first_name=ctx["first_name"] or "there"),
            "needs": needs, "signal": ctx["signal"], "facts": ctx["facts"],
        })
    conn.commit()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{export_id}.json"
    path.write_text(json.dumps({"export_id": export_id, "created_at": now(), **BRIEF, "leads": items},
                               indent=2, ensure_ascii=False))
    log_event(conn, "email1_exported", "export", export_id, None, leads=len(items), path=str(path))
    conn.commit()
    return path, len(items)


def _clean(email: dict | None) -> dict | None:
    if not email or not (email.get("body") or "").strip():
        return None
    return {"subject": (email.get("subject") or "").strip(), "body": email["body"].strip(),
            "facts_used": [str(x) for x in email.get("facts_used") or []]}


def import_file(conn, path: Path, cfg: dict) -> dict:
    data = json.loads(Path(path).read_text())
    export_id = data.get("export_id")
    out = {"ready": 0, "held": 0, "fallback": 0, "unknown_lead": 0, "already_imported": 0}
    for item in data.get("emails") or []:
        row = conn.execute("SELECT * FROM email_exports WHERE export_id=? AND lead_id=?",
                           (export_id, item.get("lead_id"))).fetchone()
        if not row:
            out["unknown_lead"] += 1
            log_event(conn, "import_skipped", "lead", item.get("lead_id"), f"not in export {export_id}")
            continue
        if row["imported_at"]:
            out["already_imported"] += 1
            continue
        ctx = json.loads(row["ctx_json"])          # the facts we really handed out
        needs = json.loads(row["needs"])
        tries = ([("signal", True, _clean(item.get("signal_email")))] if "signal_email" in needs else []) + \
                [("fallback", False, _clean(item.get("fallback_email")))]
        reasons, passed, passed_signal, best, attempts = [], None, False, None, 0
        for label, use_signal, email in tries:
            attempts += 1
            if email is None:
                reasons.append(f"{label} try: missing")
                continue
            fails = qa.check(email, ctx, cfg["rules"], use_signal)
            if not fails:
                passed, passed_signal = email, use_signal
                break
            reasons += [f"{label} try: rules: {f}" for f in fails]
            best = best or email                     # keep the failed text visible for review
        chosen = passed or best
        status = "ready" if passed else "held"
        used_fallback = not passed_signal
        body = (chosen or {}).get("body", "")
        cls = (ctx.get("signal") or {}).get("class") if not used_fallback else "fallback"
        conn.execute(
            """INSERT INTO messages (lead_id, step, variant, subject, body, signal_ids, qa_verdict, qa_reasons,
                 created_at, status, model, cost_usd, uses_fallback, facts_json, signal_class, attempts, review,
                 writer_mode) VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item["lead_id"], row["variant"], (chosen or {}).get("subject", ""),
             f"{body}\n\n{footer(cfg['sender'])}" if body else "",
             json.dumps([ctx["signal"]["signal_id"]] if ctx.get("signal") and not used_fallback else []),
             "rules pass" if status == "ready" else "rules fail", json.dumps(reasons), now(), status,
             "claude-code-session", 0.0, int(used_fallback),
             json.dumps({"facts": ctx["facts"], "facts_used": (chosen or {}).get("facts_used", [])}),
             cls, attempts, "pending" if status == "ready" else "held", "session"))
        conn.execute("UPDATE email_exports SET imported_at=? WHERE id=?", (now(), row["id"]))
        log_event(conn, "email1_imported", "lead", item["lead_id"], "; ".join(reasons) or None,
                  status=status, fallback=used_fallback, export_id=export_id)
        out[status] += 1
        out["fallback"] += int(used_fallback and status == "ready")
    conn.commit()
    return out


def review_rows(conn, status: str = "pending", limit: int = 50) -> list[dict]:
    q = """SELECT m.*, l.first_name, l.email, l.tier, l.score FROM messages m JOIN leads l ON l.id = m.lead_id
           WHERE m.step = 1 AND m.review = ? ORDER BY l.score DESC, m.id LIMIT ?"""
    return [dict(r) for r in conn.execute(q, (status, limit))]


def set_review(conn, ids: list[int], decision: str, note: str | None = None) -> int:
    assert decision in ("approved", "held")
    n = 0
    for mid in ids:
        r = conn.execute("SELECT status FROM messages WHERE id=? AND step=1", (mid,)).fetchone()
        if not r:
            continue
        if decision == "approved" and r["status"] != "ready":
            continue                                   # a rule failure can never be approved
        conn.execute("UPDATE messages SET review=?, reviewed_at=?, review_note=? WHERE id=?",
                     (decision, now(), note, mid))
        log_event(conn, f"email1_{decision}", "message", mid, note)
        n += 1
    conn.commit()
    return n
