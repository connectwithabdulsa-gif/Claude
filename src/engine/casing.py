"""normal sentence case for everything that goes out: first letter of each sentence and line
capitalized, "i" -> "I". names and companies keep their own caps. links, emails and domains
are left exactly as they are (abdul: "dont send small letters messages")."""
import re

_I_WORDS = re.compile(r"(?<![\w@/.'’-])i(?=(?:['’](?:m|ll|ve|d))?(?![\w@/-])(?!\.\w))")
_START = re.compile(r"(^|[.?!]\s+|\n\s*)([a-z][^\s]*)")


def _is_link(word: str) -> bool:
    w = word.rstrip(".,;:?!)")
    return "://" in w or "@" in w or w.startswith("www.") or bool(re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", w))


_OURS = [(re.compile(r"\bdfy gtm systems\b", re.I), "DFY GTM Systems"),
         (re.compile(r"(?<![@\w.])abdul(?![@\w])", re.I), "Abdul")]


def sentence_case(text: str | None) -> str | None:
    if not text:
        return text
    text = _I_WORDS.sub("I", text)
    for rx, fixed in _OURS:
        text = rx.sub(fixed, text)

    def up(m):
        lead, word = m.group(1), m.group(2)
        return m.group(0) if _is_link(word) else lead + word[0].upper() + word[1:]

    return _START.sub(up, text)
