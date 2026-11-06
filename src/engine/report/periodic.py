"""engine report daily | weekly: sends, bounces, replies, positive replies and calls booked,
split by niche, signal, variant and subject. A/B: one variable at a time, nothing is judged
before ab_min_sends, then best / worst and ONE suggested change."""
from datetime import date, timedelta

DIMENSIONS = {
    "niche": "COALESCE(l.niche, 'none')",
    "signal": "COALESCE(m1.signal_class, 'fallback')",
    "variant": "COALESCE(m1.variant, '-')",
}


def _subject_split(conn, pos: list) -> list[dict]:
    """subjects as patterns ({company} outbound), so the a/b compares wording, not companies."""
    from ..signals.render import clean_company
    pos_sql = ",".join("?" * len(pos))
    rows = conn.execute(f"""SELECT l.id, m1.subject, c.name,
            (SELECT COUNT(*) FROM sends s WHERE s.lead_id = l.id) AS sends,
            (SELECT COUNT(*) FROM replies r WHERE r.lead_id = l.id
               AND COALESCE(r.category,'') NOT IN ('auto_bounce','out_of_office')) AS replies,
            (SELECT COUNT(*) FROM replies r WHERE r.lead_id = l.id AND r.category IN ({pos_sql})) AS positive
            FROM leads l JOIN messages m1 ON m1.lead_id = l.id AND m1.step = 1
            LEFT JOIN companies c ON c.id = l.company_id
            WHERE l.id IN (SELECT lead_id FROM sends)""", pos).fetchall()
    agg = {}
    for r in rows:
        pat = r["subject"] or "-"
        for name in {clean_company(r["name"]), r["name"]} - {None, ""}:
            pat = pat.replace(name, "{company}").replace(name.lower(), "{company}")
        g = agg.setdefault(pat, {"k": pat, "people": 0, "sends": 0, "bounces": 0, "replies": 0, "positive": 0})
        g["people"] += 1
        g["sends"] += r["sends"]
        g["replies"] += min(r["replies"], 1)
        g["positive"] += min(r["positive"], 1)
    out = []
    for g in sorted(agg.values(), key=lambda g: -g["sends"]):
        g["reply_pct"] = round(g["replies"] / g["people"] * 100, 1) if g["people"] else 0.0
        g["positive_pct"] = round(g["positive"] / g["people"] * 100, 1) if g["people"] else 0.0
        out.append(g)
    return out


def _window(kind: str, end: date) -> tuple[date, date]:
    return (end, end) if kind == "daily" else (end - timedelta(days=6), end)


def build(conn, cfg: dict, kind: str, end: date) -> dict:
    start, stop = _window(kind, end)
    a, b = start.isoformat(), (stop + timedelta(days=1)).isoformat()
    pos = cfg["reports"]["positive_categories"]
    pos_sql = ",".join("?" * len(pos))
    base = f"""FROM sends s JOIN leads l ON l.id = s.lead_id
               LEFT JOIN messages m1 ON m1.lead_id = l.id AND m1.step = 1
               WHERE s.sent_at >= ? AND s.sent_at < ?"""
    one = lambda q, *p: conn.execute(q, p).fetchone()[0] or 0
    totals = {
        "sends": one(f"SELECT COUNT(*) {base}", a, b),
        "new_sequences": one(f"SELECT COUNT(*) {base} AND s.step = 1", a, b),
        "bounces": one(f"SELECT COUNT(*) {base} AND s.outcome = 'bounce'", a, b),
        "replies": one("SELECT COUNT(*) FROM replies WHERE received_at >= ? AND received_at < ? AND lead_id IS NOT NULL"
                       " AND COALESCE(category,'') NOT IN ('auto_bounce','out_of_office')", a, b),
        "positive": one(f"SELECT COUNT(*) FROM replies WHERE received_at >= ? AND received_at < ? AND category IN ({pos_sql})",
                        a, b, *pos),
        "unsubscribes": one("SELECT COUNT(*) FROM replies WHERE received_at >= ? AND received_at < ? AND category='unsubscribe'",
                            a, b),
        "booked": one("SELECT COUNT(*) FROM leads WHERE booked_at >= ? AND booked_at < ?", a, b),
        "linkedin_sent": one("SELECT COUNT(*) FROM linkedin_log WHERE kind='connect' AND status!='listed'"
                             " AND updated_at >= ? AND updated_at < ?", a, b),
    }
    t = totals
    t["bounce_pct"] = round(t["bounces"] / t["sends"] * 100, 2) if t["sends"] else 0.0

    # per dimension: everyone who started a sequence (ever, so rates are fair), replies at any time
    splits = {}
    for name, expr in DIMENSIONS.items():
        rows = conn.execute(f"""
            SELECT {expr} AS k, COUNT(DISTINCT l.id) AS people, COUNT(s.id) AS sends,
                   SUM(CASE WHEN s.outcome='bounce' THEN 1 ELSE 0 END) AS bounces,
                   (SELECT COUNT(*) FROM replies r JOIN leads l2 ON l2.id = r.lead_id
                      LEFT JOIN messages mm ON mm.lead_id = l2.id AND mm.step = 1
                      WHERE {expr.replace('l.', 'l2.').replace('m1.', 'mm.')} = {expr}
                        AND COALESCE(r.category,'') NOT IN ('auto_bounce','out_of_office')) AS replies,
                   (SELECT COUNT(*) FROM replies r JOIN leads l2 ON l2.id = r.lead_id
                      LEFT JOIN messages mm ON mm.lead_id = l2.id AND mm.step = 1
                      WHERE {expr.replace('l.', 'l2.').replace('m1.', 'mm.')} = {expr}
                        AND r.category IN ({pos_sql})) AS positive
            FROM sends s JOIN leads l ON l.id = s.lead_id
            LEFT JOIN messages m1 ON m1.lead_id = l.id AND m1.step = 1
            GROUP BY k ORDER BY sends DESC""", pos).fetchall()
        splits[name] = [dict(r) | {"reply_pct": round(r["replies"] / r["people"] * 100, 1) if r["people"] else 0.0,
                                   "positive_pct": round(r["positive"] / r["people"] * 100, 1) if r["people"] else 0.0}
                        for r in rows]
    splits["subject"] = _subject_split(conn, pos)
    return {"kind": kind, "start": start.isoformat(), "end": stop.isoformat(), "totals": totals,
            "splits": splits, "ab": ab_readout(splits, cfg["reports"]["ab_min_sends"])}


def ab_readout(splits: dict, min_sends: int) -> dict:
    """best and worst per dimension only where every compared group has min_sends. ONE suggestion."""
    out, suggestion = {}, None
    for name in ("variant", "subject", "signal", "niche"):
        groups = [g for g in splits.get(name, []) if g["sends"] >= min_sends]
        if len(groups) < 2:
            biggest = max((g["sends"] for g in splits.get(name, [])), default=0)
            out[name] = f"not enough data: largest group has {biggest} of {min_sends} sends"
            continue
        best = max(groups, key=lambda g: (g["positive_pct"], g["reply_pct"]))
        worst = min(groups, key=lambda g: (g["positive_pct"], g["reply_pct"]))
        out[name] = {"best": best["k"], "best_positive_pct": best["positive_pct"],
                     "worst": worst["k"], "worst_positive_pct": worst["positive_pct"]}
        if suggestion is None and best["k"] != worst["k"]:
            suggestion = (f"change one thing: stop {name} '{worst['k']}' ({worst['positive_pct']}% positive) "
                          f"and give its volume to '{best['k']}' ({best['positive_pct']}%). keep everything else the same.")
    out["suggestion"] = suggestion or f"no change yet. keep sending until each group has {min_sends} sends."
    return out


def text(rep: dict) -> str:
    t = rep["totals"]
    lines = [f"{rep['kind']} report  {rep['start']} to {rep['end']}",
             f"sends {t['sends']} (new sequences {t['new_sequences']})   bounces {t['bounces']} ({t['bounce_pct']}%, target under 2%)",
             f"replies {t['replies']}   positive {t['positive']}   unsubscribes {t['unsubscribes']}   calls booked {t['booked']}"
             f"   linkedin sent {t['linkedin_sent']}", ""]
    for name, rows in rep["splits"].items():
        lines.append(f"by {name}  (all time: people, sends, reply %, positive %)")
        for r in rows[:8]:
            lines.append(f"  {str(r['k'])[:34]:<34} {r['people']:>4} {r['sends']:>5} {r['reply_pct']:>6}% {r['positive_pct']:>6}%")
    lines.append("")
    lines.append("a/b (one variable at a time)")
    for name in ("variant", "subject", "signal", "niche"):
        v = rep["ab"][name]
        lines.append(f"  {name:<8} " + (v if isinstance(v, str) else
                     f"best '{v['best']}' {v['best_positive_pct']}%, worst '{v['worst']}' {v['worst_positive_pct']}%"))
    lines.append(f"suggestion: {rep['ab']['suggestion']}")
    return "\n".join(lines)
