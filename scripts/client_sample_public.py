"""Client sample: local business decision makers from free public US sources.

Standalone. Does not touch the outbound engine database. Sends nothing.
No emails are looked up. Costs 0 credits (both sources are free public APIs).

Sources (fields confirmed from live API responses, Nov 2026):
  dental: NPPES NPI Registry API v2.1, https://npiregistry.cms.hhs.gov/api/
          organization NPIs (NPI-2), taxonomy "Dentist". Decision maker =
          basic.authorized_official_* with authorized_official_title_or_position.
  hvac:   Texas Open Data, "TDLR - All Licenses" (dataset 7358-krk7),
          https://data.texas.gov/resource/7358-krk7.json, license_type "A/C Contractor".
          Decision maker = OWNER NAME field.

Not covered (no free source with a decision maker field found): roofing,
plumbing, personal injury law. No rating, review, website or ads field exists
in either source, so ads_status is always not_verified.

Usage:
  python scripts/client_sample_public.py            # dry run, prints the queries
  python scripts/client_sample_public.py --live     # pulls and writes the csv
"""
import argparse
import csv
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

PER_CATEGORY = 10
DENTAL_STATES = ["TX", "FL", "GA", "AZ", "NC"]   # spread the sample across states
DENTAL_MIN_LAST_UPDATED = "2022-01-01"           # skip stale NPI records
DM_TITLE = re.compile(r"\b(owner|founder|ceo|chief executive|president|managing partner)\b", re.I)
NOT_DM = re.compile(r"\bvice\b|\bvp\b|\bassistant\b", re.I)

NPI_URL = "https://npiregistry.cms.hhs.gov/api/"
TDLR_URL = "https://data.texas.gov/resource/7358-krk7.json"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "client_samples"
COLUMNS = ["business_name", "source_record_id", "category", "city", "state", "website", "domain",
           "business_phone", "decision_maker_name", "title", "linkedin_url", "ads_status",
           "source_per_field", "date_pulled"]


def get_json(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=60) as r:
        return json.load(r)


def fmt_phone(p):
    d = re.sub(r"\D", "", p or "")
    return f"({d[:3]}) {d[3:6]}-{d[6:10]}" if len(d) == 10 else ""


KEEP_UPPER = {"LLC", "PLLC", "PC", "P.C.", "PA", "P.A.", "P.A", "DDS", "DMD", "MS", "INC", "INC.", "A/C", "HVAC", "LLP", "LP", "II", "III", "IV"}


def title_case(s):
    out = []
    for w in (s or "").split():
        bare = w.rstrip(",")
        out.append(w.upper() if bare.upper() in KEEP_UPPER else w.capitalize())
    return " ".join(out)


SUFFIX = re.compile(r"\b(JR|SR|II|III|IV)\.?\b", re.I)


def person_from_last_first(raw):
    """TDLR stores 'LAST, FIRST MIDDLE SUFFIX'. returns 'First Middle Last Suffix'."""
    last, _, first = raw.partition(",")
    suffixes = SUFFIX.findall(first)
    first = re.sub(r"\s+", " ", SUFFIX.sub("", first)).strip()
    sfx = " ".join(x.capitalize() if x.upper() in ("JR", "SR") else x.upper() for x in suffixes)
    return " ".join(p for p in [title_case(first), title_case(last.strip()), sfx] if p)


def dental_queries():
    return [(NPI_URL, {"version": "2.1", "enumeration_type": "NPI-2", "taxonomy_description": "Dentist",
                       "state": st, "limit": 200, "skip": 0}) for st in DENTAL_STATES]


def pull_dental(today, skips):
    rows, seen = [], set()
    per_state = -(-PER_CATEGORY // len(DENTAL_STATES))
    for url, params in dental_queries():
        taken = 0
        for r in get_json(url, params).get("results", []):
            b = r.get("basic", {})
            name = b.get("organization_name", "")
            title = (b.get("authorized_official_title_or_position") or "").strip()
            loc = next((a for a in r.get("addresses", []) if a.get("address_purpose") == "LOCATION"), {})
            if b.get("status") != "A":
                skips["dental: npi not active"] += 1; continue
            if (b.get("last_updated") or "") < DENTAL_MIN_LAST_UPDATED:
                skips["dental: npi record not updated since 2022"] += 1; continue
            if not DM_TITLE.search(title) or NOT_DM.search(title):
                skips["dental: authorized official is not owner/founder/ceo/president/managing partner"] += 1; continue
            if not fmt_phone(loc.get("telephone_number")):
                skips["dental: no business phone"] += 1; continue
            dm = title_case(f"{b.get('authorized_official_first_name','')} {b.get('authorized_official_last_name','')}")
            if name.lower() in seen or dm.lower() in seen:
                skips["dental: duplicate practice or same owner already listed"] += 1; continue
            if taken >= per_state:
                continue
            seen.update({name.lower(), dm.lower()}); taken += 1
            src = f"NPPES NPI Registry API v2.1, NPI {r['number']}, record last_updated {b.get('last_updated')}"
            rows.append({
                "business_name": title_case(name), "source_record_id": f"NPI {r['number']}",
                "category": "dental", "city": title_case(loc.get("city")), "state": loc.get("state", ""),
                "website": "", "domain": "", "business_phone": fmt_phone(loc.get("telephone_number")),
                "decision_maker_name": dm, "title": title_case(title), "linkedin_url": "",
                "ads_status": "not_verified",
                "source_per_field": f"business_name, city, state, business_phone, decision_maker_name, title: {src}",
                "date_pulled": today})
    return rows[:PER_CATEGORY]


def hvac_query():
    return (TDLR_URL, {"license_type": "A/C Contractor", "$limit": 2000, "$order": "license_number DESC",
                       "$where": "business_name IS NOT NULL AND owner_name IS NOT NULL AND business_county IS NOT NULL"})


def pull_hvac(today, skips):
    url, params = hvac_query()
    rows, seen_city, seen_name = [], Counter(), set()
    meta = get_json("https://data.texas.gov/api/views/7358-krk7.json", {})
    updated = datetime.fromtimestamp(meta.get("rowsUpdatedAt", 0), timezone.utc).strftime("%Y-%m-%d")
    for r in get_json(url, params):
        exp = r.get("license_expiration_date_mmddccyy", "")
        try:
            if datetime.strptime(exp, "%m/%d/%Y").date() < date.today():
                skips["hvac: license expired"] += 1; continue
        except ValueError:
            skips["hvac: no valid expiration date"] += 1; continue
        owner = person_from_last_first(r["owner_name"])
        biz = r["business_name"].strip()
        if title_case(biz) == owner or len(biz) < 3:
            skips["hvac: business name is just the person (sole licensee, not a company)"] += 1; continue
        city = title_case(r["business_county"]) + " County"   # TDLR gives county only, no city
        if biz.lower() in seen_name or seen_city[city] >= 2:   # spread across counties
            continue
        seen_name.add(biz.lower()); seen_city[city] += 1
        src = f"Texas Open Data, TDLR All Licenses (7358-krk7), license {r['license_number']}, dataset updated {updated}"
        rows.append({
            "business_name": title_case(biz), "source_record_id": f"TDLR {r['license_number']}",
            "category": "hvac", "city": city, "state": "TX",
            "website": "", "domain": "", "business_phone": "",
            "decision_maker_name": owner, "title": "Owner (TDLR license OWNER NAME field)", "linkedin_url": "",
            "ads_status": "not_verified",
            "source_per_field": f"business_name, city (county), decision_maker_name: {src}. state TX = TDLR is a Texas agency. phone not in this dataset.",
            "date_pulled": today})
        if len(rows) >= PER_CATEGORY:
            break
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="pull from the free public APIs and write the csv")
    args = ap.parse_args()
    if not args.live:
        print("DRY RUN. 0 credits. Would call:")
        for u, p in dental_queries() + [hvac_query()]:
            print(" ", u, p)
        return
    today = date.today().isoformat()
    skips = Counter()
    rows = pull_dental(today, skips) + pull_hvac(today, skips)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"local_business_sample_{today}.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader(); w.writerows(rows)
    print(f"wrote {len(rows)} leads to {out}")
    per = Counter(r["category"] for r in rows)
    for cat in ["roofing", "hvac", "plumbing", "dental", "personal injury law"]:
        print(f"  {cat}: {per.get(cat, 0)}")
    print("skipped records:")
    for k, v in skips.most_common():
        print(f"  {v}  {k}")
    print("credits used: 0 (free public sources)")


if __name__ == "__main__":
    sys.exit(main())
