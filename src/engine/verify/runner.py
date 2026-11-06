"""runs a Verifier over leads and stores the result with provenance."""
import json

from ..db import log_event, now


def verify_leads(conn, verifier, only_unverified: bool = True) -> dict:
    q = """SELECT l.id, l.email, l.email_status_source, c.domain AS company_domain
           FROM leads l LEFT JOIN companies c ON c.id = l.company_id"""
    if only_unverified:
        q += " WHERE l.verify_status IS NULL"
    counts = {"ok": 0, "risky": 0, "fail": 0}
    ts = now()
    for row in conn.execute(q).fetchall():
        r = verifier.verify(row["email"], row["company_domain"], row["email_status_source"])
        counts[r.status] = counts.get(r.status, 0) + 1
        conn.execute("UPDATE leads SET verify_status=?, verify_reason=?, verified_at=?, updated_at=? WHERE id=?",
                     (r.status, r.reason, ts, ts, row["id"]))
        conn.execute(
            "INSERT INTO field_values (entity, entity_id, field, value, source, source_ref, observed_at, fetched_at)"
            " VALUES ('lead', ?, 'verify_status', ?, ?, ?, ?, ?)",
            (row["id"], r.status, verifier.name, json.dumps(r.checks), ts, ts))
        log_event(conn, "verified", "lead", row["id"], r.reason, status=r.status)
    conn.commit()
    return counts
