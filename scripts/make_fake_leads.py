"""writes fake Clay style csv exports for demos and tests.
every domain ends in .test (reserved, can never be a real company)."""
import csv
import random
import sys
from pathlib import Path

FIRST = ["Jake", "Priya", "Marcus", "Sofia", "Liam", "Aisha", "Noah", "Emma", "Omar", "Chloe",
         "Ethan", "Zara", "Lucas", "Mia", "Daniel", "Hana", "Ryan", "Leah", "Owen", "Nina"]
LAST = ["Reed", "Patel", "Cole", "Lopez", "Hart", "Khan", "Byrne", "Walsh", "Ali", "Moore"]
NICHES = [
    ("amazon agency", "Marketing & Advertising", ["Founder", "CEO", "Head of Growth"]),
    ("saas", "Computer Software", ["Founder", "CEO", "Head of Growth"]),
    ("mortgage", "Financial Services", ["Owner", "Managing Partner"]),
    ("staffing", "Staffing & Recruiting", ["Founder", "Managing Partner", "Head of BD"]),
    ("msp", "IT Services", ["Founder", "CEO", "Sales Lead"]),
    ("freight", "Logistics & Supply Chain", ["Owner", "Head of Sales"]),
]
COUNTRIES = ["United States", "United States", "United Kingdom", "Australia", "Germany"]
HEADERS = ["First Name", "Last Name", "Job Title", "Work Email", "Email Status", "LinkedIn Profile",
           "Company Name", "Company Domain", "Industry", "Employee Count", "Country",
           "Job Openings"]


def lead(rng, i):
    niche, industry, titles = NICHES[i % len(NICHES)]
    first, last = rng.choice(FIRST), rng.choice(LAST)
    slug = f"{niche.split()[0]}co{i}"
    domain = f"{slug}.test"
    return {
        "First Name": first, "Last Name": last, "Job Title": rng.choice(titles),
        "Work Email": f"{first.lower()}@{domain}", "Email Status": rng.choice(["valid", "valid", "catch-all"]),
        "LinkedIn Profile": f"https://linkedin.com/in/{first.lower()}-{last.lower()}-{i}",
        "Company Name": f"{slug.title()} Ltd", "Company Domain": f"https://www.{domain}/",
        "Industry": industry, "Employee Count": rng.choice(["2-10", "11-50", "51-200"]),
        "Country": rng.choice(COUNTRIES), "Job Openings": rng.choice(["", "SDR", "Account Executive"]),
    }


def build_main(rng):
    rows = [lead(rng, i) for i in range(40)]
    # planted problems for the verify/score demo
    rows[6]["Work Email"] = "info@" + rows[6]["Work Email"].split("@")[1]           # role mailbox
    rows[7]["Work Email"] = rows[7]["First Name"].lower() + "@gmail.com"            # personal address
    rows[8]["Job Title"] = "Marketing Coordinator"                                  # not a decision maker
    rows[9]["Work Email"] = rows[9]["First Name"].lower() + "@other-company.test"   # domain mismatch
    rows[10]["Employee Count"] = "1001-5000"                                        # too big
    for r in rows[11:15]:
        r["Email Status"] = ""                                                      # no source status
    # 4 in-file duplicates (same email, different case and spacing)
    for src in rows[:4]:
        d = dict(src)
        d["Work Email"] = "  " + src["Work Email"].upper() + " "
        rows.append(d)
    # 2 rows with no email
    for i in (41, 42):
        d = lead(rng, i); d["Work Email"] = ""; rows.append(d)
    # 2 people at a competitor domain (suppress rival-outbound.test in the demo)
    for name in ("Kai", "Rosa"):
        d = lead(rng, 43)
        d.update({"First Name": name, "Work Email": f"{name.lower()}@rival-outbound.test",
                  "Company Domain": "rival-outbound.test", "Company Name": "Rival Outbound"})
        rows.append(d)
    # 1 existing client email (suppress in the demo) + 1 second person at an existing company
    d = lead(rng, 44); d["Work Email"] = "client@happyclient.test"; d["Company Domain"] = "happyclient.test"
    rows.append(d)
    d = dict(rows[5]); d["First Name"] = "Second"; d["Work Email"] = "second@" + rows[5]["Work Email"].split("@")[1]
    rows.append(d)
    rng.shuffle(rows)
    return rows


def build_second(rng, main_rows):
    """10 rows: 5 already imported, 5 new."""
    seen = [r for r in main_rows if r["Work Email"].strip()][:5]
    return [dict(r) for r in seen] + [lead(rng, 100 + i) for i in range(5)]


def write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader(); w.writerows(rows)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    main = build_main(rng)
    write(out / "clay_export_1.csv", main)
    write(out / "clay_export_2.csv", build_second(rng, main))
    print(f"wrote {len(main)} rows to {out/'clay_export_1.csv'} and 10 rows to {out/'clay_export_2.csv'}")
