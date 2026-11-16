"""engine deliverability: DNS health of a sending domain before any live send.

record values come from Spaceship's knowledgebase (read 2026-11-16):
  SPF   TXT @          v=spf1 include:spf.spacemail.com ~all
        https://www.spaceship.com/knowledgebase/spacemail-dns-setup-namecheap/
  DKIM  TXT spacemail._domainkey   v=DKIM1; k=rsa; p=...
        https://www.spaceship.com/knowledgebase/setup-dkim-record-spacemail/
  DMARC TXT _dmarc     v=DMARC1; ...
blocklists: Spamhaus DBL and SURBL, by DNS. a lookup that can't run is "unknown", never a pass.
"""
from dataclasses import dataclass, field

SPF_INCLUDE = "include:spf.spacemail.com"
DKIM_SELECTOR = "spacemail"
BLOCKLISTS = ["dbl.spamhaus.org", "multi.surbl.org"]


class DnsUnavailable(Exception):
    pass


def _resolver(timeout: float = 5.0):
    """returns a lookup(name, rtype) -> list[str] | None (None = name exists but no record / NXDOMAIN)."""
    import dns.exception
    import dns.resolver

    def lookup(name: str, rtype: str):
        try:
            ans = dns.resolver.resolve(name, rtype, lifetime=timeout)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return None
        except (dns.resolver.NoNameservers, dns.exception.Timeout, dns.resolver.LifetimeTimeout) as e:
            raise DnsUnavailable(str(e)) from e
        out = []
        for r in ans:
            if rtype == "TXT":
                out.append(b"".join(r.strings).decode(errors="replace"))
            else:
                out.append(r.to_text())
        return out
    return lookup


@dataclass
class DomainReport:
    domain: str
    checks: dict = field(default_factory=dict)     # name -> (status ok|fail|unknown, detail)

    @property
    def ok(self) -> bool:
        """mx, spf, dkim and dmarc must be ok (unknown blocks: we never send on a guess). a blocklist
        lookup that can't run (common on public resolvers) is a warning; a real listing blocks."""
        core = all(self.checks.get(k, ("unknown",))[0] == "ok" for k in ("mx", "spf", "dkim", "dmarc"))
        return core and self.checks.get("blocklists", ("unknown",))[0] != "fail"

    def lines(self) -> list[str]:
        out = [f"{self.domain}: {'PASS' if self.ok else 'BLOCKED'}"]
        for name, (st, detail) in self.checks.items():
            out.append(f"  {name:<10} {st:<8} {detail}")
        return out


def check_domain(domain: str, lookup=None) -> DomainReport:
    lookup = lookup or _resolver()
    rep = DomainReport(domain)

    def run(name, fn):
        try:
            rep.checks[name] = fn()
        except DnsUnavailable as e:
            rep.checks[name] = ("unknown", f"dns lookup failed: {e}")

    def mx():
        recs = lookup(domain, "MX")
        return ("ok", ", ".join(recs)) if recs else ("fail", "no MX record, replies can't arrive")

    def spf():
        recs = [r for r in (lookup(domain, "TXT") or []) if r.lower().startswith("v=spf1")]
        if not recs:
            return "fail", f"no SPF record. add TXT @ 'v=spf1 {SPF_INCLUDE} ~all'"
        if len(recs) > 1:
            return "fail", "more than one SPF record (only one is allowed)"
        if SPF_INCLUDE not in recs[0].lower():
            return "fail", f"SPF has no {SPF_INCLUDE}: {recs[0]}"
        return "ok", recs[0]

    def dkim():
        recs = lookup(f"{DKIM_SELECTOR}._domainkey.{domain}", "TXT") or []
        good = [r for r in recs if "v=dkim1" in r.lower().replace(" ", "") and "p=" in r]
        return ("ok", f"{DKIM_SELECTOR}._domainkey found") if good else \
            ("fail", f"no DKIM at {DKIM_SELECTOR}._domainkey (Spacemail adds it on Spaceship DNS, else add it)")

    def dmarc():
        recs = [r for r in (lookup(f"_dmarc.{domain}", "TXT") or []) if r.lower().startswith("v=dmarc1")]
        return ("ok", recs[0]) if recs else ("fail", "no DMARC record. start with TXT _dmarc 'v=DMARC1; p=none'")

    def blocklists():
        hits, unknown = [], []
        for bl in BLOCKLISTS:
            try:
                recs = lookup(f"{domain}.{bl}", "A")
            except DnsUnavailable:
                unknown.append(bl)
                continue
            # 127.255.255.x answers mean the query itself was refused (e.g. public resolver), not a listing
            if recs and not all(r.startswith("127.255.255.") for r in recs):
                hits.append(f"{bl} ({', '.join(recs)})")
            elif recs:
                unknown.append(bl)
        if hits:
            return "fail", "listed on " + "; ".join(hits)
        if unknown:
            return "unknown", "could not check " + ", ".join(unknown)
        return "ok", "not listed on " + ", ".join(BLOCKLISTS)

    for name, fn in [("mx", mx), ("spf", spf), ("dkim", dkim), ("dmarc", dmarc), ("blocklists", blocklists)]:
        run(name, fn)
    return rep


def save(conn, rep: DomainReport) -> None:
    import json
    from ..db import now
    c = rep.checks
    st = lambda k: 1 if c.get(k, ("",))[0] == "ok" else 0
    conn.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (rep.domain,))
    conn.execute("""UPDATE domains SET spf_ok=?, dkim_ok=?, dmarc_ok=?, mx_ok=?, blocklisted=?, dns_report=?,
                    checked_at=? WHERE domain=?""",
                 (st("spf"), st("dkim"), st("dmarc"), st("mx"), c.get("blocklists", ("", ""))[1],
                  json.dumps({"ok": rep.ok, "checks": c}), now(), rep.domain))
