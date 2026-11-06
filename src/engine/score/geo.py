"""country text -> iso alpha-2 (only the ones scoring cares about)."""

_MAP = {
    "us": "us", "usa": "us", "u.s.": "us", "u.s.a.": "us", "united states": "us", "united states of america": "us", "america": "us",
    "gb": "gb", "uk": "gb", "u.k.": "gb", "united kingdom": "gb", "great britain": "gb", "england": "gb",
    "scotland": "gb", "wales": "gb", "northern ireland": "gb", "britain": "gb",
    "au": "au", "australia": "au",
    "de": "de", "germany": "de", "deutschland": "de",
    "at": "at", "austria": "at", "österreich": "at", "oesterreich": "at",
    "ch": "ch", "switzerland": "ch", "schweiz": "ch", "suisse": "ch", "svizzera": "ch",
    "ca": "ca", "canada": "ca", "ie": "ie", "ireland": "ie", "nz": "nz", "new zealand": "nz",
    "fr": "fr", "france": "fr", "nl": "nl", "netherlands": "nl", "in": "in", "india": "in",
}


def country_code(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().lower()
    if t in _MAP:
        return _MAP[t]
    # "london, united kingdom" style
    for part in reversed([p.strip() for p in t.split(",")]):
        if part in _MAP:
            return _MAP[part]
    return None
