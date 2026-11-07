"""daily LinkedIn list for abdul to send BY HAND. nothing here touches LinkedIn.
connection note and follow ups from message-library.md section 7."""
import re
from datetime import date

from .. import suppression
from ..db import now
from ..signals.render import clean_company
from ..casing import sentence_case

CONNECT = "hey {first}, saw {signal}. i work on outbound systems for {plural}. would be good to connect."
CONNECT_PLAIN = "hey {first}, i work on outbound systems for {plural}. would be good to connect."
AFTER_ACCEPT = "thanks for connecting {first}. saw {signal}. curious how you're handling outbound at {company} right now?"
AFTER_ACCEPT_PLAIN = "thanks for connecting {first}. curious how you're handling outbound at {company} right now?"


def _ok(note: str, rules: dict, max_chars: int) -> list[str]:
    from ..personalize import qa
    fails = [f for f in qa.check_followup(note, None, rules)]
    if len(note) > max_chars:
        fails.append(f"{len(note)} chars, max {max_chars}")
    return fails


def build(conn, cfg: dict, niches: dict, rules: dict, day: date) -> dict:
    li = cfg["linkedin"]
    d = day.isoformat()
    conn.execute("DELETE FROM linkedin_log WHERE list_date=? AND status='listed'", (d,))   # rebuild today's list
    out = {"connect": [], "followup": [], "skipped": {}}

    # follow ups first: people who accepted and have no follow up yet
    for r in conn.execute("""SELECT g.lead_id, l.first_name, l.linkedin_url, c.name AS company, p.short_line
            FROM linkedin_log g JOIN leads l ON l.id=g.lead_id LEFT JOIN companies c ON c.id=l.company_id
            LEFT JOIN signal_picks p ON p.lead_id=l.id AND p.rank=1
            WHERE g.kind='connect' AND g.status='accepted'
              AND g.lead_id NOT IN (SELECT lead_id FROM linkedin_log WHERE kind='followup')""").fetchall():
        v = {"first": r["first_name"] or "there", "company": clean_company(r["company"]) or "your team",
             "signal": r["short_line"]}
        note = (AFTER_ACCEPT if r["short_line"] else AFTER_ACCEPT_PLAIN).format(**v)
        if not _ok(note, rules, 1000):
            note = sentence_case(note)
            out["followup"].append({"lead_id": r["lead_id"], "name": r["first_name"], "url": r["linkedin_url"], "note": note})
            conn.execute("INSERT INTO linkedin_log (lead_id, kind, note, list_date, updated_at) VALUES (?,?,?,?,?)",
                         (r["lead_id"], "followup", note, d, now()))

    # new connection requests: tier A, a profile link, not suppressed, never listed before, max 20
    tiers = li.get("tiers", ["A"])
    q = f"""SELECT l.*, c.name AS company, c.domain AS cdomain, p.short_line FROM leads l
            LEFT JOIN companies c ON c.id=l.company_id LEFT JOIN signal_picks p ON p.lead_id=l.id AND p.rank=1
            WHERE l.tier IN ({','.join('?' * len(tiers))})
              AND l.id NOT IN (SELECT lead_id FROM linkedin_log WHERE kind='connect')
            ORDER BY l.score DESC, l.id"""
    for r in conn.execute(q, tiers).fetchall():
        if len(out["connect"]) >= li["max_per_day"]:
            break
        if not r["linkedin_url"]:
            out["skipped"]["no profile link"] = out["skipped"].get("no profile link", 0) + 1
            continue
        if suppression.check(conn, r["email"], r["cdomain"]):
            out["skipped"]["suppressed"] = out["skipped"].get("suppressed", 0) + 1
            continue
        plural = (niches.get(r["niche"] or "") or {}).get("plural") or "b2b teams"
        v = {"first": r["first_name"] or "there", "plural": plural, "signal": r["short_line"]}
        note = CONNECT.format(**v) if r["short_line"] else CONNECT_PLAIN.format(**v)
        if _ok(note, rules, li["max_note_chars"]):
            note = CONNECT_PLAIN.format(**v)              # signal line too long: plain note instead
        if _ok(note, rules, li["max_note_chars"]):
            out["skipped"]["note failed checks"] = out["skipped"].get("note failed checks", 0) + 1
            continue
        note = sentence_case(note)
        url = r["linkedin_url"] if r["linkedin_url"].startswith("http") else "https://" + r["linkedin_url"]
        out["connect"].append({"lead_id": r["id"], "name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip(),
                               "title": r["title"], "company": clean_company(r["company"]), "url": url,
                               "signal": r["short_line"], "note": note})
        conn.execute("INSERT INTO linkedin_log (lead_id, kind, note, list_date, updated_at) VALUES (?,?,?,?,?)",
                     (r["id"], "connect", note, d, now()))
    conn.commit()
    return out


def markdown(out: dict, day: date) -> str:
    lines = [f"# linkedin list {day.isoformat()}", "",
             "send these by hand. nothing is automated. mark each one after: "
             "`engine linkedin mark <lead id> sent|accepted|replied|skipped`", ""]
    if out["followup"]:
        lines += ["## follow ups (they accepted)", ""]
        for x in out["followup"]:
            lines += [f"- **{x['name']}** (lead {x['lead_id']}) {x['url']}", f"  > {x['note']}", ""]
    lines += [f"## new connection requests ({len(out['connect'])})", ""]
    for i, x in enumerate(out["connect"], 1):
        lines += [f"{i}. **{x['name']}**, {x['title'] or ''} at {x['company'] or ''} (lead {x['lead_id']})",
                  f"   {x['url']}", f"   > {x['note']}", f"   ({len(x['note'])} chars"
                  + (f", signal: {x['signal']})" if x["signal"] else ", no signal, plain note)"), ""]
    if out["skipped"]:
        lines += ["skipped: " + ", ".join(f"{k} {v}" for k, v in out["skipped"].items())]
    return "\n".join(lines) + "\n"


def mark(conn, lead_id: int, status: str) -> int:
    assert status in ("sent", "accepted", "replied", "skipped")
    kind = "followup" if status == "replied" else "connect"
    cur = conn.execute("UPDATE linkedin_log SET status=?, updated_at=? WHERE lead_id=? AND kind=?",
                       (status, now(), lead_id, kind))
    if not cur.rowcount and status == "replied":
        cur = conn.execute("UPDATE linkedin_log SET status=?, updated_at=? WHERE lead_id=? AND kind='connect'",
                           (status, now(), lead_id))
    conn.commit()
    return cur.rowcount
