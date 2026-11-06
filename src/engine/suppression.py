"""suppression list. checked on import and again right before every send."""
import csv
from pathlib import Path

from .db import log_event, now
from .normalize import norm_domain, norm_email


def add(conn, type_: str, value: str, reason: str) -> bool:
    value = norm_email(value) if type_ == "email" else norm_domain(value)
    if not value:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO suppression (type, value, reason, added_at) VALUES (?,?,?,?)",
        (type_, value, reason, now()),
    )
    if cur.rowcount:
        log_event(conn, "suppression_added", "suppression", value, reason, type=type_)
    return bool(cur.rowcount)


def load_csv(conn, path: Path) -> int:
    """sync config/suppression.csv into the db. adds only, never removes."""
    if not path.exists():
        return 0
    added = 0
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            t = (row.get("type") or "").strip().lower()
            if t in ("email", "domain"):
                added += add(conn, t, row.get("value", ""), (row.get("reason") or "config").strip())
    return added


def check(conn, email: str, company_domain: str | None = None) -> str | None:
    """returns the reason if suppressed, else None.
    a domain entry also covers its subdomains (acme.com blocks mail.acme.com)."""
    email = norm_email(email)
    row = conn.execute(
        "SELECT reason FROM suppression WHERE type='email' AND value=?", (email,)
    ).fetchone()
    if row:
        return f"email suppressed: {row['reason']}"
    domains = {d for d in (norm_domain(email), norm_domain(company_domain)) if d}
    for d in domains:
        parts = d.split(".")
        candidates = [".".join(parts[i:]) for i in range(len(parts) - 1)]
        q = "SELECT value, reason FROM suppression WHERE type='domain' AND value IN (%s)" % \
            ",".join("?" * len(candidates))
        row = conn.execute(q, candidates).fetchone()
        if row:
            return f"domain suppressed ({row['value']}): {row['reason']}"
    return None
