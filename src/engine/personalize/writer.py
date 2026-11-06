"""email 1 writer. per lead: build facts -> pick variant -> write -> QA (rules + LLM)
-> on fail, retry once as the plain fallback -> on fail again, hold for abdul."""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..db import log_event, now
from ..score.scorer import _company_view
from ..signals.render import clean_company, template_vars
from . import prompts, qa

KEY_VARS = ("role_title", "round", "place", "product", "partner", "tool", "show")


def load_cfg(config_dir: Path) -> dict:
    rd = lambda n: yaml.safe_load((config_dir / n).read_text())
    return {"llm": rd("llm.yaml"), "sender": rd("sender.yaml"), "rules": rd("copy_rules.yaml"),
            "niches": rd("niches.yaml")}


def price(llm_cfg: dict, model: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> float:
    p = llm_cfg["prices_per_million"].get(model)
    if not p:
        return 0.0
    return (input_tokens * p["input"] + output_tokens * p["output"] + cache_read * p.get("cache_read", p["input"])) / 1e6


def build_context(conn, lead: dict, niches: dict, today: datetime) -> dict:
    company = _company_view(conn, lead["company_id"])
    cname = clean_company(company.get("name")) or company.get("domain")
    niche = niches.get(lead.get("niche") or "", {})
    facts = [
        {"id": "first_name", "text": lead.get("first_name") or "", "field": "leads.first_name", "source": lead["source"]},
        {"id": "company", "text": cname, "field": "companies.name", "source": company.get("source")},
    ]
    if lead.get("title"):
        facts.append({"id": "title", "text": lead["title"], "field": "leads.title", "source": lead["source"]})
    if niche:
        facts.append({"id": "niche", "text": niche.get("plural", ""), "field": "leads.niche (scored from industry)",
                      "source": "engine"})
    if lead.get("niche") == "amazon_agency":
        facts.append({"id": "proof", "text": "we did this for an amazon agency", "field": "CLAUDE.md proof points",
                      "source": "abdul"})
    pick = conn.execute("""SELECT p.*, s.observed_at, s.source, s.source_field, s.data_json, s.type
                           FROM signal_picks p JOIN signals s ON s.id = p.signal_id
                           WHERE p.lead_id=? ORDER BY p.rank LIMIT 1""", (lead["id"],)).fetchone()
    signal = None
    if pick:
        v = template_vars(dict(pick), company, lead, today)
        signal = {"class": pick["signal_class"], "saw_line": pick["saw_line"], "matters_line": pick["matters_line"],
                  "age_days": pick["age_days"], "key_terms": [v[k] for k in KEY_VARS if v.get(k)],
                  "signal_id": pick["signal_id"]}
        facts.append({"id": "signal", "text": f"{pick['saw_line']} {pick['matters_line']}",
                      "field": pick["source_field"], "source": pick["source"], "date": pick["observed_at"]})
    return {"lead_id": lead["id"], "first_name": lead.get("first_name"), "title": lead.get("title"),
            "company": {"name": cname}, "niche": {"key": lead.get("niche"), "label": niche.get("label"),
                                                   "plural": niche.get("plural")},
            "signal": signal, "facts": facts}


def next_variant(conn, signal_class: str) -> str:
    counts = {v: 0 for v in prompts.VARIANTS}
    for r in conn.execute("SELECT variant, COUNT(*) n FROM messages WHERE step=1 AND signal_class IS ? GROUP BY variant",
                          (signal_class,)):
        if r["variant"] in counts:
            counts[r["variant"]] = r["n"]
    return min(counts, key=lambda v: (counts[v], v))


def footer(sender: dict) -> str:
    return "\n".join([sender["sign_off"], sender["company_line"], sender["physical_address"], sender["opt_out_line"]])


@dataclass
class Spend:
    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, llm_cfg, r):
        self.calls += 1
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        c = price(llm_cfg, r.model, r.input_tokens, r.output_tokens, r.cache_read_tokens)
        self.usd += c
        return c


@dataclass
class WriteOutcome:
    lead_id: int
    status: str            # ready, held
    uses_fallback: bool
    variant: str
    subject: str = ""
    body: str = ""
    reasons: list = field(default_factory=list)
    cost: float = 0.0
    attempts: int = 0


def _attempt(llm, cfg, ctx, variant, use_signal, sys_w, sys_q, spend):
    cost = 0.0
    w = llm.json_call(role="writer", system=sys_w, user=prompts.writer_input(ctx, variant, use_signal),
                      schema=prompts.WRITER_SCHEMA)
    cost += spend.add(cfg["llm"], w)
    if w.refused or not w.data:
        return None, [f"writer {'refused' if w.refused else 'returned no valid json'}"], cost
    email = {k: (w.data.get(k) or ([] if k == "facts_used" else "")) for k in ("subject", "body", "facts_used")}
    email["body"] = email["body"].strip()
    fails = [f"rules: {r}" for r in qa.check(email, ctx, cfg["rules"], use_signal)]
    if fails:                       # no point paying for the LLM QA on a rule fail
        return email, fails, cost
    q = llm.json_call(role="qa", system=sys_q, user=prompts.qa_input(ctx, email, use_signal), schema=prompts.QA_SCHEMA)
    cost += spend.add(cfg["llm"], q)
    if q.refused or not q.data:
        return email, ["qa pass did not return a verdict"], cost
    llm_fails = [f"qa {c['id']}: {c['reason']}" for c in q.data.get("checks", []) if not c.get("pass")]
    if q.data.get("verdict") != "pass" and not llm_fails:
        llm_fails = ["qa verdict fail without a reason"]
    return email, llm_fails, cost


def write_one(conn, llm, cfg, lead: dict, today: datetime, spend: Spend, sys_w: str, sys_q: str) -> WriteOutcome:
    ctx = build_context(conn, lead, cfg["niches"], today)
    has_signal = bool(ctx["signal"]) and lead.get("tier") != "C"     # tier C always gets the plain fallback
    signal_class = ctx["signal"]["class"] if has_signal else "fallback"
    variant = next_variant(conn, signal_class)
    plan = [True, False] if has_signal else [False]
    reasons, email, attempts, cost, used_fallback, ok = [], None, 0, 0.0, not has_signal, False
    for use_signal in plan:
        attempts += 1
        email, fails, c = _attempt(llm, cfg, ctx, variant, use_signal, sys_w, sys_q, spend)
        cost += c
        used_fallback = not use_signal
        if email and not fails:
            ok = True
            break
        reasons += [f"{'signal' if use_signal else 'fallback'} try: {f}" for f in fails]
    status = "ready" if ok else "held"
    body = (email or {}).get("body", "")
    full = f"{body}\n\n{footer(cfg['sender'])}" if body else ""
    conn.execute(
        """INSERT INTO messages (lead_id, step, variant, subject, body, signal_ids, qa_verdict, qa_reasons, created_at,
             status, model, input_tokens, output_tokens, cost_usd, uses_fallback, facts_json, signal_class, attempts,
             review, writer_mode)
           VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (lead["id"], variant, (email or {}).get("subject", ""), full,
         json.dumps([ctx["signal"]["signal_id"]] if has_signal and not used_fallback else []),
         "pass" if status == "ready" else "fail", json.dumps(reasons), now(), status,
         cfg["llm"]["writer"]["model"], None, None, round(cost, 6), int(used_fallback),
         json.dumps({"facts": ctx["facts"], "facts_used": (email or {}).get("facts_used", [])}),
         "fallback" if used_fallback else signal_class, attempts,
         "pending" if status == "ready" else "held", "fake" if getattr(llm, "is_fake", False) else "api"))
    log_event(conn, "email1_written", "lead", lead["id"], "; ".join(reasons) or None, status=status,
              variant=variant, fallback=used_fallback, cost=round(cost, 6))
    return WriteOutcome(lead["id"], status, used_fallback, variant, (email or {}).get("subject", ""), full,
                        reasons, cost, attempts)


def eligible_leads(conn, tiers: list[str], limit: int, redo: bool = False) -> list[dict]:
    tiers = [t for t in tiers if t in ("A", "B", "C")] or ["none"]   # dnc can never be written to
    q = f"""SELECT * FROM leads WHERE tier IN ({','.join('?' * len(tiers))})
            AND verify_status IN ('ok', 'risky')"""
    if not redo:
        q += " AND id NOT IN (SELECT lead_id FROM messages WHERE step=1)"
    q += " ORDER BY score DESC, id LIMIT ?"
    return [dict(r) for r in conn.execute(q, (*tiers, limit))]


def estimate(llm, cfg, sample_ctx: dict | None, n: int) -> dict:
    """worst case: every email needs a signal try + fallback try, each with a qa pass."""
    est = cfg["llm"]["estimate"]
    sys_w, sys_q = prompts.system_prompt(), prompts.qa_system_prompt()
    if sample_ctx:
        w_user = prompts.writer_input(sample_ctx, "v1", True)
        counted = llm.count_input_tokens(role="writer", system=sys_w, user=w_user) if llm else None
        q_counted = llm.count_input_tokens(role="qa", system=sys_q, user=w_user) if llm else None
    else:
        w_user, counted, q_counted = "", None, None
    method = "count_tokens" if counted else "rough (chars / %.1f)" % est["chars_per_token_rough"]
    w_in = counted or int(len(sys_w + w_user) / est["chars_per_token_rough"])
    q_in = q_counted or int(len(sys_q + w_user) / est["chars_per_token_rough"])
    wm, qm = cfg["llm"]["writer"]["model"], cfg["llm"]["qa"]["model"]
    per_try = price(cfg["llm"], wm, w_in, est["writer_output_tokens"]) + price(cfg["llm"], qm, q_in, est["qa_output_tokens"])
    return {"leads": n, "method": method, "writer_input_tokens": w_in, "qa_input_tokens": q_in,
            "per_lead_typical": round(per_try, 4), "per_lead_worst": round(per_try * 2, 4),
            "batch_typical": round(per_try * n, 2), "batch_worst": round(per_try * 2 * n, 2),
            "models": {"writer": wm, "qa": qm}}
