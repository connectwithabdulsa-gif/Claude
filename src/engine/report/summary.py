"""one set of pipeline numbers, used by `engine summary` (text) and `engine dashboard` (local html)."""
import html
from datetime import datetime, timezone


def collect(conn) -> dict:
    one = lambda q, *a: conn.execute(q, a).fetchone()[0] or 0
    group = lambda q, *a: {r[0] if r[0] is not None else "none": r[1] for r in conn.execute(q, a)}
    sends = one("SELECT COUNT(*) FROM sends")
    bounces = one("SELECT COUNT(*) FROM sends WHERE outcome='bounce'")
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "leads": one("SELECT COUNT(*) FROM leads"),
        "companies": one("SELECT COUNT(*) FROM companies"),
        "by_source": group("SELECT source, COUNT(*) FROM leads GROUP BY source"),
        "by_tier": group("SELECT tier, COUNT(*) FROM leads GROUP BY tier"),
        "verify": group("SELECT verify_status, COUNT(*) FROM leads GROUP BY verify_status"),
        "signals_active": one("SELECT COUNT(*) FROM signals WHERE active=1"),
        "signals_expired": one("SELECT COUNT(*) FROM signals WHERE active=0"),
        "with_signal_pick": one("SELECT COUNT(DISTINCT lead_id) FROM signal_picks"),
        "email1_review": group("SELECT review, COUNT(*) FROM messages WHERE step=1 GROUP BY review"),
        "email1_mode": group("SELECT writer_mode, COUNT(*) FROM messages WHERE step=1 GROUP BY writer_mode"),
        "open_exports": one("SELECT COUNT(*) FROM email_exports WHERE imported_at IS NULL"),
        "sequences": group("SELECT status, COUNT(*) FROM sequence_state GROUP BY status"),
        "sends": sends,
        "sends_by_mode": group("SELECT mode, COUNT(*) FROM sends GROUP BY mode"),
        "sends_by_step": group("SELECT step, COUNT(*) FROM sends GROUP BY step"),
        "outcomes": group("SELECT outcome, COUNT(*) FROM sends WHERE outcome IS NOT NULL GROUP BY outcome"),
        "bounce_pct": round(bounces / sends * 100, 2) if sends else 0.0,
        "reply_pct": round(one("SELECT COUNT(*) FROM sends WHERE outcome='reply'") / sends * 100, 2) if sends else 0.0,
        "mailboxes": [dict(r) for r in conn.execute(
            "SELECT m.address, m.paused, m.pause_reason, (SELECT COUNT(*) FROM sends s WHERE s.mailbox_id=m.id) AS sends"
            " FROM mailboxes m ORDER BY m.id")],
        "sends_per_day": [dict(r) for r in conn.execute(
            "SELECT date(sent_at) AS day, COUNT(*) AS n FROM sends GROUP BY day ORDER BY day DESC LIMIT 30")][::-1],
        "suppression": one("SELECT COUNT(*) FROM suppression"),
        "explorium_credits": one("SELECT SUM(credits) FROM api_calls WHERE provider='explorium'"),
        "anthropic_usd": round(one("SELECT SUM(cost_usd) FROM messages"), 4),
        "alerts": [dict(r) for r in conn.execute(
            "SELECT ts, reason FROM events WHERE action IN ('mailbox_paused','find_stopped') ORDER BY id DESC LIMIT 10")],
    }


def _kv(d: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in d.items()) or "none"


def text(s: dict) -> str:
    lines = [
        f"DFY GTM Systems pipeline  ({s['generated_at']})",
        f"leads        {s['leads']} at {s['companies']} companies   sources: {_kv(s['by_source'])}",
        f"tiers        {_kv(s['by_tier'])}",
        f"emails       verify {_kv(s['verify'])}",
        f"signals      {s['signals_active']} active, {s['signals_expired']} expired, {s['with_signal_pick']} leads with a picked signal",
        f"email 1      review {_kv(s['email1_review'])}   written by {_kv(s['email1_mode'])}   open exports {s['open_exports']}",
        f"sequences    {_kv(s['sequences'])}",
        f"sends        {s['sends']} ({_kv(s['sends_by_mode'])})   by step {_kv(s['sends_by_step'])}",
        f"outcomes     {_kv(s['outcomes'])}   bounce {s['bounce_pct']}% (target under 2%)   reply {s['reply_pct']}%",
        f"spend        explorium {s['explorium_credits']} credits, anthropic ${s['anthropic_usd']}",
        f"suppression  {s['suppression']} entries",
    ]
    for m in s["mailboxes"]:
        lines.append(f"mailbox      {m['address']:<30} {m['sends']:>4} sends  "
                     + (f"PAUSED: {m['pause_reason']}" if m["paused"] else "ok"))
    for a in s["alerts"]:
        lines.append(f"ALERT        {a['ts'][:16]} {a['reason']}")
    return "\n".join(lines)


def dashboard_html(s: dict) -> str:
    e = html.escape
    def card(label, value, note=""):
        return f'<div class="card"><div class="label">{e(label)}</div><div class="value">{e(str(value))}</div><div class="note">{e(note)}</div></div>'
    def table(title, d):
        rows = "".join(f"<tr><td>{e(str(k))}</td><td class='n'>{e(str(v))}</td></tr>" for k, v in d.items()) or "<tr><td colspan=2>none</td></tr>"
        return f"<section><h2>{e(title)}</h2><table>{rows}</table></section>"
    peak = max([d["n"] for d in s["sends_per_day"]] or [1])
    bars = "".join(
        f'<div class="bar" title="{e(d["day"])}: {d["n"]}"><span style="height:{int(d["n"] / peak * 100)}%"></span>'
        f'<em>{e(d["day"][5:])}</em></div>' for d in s["sends_per_day"]) or "<p class='note'>no sends yet</p>"
    mbs = "".join(f"<tr><td>{e(m['address'])}</td><td class='n'>{m['sends']}</td><td>"
                  f"{'<b class=bad>paused</b> ' + e(m['pause_reason'] or '') if m['paused'] else '<span class=ok>ok</span>'}</td></tr>"
                  for m in s["mailboxes"]) or "<tr><td colspan=3>no mailboxes</td></tr>"
    alerts = "".join(f"<li>{e(a['ts'][:16])} {e(a['reason'])}</li>" for a in s["alerts"]) or "<li>none</li>"
    bounce_cls = "bad" if s["bounce_pct"] >= 2 else "ok"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Outbound Pipeline</title>
<style>
:root{{--bg:#f7f7f5;--card:#fff;--text:#1a1a1a;--muted:#666;--line:#e5e5e0;--accent:#2f5bff;--ok:#1f8a4c;--bad:#c0392b}}
@media (prefers-color-scheme: dark){{:root{{--bg:#111214;--card:#1a1b1e;--text:#eee;--muted:#9a9a9a;--line:#2b2c30;--accent:#7c97ff;--ok:#5fd08f;--bad:#ff7a6b}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}}h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:15px;margin:0 0 8px}}
.sub{{color:var(--muted);margin:0 0 20px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:20px}}
.card,section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.label{{color:var(--muted);font-size:12px}}.value{{font-size:26px;font-weight:600}}.note{{color:var(--muted);font-size:12px}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse}}td{{padding:4px 0;border-bottom:1px solid var(--line)}}td.n{{text-align:right;font-variant-numeric:tabular-nums}}td+td{{padding-left:12px}}
.chart{{display:flex;align-items:flex-end;gap:6px;height:140px;overflow-x:auto}}.bar{{flex:1;min-width:22px;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center}}
.bar span{{width:100%;background:var(--accent);border-radius:3px 3px 0 0;min-height:2px}}.bar em{{font-size:10px;color:var(--muted);font-style:normal}}
.ok{{color:var(--ok)}}.bad{{color:var(--bad)}}ul{{margin:0;padding-left:18px}}
</style></head><body><main>
<h1>Outbound pipeline</h1><p class="sub">DFY GTM Systems, generated {e(s['generated_at'])}. local file, not published.</p>
<div class="grid">
{card('leads', s['leads'], f"{s['companies']} companies")}
{card('tier A', s['by_tier'].get('A', 0), 'score 75+')}
{card('email 1 approved', s['email1_review'].get('approved', 0), f"{s['email1_review'].get('pending', 0)} waiting for review")}
{card('active sequences', s['sequences'].get('active', 0), f"{s['sequences'].get('completed', 0)} completed")}
{card('sends', s['sends'], _kv(s['sends_by_mode']))}
<div class="card"><div class="label">bounce rate</div><div class="value {bounce_cls}">{s['bounce_pct']}%</div><div class="note">target under 2%</div></div>
{card('reply rate', f"{s['reply_pct']}%", 'all replies')}
{card('spend', f"${s['anthropic_usd']}", f"{s['explorium_credits']} explorium credits")}
</div>
<section style="margin-bottom:12px"><h2>sends per day</h2><div class="chart">{bars}</div></section>
<div class="cols">
{table('tiers', s['by_tier'])}{table('email verification', s['verify'])}{table('email 1 review', s['email1_review'])}
{table('sends by step', s['sends_by_step'])}{table('outcomes', s['outcomes'])}{table('sequences', s['sequences'])}
</div>
<div class="cols"><section><h2>mailboxes</h2><table>{mbs}</table></section><section><h2>alerts</h2><ul>{alerts}</ul></section></div>
</main></body></html>"""
