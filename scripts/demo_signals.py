"""adds FAKE signals to a demo database so scoring and picking have something to work with.
source is 'demo_fake' so they can never be mistaken for real data. the data shapes copy
the Explorium event fields (developers.explorium.ai/v2/businesses/events/types).
usage: ENGINE_DB=data/demo/demo.db python scripts/demo_signals.py"""
import json
from datetime import datetime, timedelta, timezone

from engine.db import connect, now
from engine.paths import db_path

PLAN = [  # (raw type, age days, value, data) cycled over companies
    ("hiring_in_sales_department", 5, "2 open roles: SDR, Account Executive",
     {"job_titles": ["SDR", "Account Executive"], "job_count": 2}),
    ("new_funding_round", 20, "Seed $2,000,000", {"funding_stage": "Seed", "amount_raised": 2000000}),
    ("hiring_in_sales_department", 28, "1 open role: BDR", {"job_titles": ["BDR"], "job_count": 1}),
    ("hiring_in_sales_department", 45, "1 open role: SDR", {"job_titles": ["SDR"], "job_count": 1}),  # stale
    ("new_office", 10, "new office in Austin", {"office_location": "Austin"}),
    ("executive_joined_company", 15, "new head of sales",
     {"full_name": "Pat Doe", "job_role_title": "Head of Sales"}),
    ("new_product", 12, "launch", {}),                                   # no product_name: must fall back
    (None, None, None, None),                                            # no signal
]

conn = connect(db_path())
today = datetime.now(timezone.utc)
n = 0
for i, row in enumerate(conn.execute("SELECT id FROM companies ORDER BY id").fetchall()):
    typ, age, val, data = PLAN[i % len(PLAN)]
    if not typ:
        continue
    conn.execute(
        "INSERT INTO signals (company_id, type, value, source, source_field, observed_at, detected_at, data_json)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (row["id"], typ, val, "demo_fake", "demo", (today - timedelta(days=age)).isoformat(), now(), json.dumps(data)))
    n += 1
conn.commit()
print(f"added {n} fake signals")
