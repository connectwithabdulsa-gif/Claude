"""one set of pipeline numbers, used by `engine summary` (text, with "since last time") and
`engine dashboard` (local html, see dashboard.py)."""
import json
from datetime import datetime, timezone

from ..db import now


def collect(conn) -> dict:
    one = lambda q, *a: conn.execute(q, a).fetchone()[0] or 0
    # keys as strings so a snapshot (json) and a live collect compare equal
    group = lambda q, *a: {str(r[0]) if r[0] is not None else "none": r[1] for r in conn.execute(q, a)}
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
        "notes": one("SELECT COUNT(*) FROM notes"),
        "replies_by_category": group("SELECT COALESCE(category, status), COUNT(*) FROM replies GROUP BY 1"),
        "replies_urgent_waiting": one("SELECT COUNT(*) FROM replies WHERE draft_status='pending' AND category IN"
                                      " ('interested','meeting_request','question','asks_if_automated')"),
        "replies_needs_abdul": one("SELECT COUNT(*) FROM replies WHERE status IN ('needs_abdul','unmatched')"),
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
        f"replies      {_kv(s.get('replies_by_category', {}))}   urgent waiting {s.get('replies_urgent_waiting', 0)}"
        f"   needs abdul {s.get('replies_needs_abdul', 0)}",
        f"suppression  {s['suppression']} entries",
    ]
    for m in s["mailboxes"]:
        lines.append(f"mailbox      {m['address']:<30} {m['sends']:>4} sends  "
                     + (f"PAUSED: {m['pause_reason']}" if m["paused"] else "ok"))
    for a in s["alerts"]:
        lines.append(f"ALERT        {a['ts'][:16]} {a['reason']}")
    return "\n".join(lines)


# ---- since last time -------------------------------------------------------

def last_snapshot(conn) -> dict | None:
    r = conn.execute("SELECT ts, data_json FROM summary_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    return {"ts": r["ts"], "data": json.loads(r["data_json"])} if r else None


def save_snapshot(conn, s: dict) -> None:
    conn.execute("INSERT INTO summary_snapshots (ts, data_json) VALUES (?, ?)", (now(), json.dumps(s, default=str)))
    conn.commit()


def _dict_delta(new: dict, old: dict) -> dict:
    keys = list(dict.fromkeys(list(new) + list(old)))
    out = {}
    for k in keys:
        d = (new.get(k) or 0) - (old.get(k) or 0)
        if d:
            out[str(k)] = d
    return out


def _signed(d: dict) -> str:
    return ", ".join(f"{k} {v:+d}" for k, v in d.items()) or "no change"


def since_text(new: dict, prev: dict | None, now_dt: datetime | None = None) -> str:
    if prev is None:
        return "since last time: first run, nothing to compare yet. this summary is now the baseline."
    old, ts = prev["data"], prev["ts"]
    now_dt = now_dt or datetime.now(timezone.utc)
    then = datetime.fromisoformat(ts)
    hours = (now_dt - then).total_seconds() / 3600
    ago = f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f} days ago"
    lines = [f"since last time ({ts[:16].replace('T', ' ')} UTC, {ago})"]

    def row(label, value):
        lines.append(f"  {label:<14} {value}")

    row("new leads", f"{new['leads'] - old['leads']:+d}" + (f"  ({_signed(_dict_delta(new['by_source'], old['by_source']))})"
                                                             if new["leads"] != old["leads"] else ""))
    row("tiers", _signed(_dict_delta(new["by_tier"], old["by_tier"])))
    w = sum(new["email1_review"].values()) - sum(old["email1_review"].values())
    row("email 1", f"written {w:+d}, review " + _signed(_dict_delta(new["email1_review"], old["email1_review"])))
    row("sends", f"{new['sends'] - old['sends']:+d}  by step " + _signed(_dict_delta(new["sends_by_step"], old["sends_by_step"])))
    out_d = _dict_delta(new["outcomes"], old["outcomes"])
    row("replies", f"{out_d.get('reply', 0):+d}   by category "
                   + _signed(_dict_delta(new.get("replies_by_category", {}), old.get("replies_by_category", {})))
                   + f"   urgent waiting now {new.get('replies_urgent_waiting', 0)}")
    row("bounces", f"{out_d.get('bounce', 0):+d}   unsubscribes {out_d.get('unsubscribe', 0):+d}   complaints {out_d.get('complaint', 0):+d}")
    row("sequences", _signed(_dict_delta(new["sequences"], old["sequences"])))
    was_paused = {m["address"] for m in old["mailboxes"] if m["paused"]}
    now_paused = {m["address"]: m["pause_reason"] for m in new["mailboxes"] if m["paused"]}
    newly = [f"{a} ({r})" for a, r in now_paused.items() if a not in was_paused]
    resumed = [a for a in was_paused if a not in now_paused]
    row("mailboxes", ("newly paused: " + "; ".join(newly) if newly else "no new pauses")
        + (f"   resumed: {', '.join(resumed)}" if resumed else ""))
    row("spend", f"explorium {new['explorium_credits'] - old['explorium_credits']:+d} credits, "
                 f"anthropic ${new['anthropic_usd'] - old['anthropic_usd']:+.4f}")
    row("notes", f"{new.get('notes', 0) - old.get('notes', 0):+d}")
    old_alerts = {(a["ts"], a["reason"]) for a in old["alerts"]}
    for a in new["alerts"]:
        if (a["ts"], a["reason"]) not in old_alerts:
            lines.append(f"  NEW ALERT      {a['ts'][:16]} {a['reason']}")
    return "\n".join(lines)


def full_summary(conn, save: bool = True) -> str:
    s = collect(conn)
    out = since_text(s, last_snapshot(conn)) + "\n\n" + text(s)
    if save:
        save_snapshot(conn, s)
    return out
