"""score leads 0 to 100 (CLAUDE.md section 9). weights live in config/scoring.yaml.

fit 40 + signal 35 + data 15 + geo 10. hard do-not-contact rules win over any score.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import suppression
from ..db import log_event, now
from ..normalize import norm_domain
from .geo import country_code


@dataclass
class Config:
    scoring: dict
    niches: dict
    signals: dict

    @classmethod
    def load(cls, config_dir: Path) -> "Config":
        rd = lambda n: yaml.safe_load((config_dir / n).read_text()) or {}
        return cls(rd("scoring.yaml"), rd("niches.yaml"), rd("signals.yaml"))

    @property
    def signal_classes(self) -> dict:
        return self.signals.get("classes", self.signals)

    def signal_class(self, raw_type: str) -> tuple[str, dict] | None:
        classes = self.signal_classes
        if raw_type in classes:
            return raw_type, classes[raw_type]
        for key, spec in classes.items():
            if raw_type in (spec.get("sources") or []):
                return key, spec
        return None


@dataclass
class Score:
    total: int
    tier: str
    reason: str
    niche: str | None
    parts: dict = field(default_factory=dict)
    dnc_reasons: list = field(default_factory=list)


def _has_any(text: str, words) -> str | None:
    """whole word / phrase match, so 'cro' does not hit 'microsoft'."""
    for w in words or []:
        if re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + r"(?![a-z0-9])", text):
            return w
    return None


def match_niche(cfg: Config, text: str, naics: str | None) -> tuple[str | None, int, str]:
    """best niche for a company. returns (niche key, points, why)."""
    fit = cfg.scoring["icp_fit"]
    text = (text or "").lower()
    naics = str(naics or "")
    best = (None, 0, "no niche match")
    for key, n in cfg.niches.items():
        hit = None
        if naics and any(naics.startswith(c) for c in n.get("naics_strong") or []):
            hit = (fit["niche_strong"], f"naics {naics}")
        elif w := _has_any(text, n.get("keywords_strong")):
            hit = (fit["niche_strong"], f"'{w}'")
        elif naics and any(naics.startswith(c) for c in n.get("naics_weak") or []):
            hit = (fit["niche_weak"], f"naics {naics}")
        elif w := _has_any(text, n.get("keywords_weak")):
            hit = (fit["niche_weak"], f"'{w}'")
        if hit and hit[0] > best[1]:
            best = (key, hit[0], f"{n.get('label', key)} via {hit[1]}")
    return best


def title_points(cfg: Config, title: str | None) -> tuple[int, str, bool | None]:
    """(points, label, is decision maker). None = no title to judge."""
    if not title:
        return 0, "no title", None
    t = title.lower()
    fit, titles = cfg.scoring["icp_fit"], cfg.scoring["titles"]
    if _has_any(t, titles.get("exclude")):
        return 0, title, False
    if _has_any(re.sub(r"vice[ -]president", "", t), titles["top"]):
        return fit["title_top"], title, True
    if _has_any(t, titles["lead"]):
        return fit["title_lead"], title, True
    return 0, title, False


def signal_points(cfg: Config, signals: list[dict], today: datetime) -> tuple[int, str]:
    """best single active signal, linear decay to decay_floor at the window edge."""
    sp = cfg.scoring["signal"]
    best = (0, "no active signal")
    for s in signals:
        cls = cfg.signal_class(s["type"])
        if not cls:
            continue
        key, spec = cls
        pts = sp["strength_points"].get(spec["strength"], 0)
        window = spec.get("window_days")
        if window is None:
            age_txt, factor = "current", 1.0
        else:
            if not s.get("observed_at"):
                continue                      # no date, cannot prove it is fresh
            seen = datetime.fromisoformat(str(s["observed_at"]).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            age = abs((today - seen).days)    # abs: trade shows can be upcoming
            if age > window:
                continue
            factor = 1 - (1 - sp["decay_floor"]) * (age / window)
            age_txt = f"{age}d"
        val = round(pts * factor)
        if val > best[0]:
            best = (val, f"{key.replace('_', ' ')}, {age_txt}")
    return best


def score_lead(cfg: Config, lead: dict, company: dict, signals: list[dict], suppressed: str | None,
               today: datetime) -> Score:
    sc = cfg.scoring
    fit_cfg, dq = sc["icp_fit"], sc["data_quality"]
    dnc = []

    # icp fit
    niche_text = " ".join(str(x or "") for x in (company.get("industry"), company.get("name"),
                                                 company.get("description"), company.get("naics_description")))
    niche, niche_pts, niche_why = match_niche(cfg, niche_text, company.get("naics"))
    hc = company.get("headcount")
    if hc is None:
        size_pts, size_why = 0, "size unknown"
    elif hc <= fit_cfg["size_max"]:
        size_pts, size_why = fit_cfg["size_ok"], company.get("size_range") or f"{hc}+"
    else:
        size_pts, size_why = 0, f"too big ({hc}+)"
    t_pts, t_why, is_dm = title_points(cfg, lead.get("title"))
    if is_dm is False:
        dnc.append(f"not a decision maker ({t_why})")
    fit = niche_pts + size_pts + t_pts

    # signal
    sig_pts, sig_why = signal_points(cfg, signals, today)

    # data quality
    v = lead.get("verify_status")
    if v is None:
        dnc.append("not verified yet")
    elif v == "fail":
        dnc.append(f"email failed checks: {lead.get('verify_reason')}")
    v_pts = dq["verify_ok"] if v == "ok" else dq["verify_risky"] if v == "risky" else 0
    edom, cdom = norm_domain(lead.get("email")), norm_domain(company.get("domain"))
    dm_match = bool(cdom) and (edom == cdom or edom.endswith("." + cdom))
    data = v_pts + (dq["domain_match"] if dm_match else 0) + (dq["decision_maker"] if is_dm else 0)

    # geo
    cc = country_code(lead.get("country")) or country_code(company.get("country"))
    g = sc["geo"]
    if cc in g["dnc"]:
        dnc.append(f"DACH ({cc}) is off")
    geo = g["points"] if cc in g["allowed"] else 0
    geo_why = cc or "country unknown"

    if suppressed:
        dnc.append(suppressed)

    total = fit + sig_pts + data + geo
    tiers = sc["tiers"]
    tier = ("A" if total >= tiers["A"] else "B" if total >= tiers["B"] else "C" if total >= tiers["C"] else "dnc")
    if dnc:
        tier = "dnc"
    reason = (f"fit {fit} ({niche_why}, {size_why}, {t_why}) + signal {sig_pts} ({sig_why})"
              f" + data {data} (email {v or 'unverified'}{', domain match' if dm_match else ''})"
              f" + geo {geo} ({geo_why})")
    if dnc:
        reason = "DNC: " + "; ".join(dnc) + " | " + reason
    return Score(total, tier, reason, niche,
                 {"fit": fit, "signal": sig_pts, "data": data, "geo": geo}, dnc)


def _company_view(conn, company_id) -> dict:
    if company_id is None:
        return {}
    c = dict(conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone())
    for r in conn.execute("SELECT field, value FROM field_values WHERE entity='company' AND entity_id=?"
                          " AND field IN ('description','naics','naics_description','size_range')"
                          " ORDER BY id", (company_id,)):
        if c.get(r["field"]) in (None, ""):
            c[r["field"]] = r["value"]
    return c


def score_all(conn, cfg: Config, today: datetime | None = None) -> dict:
    today = today or datetime.now(timezone.utc)
    counts = {"A": 0, "B": 0, "C": 0, "dnc": 0}
    ts = now()
    for lead in [dict(r) for r in conn.execute("SELECT * FROM leads").fetchall()]:
        company = _company_view(conn, lead["company_id"])
        sigs = [dict(r) for r in conn.execute(
            "SELECT * FROM signals WHERE active=1 AND (lead_id=? OR company_id=?)",
            (lead["id"], lead["company_id"]))]
        sup = suppression.check(conn, lead["email"], company.get("domain"))
        s = score_lead(cfg, lead, company, sigs, sup, today)
        counts[s.tier] += 1
        conn.execute("UPDATE leads SET score=?, tier=?, score_reason=?, niche=?, scored_at=?, updated_at=? WHERE id=?",
                     (s.total, s.tier, s.reason, s.niche, ts, ts, lead["id"]))
        log_event(conn, "scored", "lead", lead["id"], s.reason, score=s.total, tier=s.tier)
    conn.commit()
    return counts
