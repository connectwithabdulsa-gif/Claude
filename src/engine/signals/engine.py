"""signal engine: classify, set expiry, expire stale ones, detect csv signals,
pick the best 1 (max 2) per lead with rendered why-now lines."""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..db import log_event, now
from ..score.scorer import Config, signal_points
from .render import fill, is_same_person, template_vars


def _parse(ts):
    if not ts:
        return None
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def classify_and_expire(conn, cfg: Config, today: datetime) -> dict:
    """sets signal_class + expires_at on every signal, deactivates stale or unknown ones."""
    out = {"classified": 0, "expired": 0, "unknown_type": 0, "undated": 0}
    ts = now()
    for s in conn.execute("SELECT * FROM signals WHERE active=1").fetchall():
        cls = cfg.signal_class(s["type"])
        if not cls:
            out["unknown_type"] += 1
            continue
        key, spec = cls
        window = spec.get("window_days")
        seen = _parse(s["observed_at"])
        expires = None
        if window is not None:
            if seen is None:
                out["undated"] += 1
                conn.execute("UPDATE signals SET signal_class=?, active=0, expired_at=? WHERE id=?", (key, ts, s["id"]))
                log_event(conn, "signal_expired", "signal", s["id"], "no date, cannot prove it is fresh")
                continue
            expires = (seen + timedelta(days=window)).isoformat()
        conn.execute("UPDATE signals SET signal_class=?, expires_at=? WHERE id=?", (key, expires, s["id"]))
        out["classified"] += 1
        # trade shows count before and after the event date, so age is absolute
        if window is not None and abs((today - seen).days) > window:
            conn.execute("UPDATE signals SET active=0, expired_at=? WHERE id=?", (ts, s["id"]))
            log_event(conn, "signal_expired", "signal", s["id"],
                      f"{key} is {abs((today - seen).days)}d old, window {window}d")
            out["expired"] += 1
    conn.commit()
    return out


def detect_csv(conn, mapping_path: Path, source: str = "clay") -> dict:
    """reads signal columns out of imported csv rows (leads.raw_json). idempotent."""
    spec = (yaml.safe_load(mapping_path.read_text()) or {}).get(source) or []
    out = {"added": 0, "skipped_no_date": 0, "skipped_no_match": 0, "already": 0}
    ts = now()
    rows = conn.execute("SELECT id, company_id, raw_json, imported_at FROM leads WHERE source=?", (source,)).fetchall()
    for lead in rows:
        raw = json.loads(lead["raw_json"] or "{}")
        for m in spec:
            val = (raw.get(m["column"]) or "").strip()
            if not val:
                continue
            match = m.get("only_if_matches")
            if match and not any(w in val.lower() for w in match):
                out["skipped_no_match"] += 1
                continue
            if m.get("date_column"):
                observed = (raw.get(m["date_column"]) or "").strip()
                if not observed:
                    out["skipped_no_date"] += 1
                    continue
                observed_at, field_ref = observed, f"{m['column']} + {m['date_column']}"
            else:
                observed_at, field_ref = lead["imported_at"], f"{m['column']} (seen at import)"
            data = {m["data_key"]: [v.strip() for v in val.split(",")] if m["data_key"] == "job_titles" else
                    next((w for w in (match or []) if w in val.lower()), val)}
            if data.get("tool"):
                data["tool"] = data["tool"].title() if "." not in data["tool"] else data["tool"]
            dup = conn.execute("SELECT 1 FROM signals WHERE company_id=? AND type=? AND value=? AND source=?",
                               (lead["company_id"], m["type"], val, f"csv:{source}")).fetchone()
            if dup:
                out["already"] += 1
                continue
            conn.execute(
                "INSERT INTO signals (company_id, type, value, source, source_field, observed_at, detected_at, data_json)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (lead["company_id"], m["type"], val, f"csv:{source}", field_ref, observed_at, ts, json.dumps(data)))
            out["added"] += 1
    conn.commit()
    return out


@dataclass
class Pick:
    signal_id: int
    signal_class: str
    points: int
    age_days: int | None
    saw: str
    matters: str
    short: str | None


def pick_for_lead(cfg: Config, lead: dict, company: dict, signals: list[dict], today: datetime) -> list[Pick]:
    classes = cfg.signal_classes
    max_n = int(cfg.signals.get("max_signals_per_email", 1))
    second_min = int(cfg.signals.get("second_signal_min_points", 20))
    scored = []
    for s in signals:
        cls = cfg.signal_class(s["type"])
        if not cls:
            continue
        key, spec = cls
        pts, _ = signal_points(cfg, [s], today)
        if pts <= 0 and spec.get("window_days") is not None:
            continue
        vars_ = template_vars(s, company, lead, today)
        data = json.loads(s.get("data_json") or "{}")
        saw_t = spec.get("saw_you") if (s["lead_id"] == lead["id"] or is_same_person(data, lead)) and spec.get("saw_you") else spec.get("saw")
        saw, matters = fill(saw_t, vars_), fill(spec.get("matters"), vars_)
        if not saw or not matters:
            continue                         # missing data, never guess
        seen = _parse(s.get("observed_at"))
        age = abs((today - seen).days) if seen and spec.get("window_days") is not None else None
        scored.append(Pick(s["id"], key, pts, age, saw, matters, fill(spec.get("short"), vars_)))
    scored.sort(key=lambda p: (-p.points, p.age_days if p.age_days is not None else 999))
    picks = scored[:1]
    if max_n >= 2:
        for p in scored[1:]:
            if p.signal_class != picks[0].signal_class and p.points >= second_min:
                picks.append(p)
                break
    return picks


def pick_all(conn, cfg: Config, today: datetime) -> dict:
    from ..score.scorer import _company_view
    out = {"with_signal": 0, "fallback": 0, "skipped_dnc": 0}
    ts = now()
    for lead in [dict(r) for r in conn.execute("SELECT * FROM leads").fetchall()]:
        conn.execute("DELETE FROM signal_picks WHERE lead_id=?", (lead["id"],))
        if lead.get("tier") == "dnc":
            out["skipped_dnc"] += 1
            continue
        company = _company_view(conn, lead["company_id"])
        sigs = [dict(r) for r in conn.execute(
            "SELECT * FROM signals WHERE active=1 AND (lead_id=? OR company_id=?)", (lead["id"], lead["company_id"]))]
        picks = pick_for_lead(cfg, lead, company, sigs, today)
        for rank, p in enumerate(picks, start=1):
            conn.execute(
                "INSERT INTO signal_picks (lead_id, signal_id, rank, signal_class, points, age_days, saw_line,"
                " matters_line, short_line, picked_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (lead["id"], p.signal_id, rank, p.signal_class, p.points, p.age_days, p.saw, p.matters, p.short, ts))
        conn.execute("UPDATE leads SET fallback=? WHERE id=?", (0 if picks else 1, lead["id"]))
        out["with_signal" if picks else "fallback"] += 1
        log_event(conn, "signals_picked", "lead", lead["id"], None,
                  picks=[p.signal_class for p in picks] or ["plain fallback"])
    conn.commit()
    return out
