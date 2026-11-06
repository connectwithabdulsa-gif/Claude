"""local html dashboard: totals at the top, then a searchable, filterable table of every
prospect. clicking a row opens that person's full history.

one self contained file. no cdn, no fonts, no external url, nothing is uploaded.
until a live send exists it shows dry run data only, with a banner saying so."""
import html
import json
from datetime import datetime
from pathlib import Path

import yaml

from ..score.geo import country_code
from .summary import collect

SEQUENCE_STEPS = 4


def _tz_name(sending_cfg: dict, country: str | None) -> str:
    tz = sending_cfg.get("timezones") or {}
    return tz.get(country_code(country) or "default", tz.get("default", "UTC"))


def _local(ts: str | None, tz_name: str) -> str | None:
    if not ts:
        return None
    from zoneinfo import ZoneInfo
    try:
        return datetime.fromisoformat(ts).astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
    except (ValueError, KeyError):
        return None


def _status(lead, seq, email1, exported) -> tuple[str, str]:
    """(status for the filter, longer label)."""
    if lead["tier"] == "dnc":
        return "dnc", "do not contact"
    if seq:
        if seq["status"] == "stopped":
            if lead["status"] == "replied":
                return "replied", "replied"
            return "stopped", f"stopped: {seq['stop_reason'] or 'no reason'}"
        if seq["status"] == "completed":
            return "completed", "sequence completed"
        return "in sequence", "in sequence"
    if email1:
        if email1["review"] == "approved":
            return "approved", "approved, waiting to be scheduled"
        if email1["review"] == "held" or email1["status"] == "held":
            return "held", "email 1 held"
        return "ready for review", "email 1 waiting for review"
    if exported:
        return "exported", "in an open writing export"
    return "new", "no email 1 yet"


def build_data(conn, sending_cfg: dict, include_dry_run: bool = False) -> dict:
    live_sends = conn.execute("SELECT COUNT(*) FROM sends WHERE mode='live'").fetchone()[0]
    if live_sends and not include_dry_run:
        modes, mode_label = ("live",), "live"
    elif live_sends:
        modes, mode_label = ("live", "dry_run"), "live + dry run"
    else:
        modes, mode_label = ("dry_run",), "dry run"
    mode_sql = f"({','.join('?' * len(modes))})"

    rows = []
    leads = conn.execute("""SELECT l.*, c.name AS company, c.domain AS domain FROM leads l
                            LEFT JOIN companies c ON c.id = l.company_id ORDER BY l.score DESC, l.id""").fetchall()
    for lead in leads:
        lid = lead["id"]
        seq = conn.execute("SELECT s.*, m.address AS mailbox FROM sequence_state s LEFT JOIN mailboxes m"
                           " ON m.id = s.mailbox_id WHERE s.lead_id=?", (lid,)).fetchone()
        email1 = conn.execute("SELECT * FROM messages WHERE lead_id=? AND step=1 ORDER BY id DESC LIMIT 1",
                              (lid,)).fetchone()
        exported = conn.execute("SELECT 1 FROM email_exports WHERE lead_id=? AND imported_at IS NULL",
                                (lid,)).fetchone()
        pick = conn.execute("""SELECT p.*, s.type, s.value, s.source, s.source_field, s.observed_at
                               FROM signal_picks p JOIN signals s ON s.id = p.signal_id
                               WHERE p.lead_id=? ORDER BY p.rank LIMIT 1""", (lid,)).fetchone()
        signals = conn.execute("""SELECT type, signal_class, value, source, source_field, observed_at, active, expired_at
                                  FROM signals WHERE lead_id=? OR company_id=? ORDER BY observed_at DESC""",
                               (lid, lead["company_id"])).fetchall()
        tz_name = _tz_name(sending_cfg, lead["country"])
        sends = conn.execute(f"""SELECT s.step, s.sent_at, s.mode, s.outcome, s.outcome_at, m.address AS mailbox,
                                        msg.subject, msg.body
                                 FROM sends s LEFT JOIN mailboxes m ON m.id = s.mailbox_id
                                 LEFT JOIN messages msg ON msg.id = s.message_id
                                 WHERE s.lead_id=? AND s.mode IN {mode_sql} ORDER BY s.sent_at""",
                             (lid, *modes)).fetchall()
        replies = conn.execute("SELECT received_at, category, confidence, body, draft FROM replies WHERE lead_id=?"
                               " ORDER BY received_at", (lid,)).fetchall()
        notes = conn.execute("SELECT created_at, author, text FROM notes WHERE lead_id=? ORDER BY created_at",
                             (lid,)).fetchall()
        events = conn.execute("""SELECT ts, action, reason FROM events WHERE entity='lead' AND entity_id=?
                                 AND action IN ('sequence_stopped','email1_held','followup_held','suppressed_on_import')
                                 ORDER BY ts""", (str(lid),)).fetchall()
        status, status_label = _status(lead, seq, email1, exported)
        step_label = ""
        if seq:
            step_label = f"{seq['step_done']} of {SEQUENCE_STEPS}"
            if seq["status"] == "active" and seq["next_due"]:
                step_label += f", next {seq['next_due'][5:]}"
        person = f"{lead['first_name'] or ''} {lead['last_name'] or ''}".strip()
        rows.append({
            "id": lid, "person": person, "email": lead["email"], "title": lead["title"] or "",
            "company": lead["company"] or lead["domain"] or "", "domain": lead["domain"] or "",
            "niche": lead["niche"] or "none", "tier": lead["tier"] or "-", "score": lead["score"],
            "score_reason": lead["score_reason"] or "", "verify": lead["verify_status"] or "unverified",
            "verify_reason": lead["verify_reason"] or "", "country": lead["country"] or "",
            "source": lead["source"], "imported_at": lead["imported_at"],
            "signal": pick["signal_class"] if pick else "none",
            "signal_age": pick["age_days"] if pick else None,
            "pick": dict(pick) if pick else None,
            "signals": [dict(s) for s in signals],
            "step": step_label, "status": status, "status_label": status_label,
            "mailbox": seq["mailbox"] if seq else None,
            "email1": {k: email1[k] for k in ("subject", "body", "writer_mode", "status", "review", "review_note",
                                             "reviewed_at", "created_at", "variant", "signal_class")}
                      | {"qa_reasons": json.loads(email1["qa_reasons"] or "[]")} if email1 else None,
            "sends": [dict(s) | {"sent_local": _local(s["sent_at"], tz_name), "tz": tz_name} for s in sends],
            "replies": [dict(r) for r in replies],
            "notes": [dict(n) for n in notes],
            "events": [dict(e) for e in events],
        })
    return {"mode": mode_label, "live_sends": live_sends, "prospects": rows}


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--text:#1a1a1a;--muted:#666;--line:#e5e5e0;--accent:#2f5bff;--accent-soft:#e9eeff;
--ok:#1f8a4c;--bad:#c0392b;--warn-bg:#fff4d6;--warn-text:#7a5200}
@media (prefers-color-scheme: dark){:root{--bg:#111214;--card:#1a1b1e;--text:#eee;--muted:#9a9a9a;--line:#2b2c30;
--accent:#7c97ff;--accent-soft:#1d2340;--ok:#5fd08f;--bad:#ff7a6b;--warn-bg:#3a2f12;--warn-text:#f5d48a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,sans-serif}
main{max-width:1240px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0 0 2px}h2{font-size:15px;margin:0 0 8px}
.sub{color:var(--muted);margin:0 0 14px}.banner{background:var(--warn-bg);color:var(--warn-text);border-radius:8px;
padding:8px 12px;margin:0 0 14px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.card,section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.label{color:var(--muted);font-size:12px}.value{font-size:24px;font-weight:600}.note{color:var(--muted);font-size:12px}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
.controls input,.controls select{font:inherit;color:var(--text);background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:7px 9px}.controls input{flex:1 1 220px;min-width:0}
.count{color:var(--muted);margin-left:auto}.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:12px;color:var(--muted);cursor:pointer;user-select:none;position:sticky;top:0;background:var(--card)}
th[aria-sort=ascending]::after{content:" \\2191"}th[aria-sort=descending]::after{content:" \\2193"}
tbody tr{cursor:pointer}tbody tr:hover,tbody tr:focus{background:var(--accent-soft);outline:none}
td.wrap{white-space:normal;min-width:160px}.pill{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--accent-soft);font-size:12px}
.t-A{color:var(--ok);font-weight:600}.t-dnc{color:var(--bad)}
#panel{position:fixed;top:0;right:0;height:100%;width:min(560px,100%);background:var(--card);border-left:1px solid var(--line);
box-shadow:-8px 0 24px rgba(0,0,0,.15);overflow-y:auto;padding:18px 18px 40px;transform:translateX(105%);transition:transform .18s}
#panel.open{transform:none}#panel h3{margin:0 0 2px;font-size:18px}#panel h4{margin:18px 0 6px;font-size:13px;color:var(--muted);text-transform:lowercase}
#panel .close{float:right;font:inherit;background:none;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:4px 10px;cursor:pointer}
.item{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-bottom:8px}.item .meta{color:var(--muted);font-size:12px}
pre{white-space:pre-wrap;font:13px/1.5 system-ui,sans-serif;margin:6px 0 0}.empty{color:var(--muted)}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const P = D.prospects;
const $ = (s) => document.querySelector(s);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = String(text); if (cls) e.className = cls; return e; };
const COLS = [
  ['company', r => r.company], ['person', r => r.person], ['niche', r => r.niche],
  ['tier', r => r.tier, r => (r.score ?? -1)], ['signal', r => r.signal + (r.signal_age != null ? ' (' + r.signal_age + 'd)' : '')],
  ['step', r => r.step], ['status', r => r.status_label],
];
const FILTERS = ['niche', 'signal', 'tier', 'status'];
let sortCol = 3, sortDir = -1;

function fillSelect(key) {
  const sel = $('#f-' + key);
  [...new Set(P.map(r => r[key]))].sort().forEach(v => sel.appendChild(new Option(v, v)));
}
function state() {
  const s = { q: $('#q').value.trim().toLowerCase() };
  FILTERS.forEach(k => s[k] = $('#f-' + k).value);
  return s;
}
function saveHash() {
  const s = state(); const p = new URLSearchParams();
  Object.entries(s).forEach(([k, v]) => { if (v) p.set(k, v); });
  if (sortCol !== 3 || sortDir !== -1) p.set('sort', sortCol + ':' + sortDir);
  history.replaceState(null, '', '#' + p.toString());
}
function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  $('#q').value = p.get('q') || '';
  FILTERS.forEach(k => { if (p.get(k)) $('#f-' + k).value = p.get(k); });
  if (p.get('sort')) { const [c, d] = p.get('sort').split(':').map(Number); sortCol = c; sortDir = d; }
}
function matches(r, s) {
  if (s.q) {
    const hay = [r.company, r.person, r.email, r.title, r.signal, r.pick ? r.pick.saw_line : '', r.niche].join(' ').toLowerCase();
    if (!hay.includes(s.q)) return false;
  }
  return FILTERS.every(k => !s[k] || r[k] === s[k]);
}
function render() {
  const s = state();
  const col = COLS[sortCol]; const key = col[2] || col[1];
  const rows = P.filter(r => matches(r, s)).sort((a, b) => {
    const x = key(a), y = key(b);
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
  });
  const tb = $('#rows'); tb.replaceChildren();
  rows.forEach(r => {
    const tr = el('tr'); tr.tabIndex = 0; tr.dataset.id = r.id;
    COLS.forEach(([name, get], i) => {
      const td = el('td', get(r));
      if (name === 'tier') { td.textContent = r.tier + (r.score != null ? ' (' + r.score + ')' : ''); td.className = 't-' + r.tier; }
      if (name === 'status') td.className = 'wrap';
      tr.appendChild(td);
    });
    tr.addEventListener('click', () => openPanel(r.id));
    tr.addEventListener('keydown', e => { if (e.key === 'Enter') openPanel(r.id); });
    tb.appendChild(tr);
  });
  $('#count').textContent = rows.length + ' of ' + P.length;
  document.querySelectorAll('th').forEach((th, i) => th.setAttribute('aria-sort', i === sortCol ? (sortDir > 0 ? 'ascending' : 'descending') : 'none'));
  saveHash();
}
function item(meta, body, pre) {
  const d = el('div', null, 'item'); d.appendChild(el('div', meta, 'meta'));
  if (body) d.appendChild(el(pre ? 'pre' : 'div', body));
  return d;
}
function section(panel, title, items, emptyText) {
  panel.appendChild(el('h4', title));
  if (!items.length) { panel.appendChild(el('div', emptyText || 'none', 'empty')); return; }
  items.forEach(i => panel.appendChild(i));
}
function openPanel(id) {
  const r = P.find(x => x.id === id); const p = $('#panel-body'); p.replaceChildren();
  p.appendChild(el('h3', (r.person || r.email) + ', ' + r.company));
  p.appendChild(el('div', [r.title, r.email, r.country].filter(Boolean).join(' · '), 'note'));
  p.appendChild(el('div', 'status: ' + r.status_label + (r.step ? ' · step ' + r.step : '') + (r.mailbox ? ' · from ' + r.mailbox : ''), 'note'));

  const sig = [];
  if (r.pick) sig.push(item('picked · ' + r.pick.signal_class + ' · ' + r.pick.age_days + 'd old · source ' + r.pick.source + ' (' + r.pick.source_field + ') · seen ' + String(r.pick.observed_at || '').slice(0, 10),
                            r.pick.saw_line + ' ' + r.pick.matters_line));
  r.signals.forEach(s => sig.push(item((s.active ? 'active' : 'expired') + ' · ' + (s.signal_class || s.type) + ' · ' + s.source + ' · seen ' + String(s.observed_at || 'no date').slice(0, 10), s.value)));
  section(p, 'signal', sig, 'no signal, plain fallback');

  section(p, 'score', [item('tier ' + r.tier + ' · ' + (r.score ?? '-') + ' · email ' + r.verify + (r.verify_reason ? ' (' + r.verify_reason + ')' : ''), r.score_reason)]);

  const e1 = [];
  if (r.email1) {
    const e = r.email1;
    const meta = 'written ' + String(e.created_at).slice(0, 16) + ' · ' + (e.writer_mode || 'api') + ' · variant ' + (e.variant || '-') +
      ' · rules ' + (e.status === 'ready' ? 'pass' : 'fail') + ' · review ' + (e.review || '-') + (e.review_note ? ' (' + e.review_note + ')' : '');
    e1.push(item(meta, 'subject: ' + e.subject + '\n\n' + e.body, true));
    if (e.qa_reasons.length) e1.push(item('rule check notes', e.qa_reasons.join('\n'), true));
  }
  section(p, 'email 1', e1, 'not written yet');

  section(p, 'emails sent (' + D.mode + ')', r.sends.map(s => item(
    'step ' + s.step + ' · ' + String(s.sent_at).slice(0, 16).replace('T', ' ') + ' utc · ' + (s.sent_local || '') + ' recipient time · ' + s.mailbox + ' · ' + s.mode + (s.outcome ? ' · ' + s.outcome : ''),
    'subject: ' + s.subject + '\n\n' + s.body, true)), 'nothing sent yet');

  section(p, 'replies', r.replies.map(x => item(String(x.received_at).slice(0, 16) + ' · ' + (x.category || 'unclassified') + (x.confidence != null ? ' (' + x.confidence + ')' : ''), x.body + (x.draft ? '\n\ndraft reply:\n' + x.draft : ''), true)), 'no replies');

  const notes = r.notes.map(n => item(String(n.created_at).slice(0, 16) + ' · ' + n.author, n.text))
    .concat(r.events.map(e => item(String(e.ts).slice(0, 16) + ' · ' + e.action.replace(/_/g, ' '), e.reason)));
  section(p, 'notes', notes, 'no notes. add one with: engine note ' + r.id + ' "..."');
  $('#panel').classList.add('open'); $('#panel .close').focus();
}
document.querySelectorAll('th').forEach((th, i) => th.addEventListener('click', () => { sortDir = (i === sortCol) ? -sortDir : 1; sortCol = i; render(); }));
$('#panel .close').addEventListener('click', () => $('#panel').classList.remove('open'));
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#panel').classList.remove('open'); });
FILTERS.forEach(k => { fillSelect(k); $('#f-' + k).addEventListener('change', render); });
$('#q').addEventListener('input', render);
loadHash(); render();
"""


def render_html(totals: dict, data: dict) -> str:
    e = html.escape

    def card(label, value, note=""):
        return (f'<div class="card"><div class="label">{e(label)}</div><div class="value">{e(str(value))}</div>'
                f'<div class="note">{e(note)}</div></div>')

    s = totals
    bounce_cls = "bad" if s["bounce_pct"] >= 2 else "ok"
    if data["mode"] == "dry run":
        banner = '<p class="banner">DRY RUN DATA. simulated sends only, nothing was sent.</p>'
    elif "dry" in data["mode"]:
        banner = '<p class="banner">LIVE + DRY RUN DATA mixed, because --include-dry-run was used.</p>'
    else:
        banner = ""
    paused = sum(1 for m in s["mailboxes"] if m["paused"])
    # every "<" escaped, so prospect text can never open or close a tag inside the data block
    blob = json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")
    plural = {"niche": "niches", "signal": "signals", "tier": "tiers", "status": "statuses"}
    filters = "".join(f'<select id="f-{k}" aria-label="filter by {k}"><option value="">all {plural[k]}</option></select>'
                      for k in ("niche", "signal", "tier", "status"))
    head = "".join(f"<th>{c}</th>" for c in ("company", "person", "niche", "tier", "signal", "step", "status"))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Outbound Pipeline</title>
<style>{CSS}</style></head><body><main>
<h1>Outbound pipeline</h1><p class="sub">DFY GTM Systems, generated {e(s['generated_at'])}. local file, never uploaded.</p>
{banner}
<div class="grid">
{card('leads', s['leads'], f"{s['companies']} companies")}
{card('tier A', s['by_tier'].get('A', 0), 'score 75+')}
{card('email 1 approved', s['email1_review'].get('approved', 0), f"{s['email1_review'].get('pending', 0)} waiting for review")}
{card('active sequences', s['sequences'].get('active', 0), f"{s['sequences'].get('completed', 0)} completed")}
{card('sends', s['sends'], data['mode'])}
<div class="card"><div class="label">bounce rate</div><div class="value {bounce_cls}">{s['bounce_pct']}%</div><div class="note">target under 2%</div></div>
{card('reply rate', f"{s['reply_pct']}%", 'all replies')}
{card('mailboxes', len(s['mailboxes']), f"{paused} paused")}
{card('spend', f"${s['anthropic_usd']}", f"{s['explorium_credits']} explorium credits")}
</div>
<h2>prospects</h2>
<div class="controls"><input id="q" type="search" placeholder="search company, person, email, signal" aria-label="search">
{filters}<span id="count" class="count"></span></div>
<div class="tablewrap"><table><thead><tr>{head}</tr></thead><tbody id="rows"></tbody></table></div>
</main>
<aside id="panel" aria-label="prospect history"><button class="close" type="button">close</button><div id="panel-body"></div></aside>
<script type="application/json" id="data">{blob}</script>
<script>{JS}</script>
</body></html>"""


def write_dashboard(conn, config_dir: Path, out: Path, include_dry_run: bool = False) -> dict:
    sending = yaml.safe_load((config_dir / "sending.yaml").read_text())
    data = build_data(conn, sending, include_dry_run)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(collect(conn), data))
    return data
