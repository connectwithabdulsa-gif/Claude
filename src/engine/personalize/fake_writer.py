"""a deterministic stand in for the writer and QA models, for demos and tests.
it fills message-library style templates from the given facts only. every 7th
call writes a bad email on purpose so the QA and fallback path is exercised."""
import itertools

_counter = itertools.count(1)

WHO = {
    "v1": "i'm abdul. i build outbound systems in clay. it finds the right people, checks the data, writes to them and sends it, so you only take the calls.",
    "v2": "i set up outbound systems in clay that run without anyone babysitting them. leads get found, checked and written to on their own and replies come to you.",
    "v3": "i'm abdul. i build the system that finds the right people, writes to them and sends it. you take the calls.",
    "v4": "i'm abdul. i build outbound systems in clay for {plural}. research, writing and sending run on their own and replies come to you.",
}


def writer(p: dict) -> dict:
    n = next(_counter)
    first = p["lead"]["first_name"] or "there"
    company = p["company"]["name"]
    plural = (p.get("niche") or {}).get("plural") or "b2b teams"
    v = p["variant"]["id"]
    who = WHO[v].format(plural=plural)
    ask = f"want me to send a one page map of how it would run for {company}?"
    sig = p.get("signal")
    if sig:
        opener = {"v1": f"hey {first},\n\n", "v2": f"{first}, ", "v3": "", "v4": f"hey {first},\n\n"}[v]
        saw = sig["saw_line"] if v != "v2" else sig["saw_line"][0].lower() + sig["saw_line"][1:]
        body = f"{opener}{saw} {sig['matters_line']}\n\n{who}\n\n{ask}"
        used = ["company", "signal", "first_name"]
        subject = f"{company} outbound"
    else:
        body = (f"hey {first},\n\nif most of {company}'s clients come from referrals, outbound can sit next to that "
                f"and fill the gaps.\n\ni'm abdul. i build outbound systems in clay for {plural}. it finds the right "
                f"people, checks the data, writes to them and sends it. you take the calls.\n\n{ask}")
        used = ["company", "first_name", "niche"]
        subject = f"{company} outbound"
    if n % 7 == 0 and sig:                      # planted bad email
        body = body.replace("i'm abdul.", "i'm abdul. we leverage AI powered solutions to unlock growth!")
    return {"subject": subject, "body": body, "facts_used": used}


def qa(p: dict) -> dict:
    return {"checks": [{"id": i, "pass": True, "reason": "ok"} for i in range(1, 8)], "verdict": "pass"}
