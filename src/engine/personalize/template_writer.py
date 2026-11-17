"""template writer: fills Abdul's approved copy (config/templates/<signal class>.yaml) with stored
facts only. no api, no cost, scales to hundreds a day. every email still runs the full rule check;
a fail is held for review, never sent. variants rotate per signal class."""
import json
import re
from datetime import datetime
from pathlib import Path

import yaml

from ..casing import sentence_case
from ..db import log_event, now
from ..signals.render import template_vars
from . import qa
from .writer import build_context, footer

PH = re.compile(r"\{(\w+)\}")


def load_templates(config_dir: Path, signal_class: str) -> dict | None:
    p = Path(config_dir) / "templates" / f"{signal_class}.yaml"
    return (yaml.safe_load(p.read_text()) or {}).get("variants") if p.exists() else None


def _variant(conn, signal_class: str, names: list[str]) -> str:
    counts = {v: 0 for v in names}
    for r in conn.execute("SELECT variant, COUNT(*) n FROM messages WHERE step=1 AND signal_class=? GROUP BY variant",
                          (signal_class,)):
        if r["variant"] in counts:
            counts[r["variant"]] = r["n"]
    return min(counts, key=lambda v: (counts[v], v))


def fill(text: str, values: dict) -> str | None:
    """None if a placeholder has no stored value (never guess a fact)."""
    missing = [k for k in PH.findall(text) if not values.get(k)]
    if missing:
        return None
    return PH.sub(lambda m: values[m.group(1)], text)


def write_one(conn, cfg: dict, config_dir: Path, lead: dict, today: datetime, auto_approve: bool) -> str:
    ctx = build_context(conn, lead, cfg["niches"], today)
    sig = ctx.get("signal")
    if not sig:
        return "no_signal"
    variants = load_templates(config_dir, sig["class"])
    if not variants:
        return "no_template"
    pick = conn.execute("""SELECT p.*, s.observed_at, s.data_json, s.type FROM signal_picks p
                           JOIN signals s ON s.id=p.signal_id WHERE p.lead_id=? ORDER BY p.rank LIMIT 1""",
                        (lead["id"],)).fetchone()
    from ..score.scorer import _company_view
    tv = template_vars(dict(pick), _company_view(conn, lead["company_id"]), lead, today)
    role = (tv.get("role_title") or "").strip()
    values = {"first_name": ctx.get("first_name"), "company": ctx["company"]["name"], "role": role,
              "saw": sentence_case(sig["saw_line"]), "matters": sentence_case(sig["matters_line"])}
    name = _variant(conn, sig["class"], sorted(variants))
    v = variants[name]
    subject, body = fill(v["subject"], values), fill(v["body"].strip(), values)
    reasons = []
    if subject is None or body is None:
        reasons.append("a template placeholder has no stored fact")
        subject, body = subject or "", body or ""
    else:
        subject = sentence_case(subject)
        body = sentence_case(body)
        email = {"subject": subject, "body": body, "facts_used": ["first_name", "company", "signal"]}
        reasons = [f"rules: {f}" for f in qa.check(email, ctx, cfg["rules"], True)]
    status = "held" if reasons else "ready"
    review = "approved" if (status == "ready" and auto_approve) else ("pending" if status == "ready" else "held")
    conn.execute(
        """INSERT INTO messages (lead_id, step, variant, subject, body, signal_ids, qa_verdict, qa_reasons, created_at,
             status, model, cost_usd, uses_fallback, facts_json, signal_class, attempts, review, writer_mode, reviewed_at,
             review_note) VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (lead["id"], name, subject, sentence_case(f"{body}\n\n{footer(cfg['sender'])}") if body else "",
         json.dumps([sig["signal_id"]]), "rules pass" if status == "ready" else "rules fail", json.dumps(reasons),
         now(), status, "template", 0.0, 0, json.dumps({"facts": ctx["facts"], "values": values}), sig["class"], 1,
         review, "template", now() if review == "approved" else None,
         "auto approved: approved template + every rule passed" if review == "approved" else None))
    log_event(conn, "email1_template", "lead", lead["id"], "; ".join(reasons) or None, status=status, variant=name)
    return status


def write_batch(conn, cfg: dict, config_dir: Path, leads: list[dict], today: datetime, auto_approve: bool) -> dict:
    out = {"ready": 0, "held": 0, "no_signal": 0, "no_template": 0}
    for lead in leads:
        out[write_one(conn, cfg, config_dir, lead, today, auto_approve)] += 1
    conn.commit()
    return out
