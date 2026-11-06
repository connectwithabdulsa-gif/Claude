"""adds FAKE signals to a demo database so scoring has something to rank.
source is 'demo_fake' so they can never be mistaken for real data.
usage: ENGINE_DB=data/demo/demo.db python scripts/demo_signals.py"""
from datetime import datetime, timedelta, timezone

from engine.db import connect, now
from engine.paths import db_path

PLAN = [  # (raw signal type, age in days, value) cycled over companies
    ("hiring_in_sales_department", 5, "2 open roles: SDR, Account Executive"),
    ("new_funding_round", 20, "Seed $2,000,000"),
    ("hiring_in_sales_department", 28, "1 open role: BDR"),
    ("hiring_in_sales_department", 45, "1 open role: SDR"),          # stale (window 30)
    ("new_office", 10, "new office in Austin"),
    ("executive_joined_company", 15, "new head of sales"),
    (None, None, None),                                              # no signal
    (None, None, None),
]

conn = connect(db_path())
today = datetime.now(timezone.utc)
n = 0
for i, row in enumerate(conn.execute("SELECT id FROM companies ORDER BY id").fetchall()):
    typ, age, val = PLAN[i % len(PLAN)]
    if not typ:
        continue
    conn.execute(
        "INSERT INTO signals (company_id, type, value, source, source_field, observed_at, detected_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (row["id"], typ, val, "demo_fake", "demo", (today - timedelta(days=age)).isoformat(), now()))
    n += 1
conn.commit()
print(f"added {n} fake signals")
