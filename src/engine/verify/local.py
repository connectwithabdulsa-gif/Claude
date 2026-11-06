"""free email checks. no paid api, no mailbox ping.

ok    = the source already validated it ('valid') and every local check passed
risky = catch-all, no source status, or the mail server could not be checked
fail  = bad syntax, role mailbox, free/personal address, domain mismatch,
        source said invalid, or the domain has no mail server
"""
import re

from ..normalize import norm_domain, norm_email
from .base import FAIL, OK, RISKY, VerifyResult

EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")

ROLE_LOCALPARTS = {
    "info", "sales", "hello", "contact", "admin", "support", "team", "office", "marketing", "enquiries",
    "inquiries", "help", "billing", "accounts", "careers", "jobs", "hr", "press", "media", "noreply",
    "no-reply", "mail", "webmaster", "postmaster", "abuse", "service", "customerservice", "bookings",
    "partners", "growth", "bd", "biz", "general", "reception",
}
FREE_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "outlook.com", "hotmail.com",
    "hotmail.co.uk", "live.com", "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "gmx.com", "gmx.de", "mail.com", "zoho.com", "yandex.com", "bigpond.com",
    "btinternet.com", "sky.com", "virginmedia.com", "comcast.net", "att.net", "verizon.net",
}
SOURCE_OK = {"valid", "verified", "deliverable", "ok"}
SOURCE_RISKY = {"catch-all", "catch_all", "catchall", "accept_all", "accept-all", "unknown", "risky", "extrapolated", "guessed"}
SOURCE_FAIL = {"invalid", "undeliverable", "bounced", "bad", "unavailable"}


class DnsMx:
    """MX lookup with dnspython. returns True (has mx), False (none), None (could not check)."""

    def __init__(self, timeout: float = 4.0):
        self.timeout = timeout
        self._cache: dict[str, bool | None] = {}

    def __call__(self, domain: str) -> bool | None:
        if domain in self._cache:
            return self._cache[domain]
        try:
            import dns.exception
            import dns.resolver
            try:
                ans = dns.resolver.resolve(domain, "MX", lifetime=self.timeout)
                out = len(ans) > 0
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                out = False
            except (dns.resolver.NoNameservers, dns.exception.Timeout, dns.resolver.LifetimeTimeout):
                out = None
        except Exception:  # noqa: BLE001  dns not reachable from this machine
            out = None
        self._cache[domain] = out
        return out


class LocalVerifier:
    name = "local_verifier"

    def __init__(self, mx_lookup=None, offline: bool = False):
        self.mx = (lambda d: None) if offline else (mx_lookup or DnsMx())

    def verify(self, email, company_domain=None, source_status=None) -> VerifyResult:
        email = norm_email(email)
        r = VerifyResult(status=OK)
        fail = lambda why, key: (r.reasons.append(why), r.checks.__setitem__(key, "fail"))

        if not EMAIL_RE.match(email):
            fail("bad email syntax", "syntax")
            r.status = FAIL
            return r
        r.checks["syntax"] = "ok"
        local, edom = email.split("@", 1)

        if local.split("+")[0] in ROLE_LOCALPARTS:
            fail(f"role mailbox ({local}@)", "role")
        else:
            r.checks["role"] = "ok"
        if edom in FREE_DOMAINS:
            fail(f"personal address ({edom})", "free_domain")
        else:
            r.checks["free_domain"] = "ok"

        cdom = norm_domain(company_domain)
        if cdom and not (edom == cdom or edom.endswith("." + cdom) or cdom.endswith("." + edom)):
            fail(f"email domain {edom} does not match company {cdom}", "domain_match")
        else:
            r.checks["domain_match"] = "ok" if cdom else "no company domain"

        src = (source_status or "").strip().lower()
        if src in SOURCE_FAIL:
            fail(f"source says {src}", "source_status")

        if r.reasons:            # any hard fail so far
            r.status = FAIL
            return r

        mx = self.mx(edom)
        r.checks["mx"] = {True: "ok", False: "none", None: "unchecked"}[mx]
        if mx is False:
            r.reasons.append(f"{edom} has no mail server")
            r.status = FAIL
            return r

        risky = []
        if src in SOURCE_OK:
            r.checks["source_status"] = src
        elif src in SOURCE_RISKY:
            risky.append(f"source says {src}")
            r.checks["source_status"] = src
        else:
            risky.append("no source verification" if not src else f"unknown source status '{src}'")
            r.checks["source_status"] = src or "none"
        if mx is None and src not in SOURCE_OK:
            risky.append("mail server not checked")
        if not cdom:
            risky.append("no company domain to match")
        if risky:
            r.status = RISKY
            r.reasons = risky
        return r
