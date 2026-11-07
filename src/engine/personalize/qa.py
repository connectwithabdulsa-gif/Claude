"""deterministic QA. runs before and alongside the LLM QA pass. every fail has a reason."""
import re
from pathlib import Path

import yaml

EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F000-\U0001F2FF☀-⛿]")
LINK = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|io|ai|co|net|org)\b)", re.I)


def load_rules(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.-]*", text))


def check(email: dict, ctx: dict, rules: dict, use_signal: bool) -> list[str]:
    """returns a list of fail reasons. empty = pass."""
    fails = []
    body, subject = email.get("body") or "", email.get("subject") or ""
    low = body.lower()

    n = words(body)
    if not rules["email1_words"]["min"] <= n <= rules["email1_words"]["max"]:
        fails.append(f"body is {n} words, needs {rules['email1_words']['min']} to {rules['email1_words']['max']}")
    for p in rules["banned_phrases"]:
        if re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low):
            fails.append(f"banned phrase '{p}'")
    for pat in rules.get("banned_patterns") or []:
        if re.search(pat, low):
            fails.append(f"banned pattern /{pat}/")
    if "—" in body or "–" in body or "—" in subject:
        fails.append("em or en dash")
    if "!" in body or "!" in subject:
        fails.append("exclamation mark")
    if EMOJI.search(body + subject):
        fails.append("emoji")
    if LINK.search(body):
        fails.append("link in email 1")
    q = body.count("?")
    if q != 1:
        fails.append(f"{q} question marks, needs exactly one ask")
    first_sentence = re.split(r"(?<=[.?])\s", body.strip(), maxsplit=1)[0]
    if first_sentence.endswith("?") and q:
        fails.append("opens with a question")
    if re.search(r":\s", body):
        fails.append("colon in body (dramatic colon)")
    if "abdul sami" in low or "sami" in low.split():
        fails.append("full name used, first name only")
    if re.search(r"\babdul\s*$", body.strip(), re.I) or "not for you?" in low:
        fails.append("body includes the sign off or footer, code adds it")

    # capitals only for names
    allowed = {w.lower() for w in rules.get("proper_nouns") or []}
    for name in (ctx.get("first_name"), (ctx.get("company") or {}).get("name")):
        for w in (name or "").split():
            allowed.add(w.lower().strip(",."))
    for f in ctx.get("facts") or []:
        for w in re.findall(r"\b[A-Z][A-Za-z0-9]*\b", f.get("text", "")):
            allowed.add(w.lower())
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9'’]*\b", body):
        w = re.sub(r"['’]s$", "", m.group(0))   # possessive: Acme's -> Acme
        if re.fullmatch(r"I(['’](m|ll|ve|d))?", w):
            continue
        before = body[:m.start()].rstrip(" \t")
        if not before or before[-1] in ".?!\n":   # sentence case: first word of a sentence or line
            continue
        if w.lower() not in allowed:
            fails.append(f"capitalized word '{w}' is not a name")
            break

    # subject
    sl = subject.lower()
    sw = len(subject.split())
    if not rules["subject"]["min_words"] <= sw <= rules["subject"]["max_words"]:
        fails.append(f"subject has {sw} words")
    if len(subject) > rules["subject"]["max_chars"]:
        fails.append(f"subject is {len(subject)} chars")
    for p in rules["banned_subject_phrases"]:
        if p in sl:
            fails.append(f"subject contains '{p}'")
    if subject.isupper() and len(subject) > 3:
        fails.append("subject in all caps")

    # could be sent to anyone? must name the company, and the signal detail if one is used
    company = (ctx.get("company") or {}).get("name") or ""
    if company and company.lower() not in low:
        fails.append(f"does not mention {company}")
    if use_signal and ctx.get("signal"):
        keys = [k.lower() for k in ctx["signal"].get("key_terms") or []]
        if keys and not any(k in low for k in keys):
            fails.append(f"signal detail missing (expected one of {keys})")

    # facts trace to provided ids
    ids = {f["id"] for f in ctx.get("facts") or []} if use_signal else \
          {f["id"] for f in ctx.get("facts") or [] if f["id"] != "signal"}
    unknown = [i for i in email.get("facts_used") or [] if i not in ids]
    if unknown:
        fails.append(f"facts_used has ids that were not given: {unknown}")
    if use_signal and "signal" not in (email.get("facts_used") or []):
        fails.append("signal fact not marked as used")
    return fails


def check_followup(body: str, subject: str | None, rules: dict) -> list[str]:
    """lighter checks for steps 2 to 4: no word floor, at most one ask, same banned list."""
    fails, low = [], body.lower()
    for p in rules["banned_phrases"]:
        if re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", low):
            fails.append(f"banned phrase '{p}'")
    if "\u2014" in body or "\u2013" in body:
        fails.append("em or en dash")
    if "!" in body:
        fails.append("exclamation mark")
    if EMOJI.search(body) or LINK.search(body):
        fails.append("emoji or link")
    if body.count("?") > 1:
        fails.append("more than one ask")
    if words(body) > rules["email1_words"]["max"]:
        fails.append("too long")
    if subject is not None:
        for p in rules["banned_subject_phrases"]:
            if p in subject.lower():
                fails.append(f"subject contains '{p}'")
    return fails
