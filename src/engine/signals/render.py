"""turn a stored signal into the saw / matters lines. only uses stored data.
if a template needs a var the signal does not have, the line is not produced."""
import json
import re
import string
from datetime import datetime

SALES_WORDS = re.compile(r"\b(sdr|bdr|account executive|ae|sales|business development|growth|demand gen|revenue)\b", re.I)
LEGAL_SUFFIX = re.compile(r"[,\s]+(ltd\.?|limited|llc|l\.l\.c\.|inc\.?|incorporated|corp\.?|corporation|plc|pty\.? ltd\.?|pty|gmbh|co\.?)$", re.I)
_AN_ACRONYM = set("AEFHILMNORSX")


def clean_company(name: str | None) -> str | None:
    if not name:
        return None
    out = name.strip()
    for _ in range(2):
        out = LEGAL_SUFFIX.sub("", out).strip()
    return out or None


def a_or_an(phrase: str) -> str:
    first = phrase.split()[0]
    if first.isupper() and len(first) <= 5:       # acronym: an SDR, a BDR
        art = "an" if first[0] in _AN_ACRONYM else "a"
    else:
        art = "an" if first[0].lower() in "aeiou" else "a"
    return f"{art} {phrase}"


def _pick_title(titles) -> str | None:
    titles = [t.strip() for t in (titles or []) if t and t.strip()]
    for t in titles:
        if SALES_WORDS.search(t):
            return t
    return titles[0] if titles else None


def _role(t: str) -> str:
    """keep acronyms (SDR), lowercase the rest."""
    return " ".join(w if (w.isupper() and len(w) <= 5) else w.lower() for w in t.split())


def timeframe(observed_at: str | None, today: datetime) -> str | None:
    if not observed_at:
        return None
    seen = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    return "this month" if (seen.year, seen.month) == (today.year, today.month) else "recently"


def template_vars(signal: dict, company: dict, lead: dict, today: datetime) -> dict:
    d = signal.get("data")
    if d is None:
        try:
            d = json.loads(signal.get("data_json") or "{}")
        except ValueError:
            d = {}
    v = {"company": clean_company(company.get("name")), "first_name": lead.get("first_name") or None,
         "timeframe": timeframe(signal.get("observed_at"), today)}
    title = _pick_title(d.get("job_titles")) or d.get("job_role_title") or d.get("current_job_title")
    if title:
        v["role_title"] = _role(title)
        v["a_role_title"] = a_or_an(v["role_title"])
    stage = d.get("funding_stage") or d.get("founding_stage")
    if stage:
        stage = stage.strip()
        # "seed" / "pre-seed" lowercase, named rounds keep caps (Series A)
        v["round"] = stage.lower() if stage.lower() in ("seed", "pre-seed", "angel", "bridge") else stage
        v["a_round"] = a_or_an(v["round"])
    if d.get("office_location"):
        v["place"] = d["office_location"]
    if d.get("product_name"):
        v["product"] = d["product_name"]
    if d.get("partner_company"):
        v["partner"] = d["partner_company"]
    if d.get("tool"):
        v["tool"] = d["tool"]
    if d.get("show"):
        v["show"] = d["show"]
    return {k: val for k, val in v.items() if val}


def fill(template: str | None, vars_: dict) -> str | None:
    if not template:
        return None
    needed = {f for _, f, _, _ in string.Formatter().parse(template) if f}
    if not needed <= vars_.keys():
        return None
    return template.format(**vars_)


def is_same_person(signal_data: dict, lead: dict) -> bool:
    name = (signal_data.get("full_name") or "").strip().lower()
    mine = f"{lead.get('first_name') or ''} {lead.get('last_name') or ''}".strip().lower()
    return bool(name) and name == mine
