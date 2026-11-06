import re

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def norm_key(header: str) -> str:
    """header matching key: lowercase, no spaces, underscores or dashes."""
    return re.sub(r"[\s_\-]+", "", header.strip().lower())


def norm_email(value: str | None) -> str:
    return (value or "").strip().strip("<>").lower()


def norm_domain(value: str | None) -> str:
    """'https://www.Acme.com/about' -> 'acme.com'. also accepts an email."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "@" in v and "/" not in v:
        v = v.rsplit("@", 1)[1]
    v = _SCHEME.sub("", v)
    v = v.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].split(":", 1)[0]
    if v.startswith("www."):
        v = v[4:]
    return v.strip(".")


def parse_int(value) -> int | None:
    """'11-50' -> 11, '1,200' -> 1200, '' -> None. takes the low end of a range."""
    if value is None:
        return None
    m = re.search(r"\d[\d,]*", str(value))
    return int(m.group(0).replace(",", "")) if m else None
