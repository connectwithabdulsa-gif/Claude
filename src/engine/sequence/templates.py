"""follow ups 2 to 4 (message-library.md section 4). each adds something new.
filled only from stored facts. email 3 uses the one allowed proof point."""
from ..signals.render import clean_company

STEP2 = ("{first_name}, here's how it would run for {company}.\n\n"
         "i'd find {target_description}, check the emails so nothing bounces, and write each message from something "
         "real about them. it sends from separate domains so {company}'s main one stays clean. replies come straight to you.\n\n"
         "want the one page map?")
STEP3 = ("{first_name}, we set this up for an amazon agency recently. leads found, enriched in clay, written and sent "
         "without anyone touching it.\n\n"
         "happy to show you how that setup worked and what would change for {company}. 15 minutes this week?")
STEP4 = ("{first_name}, i'll stop here so i don't clog your inbox.\n\n"
         "if outbound at {company} comes up later, just reply and i'll pick it up.")
STEP4_SUBJECT = "{company} pipeline"


def render(step: int, lead: dict, company: dict, niche: dict) -> tuple[str | None, str]:
    """returns (subject or None for same thread, body)."""
    v = {"first_name": lead.get("first_name") or "hey", "company": clean_company(company.get("name")) or company.get("domain"),
         "target_description": (niche or {}).get("target_description") or "the right people"}
    if step == 2:
        return None, STEP2.format(**v)
    if step == 3:
        return None, STEP3.format(**v)
    if step == 4:
        return STEP4_SUBJECT.format(**v), STEP4.format(**v)
    raise ValueError(step)
