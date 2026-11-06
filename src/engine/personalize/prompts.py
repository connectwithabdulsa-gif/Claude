"""prompt building. the system prompt is static (rules + library) so it caches.
everything lead specific goes in the user message as json."""
import json
import re
from pathlib import Path

from ..paths import ROOT

VARIANTS = {
    "v1": "start with 'hey {first_name},' on its own line. use the saw line close to how it is given.",
    "v2": "start with '{first_name},' and fold the saw line into the same first sentence.",
    "v3": "no greeting line. open straight with the saw line. aim for 55 to 65 words.",
    "v4": "start with 'hey {first_name},'. put the why it matters part as a short fragment. aim for 70 to 85 words.",
}


def _section(md: str, number: int) -> str:
    m = re.search(rf"^## {number}\. .*?(?=^## \d+\. |\Z)", md, re.S | re.M)
    return m.group(0).strip() if m else ""


def system_prompt(root: Path = ROOT) -> str:
    rules = (root / "CLAUDE.md").read_text()
    library = (root / "message-library.md").read_text()
    return "\n\n".join([
        "you write first cold emails for abdul, founder of DFY GTM Systems. you output json only, matching the schema.",
        "hard rules:\n"
        "- use only the facts in the `facts` list. never add a fact, number, client name, timeline or claim that is not there.\n"
        "- list the id of every fact you used in `facts_used`.\n"
        "- if `signal` is null, write the plain fallback email from the library (no signal line).\n"
        "- the body has four parts: what we saw and why now (one sentence), why it matters (one short sentence), "
        "who i am and what i do (one or two sentences, plain words), one ask (a question they can answer in one word).\n"
        "- the ask is the free one page map unless `ask` says otherwise. exactly one question mark in the body.\n"
        "- 50 to 90 words in the body. no links, no sign off, no footer. the code adds 'abdul' and the footer.\n"
        "- follow the `variant` instruction for shape. vary sentence length. do not copy the library examples word for word.\n"
        "- lowercase sentences. capitalize first names, company names and tool names only.",
        "# writing rules (from CLAUDE.md)\n" + _section(rules, 7),
        "# proof points (from CLAUDE.md)\n" + (re.search(r"### Proof points.*?(?=^## )", rules, re.S | re.M) or [""])[0],
        "# message library\n" + library,
    ])


def qa_system_prompt(root: Path = ROOT) -> str:
    rules = (root / "CLAUDE.md").read_text()
    return "\n\n".join([
        "you check a cold email for abdul before it is sent. output json only, matching the schema. be strict. "
        "a fail needs a one line reason that says exactly what is wrong.",
        "run these 7 checks:\n"
        "1. would a person read this in 10 seconds and know why they got it?\n"
        "2. does it say what we saw and why now, using the given signal? (if `signal` is null, pass when it reads as the plain fallback)\n"
        "3. is there exactly one ask?\n"
        "4. is the body 90 words or fewer?\n"
        "5. any banned word or pattern from the rules below, or anything that sounds machine written?\n"
        "6. could this email be sent to anyone without changes? if yes, fail.\n"
        "7. is every fact in the email backed by an entry in `facts`? any claim, number or name not in `facts` is a fail.",
        "# writing rules (from CLAUDE.md)\n" + _section(rules, 7),
    ])


WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "facts_used": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "body", "facts_used"],
    "additionalProperties": False,
}

QA_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "pass": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["id", "pass", "reason"], "additionalProperties": False}},
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
    },
    "required": ["checks", "verdict"],
    "additionalProperties": False,
}


def writer_input(ctx: dict, variant: str, use_signal: bool) -> str:
    payload = {
        "lead": {"first_name": ctx["first_name"], "title": ctx["title"]},
        "company": ctx["company"],
        "niche": ctx["niche"],
        "signal": ctx["signal"] if use_signal else None,
        "facts": ctx["facts"] if use_signal else [f for f in ctx["facts"] if f["id"] != "signal"],
        "ask": "one page map",
        "variant": {"id": variant, "instruction": VARIANTS[variant].format(first_name=ctx["first_name"] or "there")},
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def qa_input(ctx: dict, email: dict, use_signal: bool) -> str:
    return json.dumps({"email": {"subject": email["subject"], "body": email["body"]},
                       "signal": ctx["signal"] if use_signal else None,
                       "facts": ctx["facts"] if use_signal else [f for f in ctx["facts"] if f["id"] != "signal"]},
                      ensure_ascii=False, sort_keys=True)
