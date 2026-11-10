"""Pull local business leads from an OpenStreetMap extract (free, no API key).

Data source: Geofabrik state extracts (https://download.geofabrik.de/north-america/us.html).
License: OpenStreetMap data is ODbL. Credit "(c) OpenStreetMap contributors" wherever the
data is shown. Docs used: wiki.openstreetmap.org Key:craft, Tag:craft=plumber/hvac/roofer,
Key:contact:email, and the Overpass_API page (usage policy says commercial use should not hit
the public Overpass server, so we read downloaded extracts instead).

Usage:
    python scripts/osm_leads.py data/osm/texas-latest.osm.pbf --state TX --out data/osm/leads_tx.csv

Read only. Nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from urllib.parse import urlparse

import osmium

# tag key -> {tag value: niche label}
NICHES: dict[str, dict[str, str]] = {
    "craft": {
        "plumber": "plumber",
        "hvac": "hvac",
        "roofer": "roofer",
        "electrician": "electrician",
        "painter": "painter",
        "carpenter": "carpenter",
        "gardener": "landscaping",
        "builder": "general contractor",
        "tiler": "flooring and tile",
        "glaziery": "glass and windows",
        "window_construction": "glass and windows",
        "insulation": "insulation",
        "cleaning": "cleaning",
        "locksmith": "locksmith",
    },
    "shop": {"car_repair": "auto repair", "locksmith": "locksmith", "pest_control": "pest control"},
    "amenity": {"dentist": "dentist", "veterinary": "veterinarian"},
    "office": {"lawyer": "lawyer", "accountant": "accountant", "insurance": "insurance agent",
               "estate_agent": "real estate agent"},
    "healthcare": {"physiotherapist": "physical therapy", "chiropractor": "chiropractor"},
}

ROLE_PREFIXES = {"info", "sales", "hello", "contact", "office", "admin", "support", "service",
                 "team", "help", "mail", "enquiries", "inquiries", "booking", "bookings"}

FIELDS = ["company_name", "niche", "website", "domain", "phone", "email", "email_is_role",
          "street", "city", "state", "postcode", "opening_hours", "osm_url", "source", "pulled_on"]


def first(tags, *keys: str) -> str:
    for k in keys:
        v = tags.get(k)
        if v:
            return v.split(";")[0].strip()
    return ""


def to_domain(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def clean_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"+1 {digits[0:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else p


class Handler(osmium.SimpleHandler):
    def __init__(self, state: str, pulled_on: str):
        super().__init__()
        self.state, self.pulled_on, self.rows = state, pulled_on, []

    def _take(self, obj, kind: str) -> None:
        tags = obj.tags
        niche = ""
        for key, values in NICHES.items():
            v = tags.get(key)
            if v in values:
                niche = values[v]
                break
        if not niche:
            return
        name = tags.get("name")
        if not name:
            return
        website = first(tags, "website", "contact:website", "url")
        email = first(tags, "email", "contact:email").lower()
        local = email.split("@")[0] if "@" in email else ""
        self.rows.append({
            "company_name": name,
            "niche": niche,
            "website": website,
            "domain": to_domain(website),
            "phone": clean_phone(first(tags, "phone", "contact:phone", "contact:mobile")),
            "email": email,
            "email_is_role": "yes" if local in ROLE_PREFIXES else ("no" if email else ""),
            "street": " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x),
            "city": tags.get("addr:city", ""),
            "state": tags.get("addr:state", "") or self.state,
            "postcode": tags.get("addr:postcode", ""),
            "opening_hours": tags.get("opening_hours", ""),
            "osm_url": f"https://www.openstreetmap.org/{kind}/{obj.id}",
            "source": "OpenStreetMap via Geofabrik extract (ODbL)",
            "pulled_on": self.pulled_on,
        })

    def node(self, n):
        self._take(n, "node")

    def way(self, w):
        self._take(w, "way")


def dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        key = r["domain"] or re.sub(r"\D", "", r["phone"]) or (r["company_name"].lower(), r["city"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("--state", required=True, help="2 letter state code used when addr:state is missing")
    ap.add_argument("--out", required=True)
    ap.add_argument("--require", choices=["none", "phone", "website", "phone_or_website"],
                    default="phone_or_website")
    a = ap.parse_args()

    h = Handler(a.state.upper(), dt.date.today().isoformat())
    h.apply_file(a.pbf, locations=False)
    rows = dedupe(h.rows)
    if a.require == "phone":
        rows = [r for r in rows if r["phone"]]
    elif a.require == "website":
        rows = [r for r in rows if r["website"]]
    elif a.require == "phone_or_website":
        rows = [r for r in rows if r["phone"] or r["website"]]

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} leads -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
