"""DFY Sender: a local web app over the engine (Smartlead-style screens). binds to 127.0.0.1 only.
no sending logic lives here: every action calls the same engine code and gates as the CLI."""
import json
import os
import sqlite3
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from .. import suppression
from ..db import connect, log_event, now
from ..normalize import norm_domain
from ..paths import CONFIG_DIR, ROOT, db_path, load_dotenv
from ..send import dns_check, runner
from ..send import spacemail as sm
from ..sequence import scheduler as sch


JOB: dict = {}          # the csv being processed in the background (one at a time)


def _conn():
    return connect(db_path())         # read mostly. the suppression csv loads once at startup


def _sender_cfg(config_dir: Path) -> dict:
    return yaml.safe_load((config_dir / "sender.yaml").read_text()) or {}


def set_yaml_value(path: Path, key: str, value) -> None:
    """change one top level `key: value` line and keep every comment in the file."""
    text = path.read_text()
    line = f'{key}: {json.dumps(value)}'
    new, n = re.subn(rf"^{re.escape(key)}:.*$", line, text, count=1, flags=re.M)
    path.write_text(new if n else text.rstrip("\n") + "\n" + line + "\n")


def set_env_value(env_path: Path, key: str, value: str) -> None:
    """write KEY=value into .env (create it if needed). the value is never logged or shown."""
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    lines = [l for l in lines if not l.strip().startswith(f"{key}=")] + [f"{key}={value}"]
    env_path.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def ensure_mailbox(conn, sender_cfg: dict, address: str, cap: int) -> str:
    address = address.strip().lower()
    dom = norm_domain(address)
    if runner.is_override(sender_cfg, address):
        cap = min(cap, int(sender_cfg["main_domain_override"].get("max_per_day", 10)))
    elif dom in set(sender_cfg.get("main_domains") or []) or dom in ("gmail.com", "googlemail.com"):
        return f"{dom} is a main domain or gmail. use a secondary domain"
    conn.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (dom,))
    did = conn.execute("SELECT id FROM domains WHERE domain=?", (dom,)).fetchone()[0]
    if conn.execute("SELECT 1 FROM mailboxes WHERE address=?", (address,)).fetchone():
        conn.execute("UPDATE mailboxes SET fixed_cap=?, daily_cap=MAX(daily_cap, ?) WHERE address=?",
                     (cap, cap, address))
    else:
        conn.execute("INSERT INTO mailboxes (address, domain_id, daily_cap, live_from, fixed_cap) VALUES (?,?,?,?,?)",
                     (address, did, cap, date.today().isoformat(), cap))
    log_event(conn, "mailbox_saved_in_app", "mailbox", address, None, cap=cap)
    conn.commit()
    return f"mailbox {address} saved, {cap} a day"


def checklist(conn, cfg: dict) -> list[dict]:
    """every condition the sender needs, in plain words, so abdul sees why it is or isn't sending."""
    lv = cfg["sending"]["live"]
    at = datetime.now(timezone.utc)
    items = []
    addr = (cfg["sender"].get("physical_address") or "").strip()
    items.append({"ok": bool(addr) and addr != runner.PLACEHOLDER, "label": "Mailing address in the footer",
                  "fix": "Settings > mailing address"})
    mbs = [dict(r) for r in conn.execute("SELECT * FROM mailboxes ORDER BY id")]
    items.append({"ok": bool(mbs), "label": "Sending mailbox added", "fix": "Settings > mailbox"})
    for mb in mbs:
        why = runner.mailbox_gate(conn, cfg, mb, at)
        items.append({"ok": why is None, "label": f"{mb['address']} ready", "fix": why or ""})
    ready = conn.execute("""SELECT COUNT(*) FROM messages m JOIN leads l ON l.id=m.lead_id WHERE m.step=1
                            AND m.review='approved' AND l.id NOT IN (SELECT lead_id FROM sequence_state)""").fetchone()[0]
    items.append({"ok": ready > 0, "label": f"{ready} approved emails waiting to start", "fix": "Leads > upload a CSV"})
    items.append({"ok": not runner._p(lv["stop_file"]).exists() and runner._p(lv["ok_file"]).exists(),
                  "label": "Sending switched on", "fix": "press Start sending"})
    return items


def stats(conn, cfg: dict) -> dict:
    one = lambda q, *a: conn.execute(q, a).fetchone()[0] or 0
    today = date.today().isoformat()
    mbs = [dict(r) for r in conn.execute("SELECT * FROM mailboxes ORDER BY id")]
    cap = sum(sch.mailbox_cap(m, date.today(), cfg["sending"]["ramp"]) for m in mbs)
    last = conn.execute("SELECT outcome FROM sends WHERE status IN ('sent','failed') AND mode='live'"
                        " ORDER BY id DESC LIMIT 200").fetchall()
    bounce = round(sum(1 for r in last if r["outcome"] == "bounce") / len(last) * 100, 1) if last else 0.0
    days = []
    for i in range(13, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        days.append({"day": d[5:], "sent": one("SELECT COUNT(*) FROM sends WHERE status='sent' AND mode='live'"
                                               " AND date(sent_at)=?", d),
                     "replies": one("SELECT COUNT(*) FROM replies WHERE date(received_at)=? AND lead_id IS NOT NULL", d)})
    peak = max([d["sent"] for d in days] + [1])
    for d in days:
        d["h"] = int(d["sent"] / peak * 100)
    total_sent = one("SELECT COUNT(DISTINCT lead_id) FROM sends WHERE status='sent' AND mode='live' AND step=1")
    replied = one("SELECT COUNT(DISTINCT lead_id) FROM replies WHERE lead_id IS NOT NULL"
                  " AND COALESCE(category,'') NOT IN ('auto_bounce','out_of_office')")
    return {
        "sent_today": one("SELECT COUNT(*) FROM sends WHERE status='sent' AND mode='live' AND date(sent_at)=?", today),
        "queued": one("SELECT COUNT(*) FROM sends WHERE status='queued' AND mode='live'"),
        "cap": cap, "bounce_pct": bounce,
        "complaints": one("SELECT COUNT(*) FROM sends WHERE outcome='complaint'"),
        "replies": one("SELECT COUNT(*) FROM replies WHERE lead_id IS NOT NULL"),
        "reply_rate": round(replied / total_sent * 100, 1) if total_sent else 0.0,
        "people_contacted": total_sent,
        "in_sequence": one("SELECT COUNT(*) FROM sequence_state WHERE status='active'"),
        "leads": one("SELECT COUNT(*) FROM leads"),
        "mailboxes": mbs, "days": days,
        "events": [dict(r) for r in conn.execute("SELECT ts, action, entity_id, reason FROM events"
                                                  " ORDER BY id DESC LIMIT 20")],
    }


def create_app(config_dir: Path = CONFIG_DIR, root: Path = ROOT, worker=None) -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)            # new each start: sessions only live while the app runs
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
    load_dotenv(root / ".env")
    c0 = connect(db_path())
    suppression.load_csv(c0, config_dir / "suppression.csv")
    c0.commit()
    c0.close()

    @app.errorhandler(sqlite3.OperationalError)
    def busy(e):
        return ("<p style='font:16px system-ui;padding:40px'>Busy sending right now. Wait 10 seconds and "
                "<a href='javascript:location.reload()'>try again</a>.</p>", 503)

    def cfg():
        return sch.load_cfg(config_dir)

    @app.before_request
    def csrf():
        session.setdefault("csrf", secrets.token_hex(16))
        if request.method == "POST" and request.form.get("csrf") != session["csrf"]:
            abort(400, "form expired, reload the page")

    @app.context_processor
    def inject():
        return {"csrf": session.get("csrf", ""), "worker": worker, "nav": request.endpoint}

    @app.get("/")
    def dashboard():
        conn, c = _conn(), cfg()
        s, items = stats(conn, c), checklist(conn, c)
        conn.close()
        lv = c["sending"]["live"]
        on = runner._p(lv["ok_file"]).exists() and not runner._p(lv["stop_file"]).exists()
        return render_template("dashboard.html", s=s, items=items, on=on,
                               blockers=[i for i in items[:-1] if not i["ok"]],
                               next_in=max(0, int(worker.next_run - time.time())) if worker else None)

    @app.post("/start")
    def start():
        conn, c = _conn(), cfg()
        from .worker import refresh_dns
        refresh_dns(conn, dns_lookup=app.config.get("DNS_LOOKUP"))   # never "not checked" when you press Start
        blockers = [i for i in checklist(conn, c)[:-1] if not i["ok"] and "waiting to start" not in i["label"]]
        if blockers:
            conn.close()
            flash("Can't start yet: " + "; ".join(f"{b['label']} ({b['fix']})" for b in blockers), "bad")
            return redirect(url_for("dashboard"))
        lv = c["sending"]["live"]
        runner._p(lv["ok_file"]).parent.mkdir(parents=True, exist_ok=True)
        runner._p(lv["ok_file"]).write_text(f"approved in app {now()}\n")
        runner._p(lv["stop_file"]).unlink(missing_ok=True)
        log_event(conn, "go_live", None, None, "Start sending pressed in the app")
        conn.commit()
        conn.close()
        if worker:
            worker.run_now()
        flash("Sending is on. It sends inside each prospect's 9 to 5, spread through the day.", "good")
        return redirect(url_for("dashboard"))

    @app.post("/stop")
    def stop():
        lv = cfg()["sending"]["live"]
        runner._p(lv["stop_file"]).parent.mkdir(parents=True, exist_ok=True)
        runner._p(lv["stop_file"]).write_text("stopped in app\n")
        conn = _conn()
        log_event(conn, "kill_switch_on", None, None, "Stop pressed in the app")
        conn.commit()
        conn.close()
        flash("Stopped. Nothing will be sent until you press Start again.", "good")
        return redirect(url_for("dashboard"))

    @app.post("/run-now")
    def run_now():
        if worker:
            worker.run_now()
        flash("Checking now. Refresh in a few seconds.", "good")
        return redirect(url_for("dashboard"))

    def _process_upload(dest, source, only_agencies):
        """runs in the background so the page never hangs on a big csv (email checks take a while)."""
        from ..pipeline import build_batch, import_csv
        JOB.clear()
        JOB.update(running=True, file=dest.name, started=time.time(), step="importing rows", msg="", ok=True)
        try:
            conn = connect(db_path())
            r = import_csv(conn, config_dir, dest, source, only_agencies)
            JOB["step"] = f"checking {r['added']} email addresses and writing emails"
            b = build_batch(conn, config_dir)
            conn.close()
            w = b["written"]
            JOB["msg"] = (f"{r['rows']} rows read, {r['added']} new leads"
                          + (", filtered: " + ", ".join(f"{k} {v}" for k, v in r["filtered"].items()) if r["filtered"] else "")
                          + (", skipped: " + ", ".join(f"{k} {v}" for k, v in r["rejected"].items()) if r["rejected"] else "")
                          + (f". {r['prewritten']} people with your own 4 emails, approved" if r.get("prewritten") else "")
                          + (f". Emails written: {w['ready']} ready, {w['held']} held" if not r.get("prewritten") else "")
                          + f". Email check: {b['verify']['ok'] + b['verify']['risky']} ok, {b['verify']['fail']} bad (skipped).")
        except Exception as e:  # noqa: BLE001
            JOB.update(ok=False, msg=f"{type(e).__name__}: {e}")
        finally:
            JOB["running"] = False

    @app.route("/leads", methods=["GET", "POST"])
    def leads():
        from ..pipeline import build_batch, import_csv
        conn = _conn()
        if request.method == "POST":
            f = request.files.get("csv")
            if not f or not f.filename.lower().endswith(".csv"):
                flash("Pick a .csv file", "bad")
                return redirect(url_for("leads"))
            up = root / "data" / "uploads"
            up.mkdir(parents=True, exist_ok=True)
            dest = up / f"{datetime.now():%Y%m%d-%H%M%S}-{Path(f.filename).name}"
            f.save(dest)
            source = request.form.get("source", "jobs")
            with dest.open(encoding="utf-8-sig", errors="replace") as fh:
                if "email1 body" in fh.readline().lower():      # whole sequence already written: send as is
                    source = "prewritten"
            conn.close()
            if JOB.get("running"):
                flash("Another CSV is still being processed. Wait for it to finish.", "bad")
                return redirect(url_for("leads"))
            args = (dest, source, request.form.get("only_agencies") == "on")
            if app.config.get("TESTING"):
                _process_upload(*args)
            else:
                import threading
                threading.Thread(target=_process_upload, args=args, daemon=True).start()
            return redirect(url_for("leads"))
        if JOB and not JOB.get("running") and JOB.get("msg") and not JOB.get("shown"):
            flash(("Done: " if JOB["ok"] else "Upload failed: ") + JOB["msg"], "good" if JOB["ok"] else "bad")
            JOB["shown"] = True
        rows = [dict(r) for r in conn.execute(
            """SELECT l.id, l.first_name, l.last_name, l.email, l.title, l.tier, l.status, l.verify_status,
                      c.name AS company, p.saw_line, m.review, m.status AS mstatus, st.step_done, st.status AS sstatus
               FROM leads l LEFT JOIN companies c ON c.id=l.company_id
               LEFT JOIN signal_picks p ON p.lead_id=l.id AND p.rank=1
               LEFT JOIN messages m ON m.lead_id=l.id AND m.step=1
               LEFT JOIN sequence_state st ON st.lead_id=l.id
               ORDER BY l.id DESC LIMIT 500""")]
        conn.close()
        return render_template("leads.html", rows=rows, job=JOB,
                               elapsed=int(time.time() - JOB.get("started", time.time())))

    @app.route("/emails", methods=["GET", "POST"])
    def emails():
        from ..personalize.session import set_review
        conn = _conn()
        tab = request.args.get("tab", "approved")
        if request.method == "POST":
            act = request.form.get("act")
            if act == "approve_all":
                ids = [r[0] for r in conn.execute("SELECT id FROM messages WHERE step=1 AND review='pending'")]
            else:
                ids = [int(request.form["id"])]
            if act in ("approve", "approve_all"):
                ids = [i for i in ids if conn.execute("SELECT status FROM messages WHERE id=?", (i,)).fetchone()[0]
                       == "ready"]
                n = set_review(conn, ids, "approved", "approved in app")
            else:
                n = set_review(conn, ids, "held", "held in app")
            flash(f"{n} updated", "good")
            conn.close()
            return redirect(url_for("emails", tab=tab))
        q = {"approved": "m.review='approved'", "pending": "m.review='pending'", "held": "m.review='held'"}[tab] \
            if tab in ("approved", "pending", "held") else "1=1"
        rows = [dict(r) for r in conn.execute(
            f"""SELECT m.*, l.email, l.first_name, c.name AS company FROM messages m JOIN leads l ON l.id=m.lead_id
                LEFT JOIN companies c ON c.id=l.company_id WHERE m.step=1 AND {q} ORDER BY m.id DESC LIMIT 300""")]
        for r in rows:
            r["reasons"] = json.loads(r.get("qa_reasons") or "[]")
        counts = {k: conn.execute(f"SELECT COUNT(*) FROM messages WHERE step=1 AND review='{k}'").fetchone()[0]
                  for k in ("approved", "pending", "held")}
        conn.close()
        return render_template("emails.html", rows=rows, tab=tab, counts=counts)

    @app.route("/inbox", methods=["GET", "POST"])
    def inbox():
        from ..replies import classify as rc
        conn = _conn()
        rcfg = rc.load_cfg(config_dir)
        if request.method == "POST":
            msg = rc.set_draft(conn, int(request.form["id"]),
                               "approved" if request.form.get("act") == "approve" else "held", "in app")
            flash(msg, "good")
            conn.close()
            return redirect(url_for("inbox"))
        rows = rc.inbox(conn, rcfg, 100)
        conn.close()
        return render_template("inbox.html", rows=rows)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        conn = _conn()
        scfg = _sender_cfg(config_dir)
        if request.method == "POST":
            act = request.form.get("act")
            if act == "save":
                addr = request.form.get("address", "").strip().lower()
                if addr:
                    try:
                        cap = max(1, min(500, int(request.form.get("cap") or 300)))
                    except ValueError:
                        cap = 300
                    flash(ensure_mailbox(conn, scfg, addr, cap), "good")
                    rep = dns_check.check_domain(norm_domain(addr), app.config.get("DNS_LOOKUP"))
                    dns_check.save(conn, rep)
                    conn.commit()
                    flash("DNS " + ("PASS: SPF, DKIM, DMARC all found." if rep.ok else
                                    "problem: " + "; ".join(f"{k} {v[1]}" for k, v in rep.checks.items()
                                                            if v[0] != "ok" and k != "blocklists")),
                          "good" if rep.ok else "bad")
                    pw = request.form.get("password", "")
                    if pw:
                        set_env_value(root / ".env", sm.env_key(addr), pw)
                        flash("Password saved on this computer only (.env).", "good")
                for key in ("physical_address", "alerts_to"):
                    val = request.form.get(key, "").strip()
                    if val:
                        set_yaml_value(config_dir / "sender.yaml", key, val)
                        if key == "alerts_to":       # test emails may go to the alerts address too
                            own = sorted(set((scfg.get("own_addresses") or []) + [val.lower()]))
                            set_yaml_value(config_dir / "sender.yaml", "own_addresses", own)
            elif act == "dns":
                for d in sorted({norm_domain(r["address"]) for r in conn.execute("SELECT address FROM mailboxes")}):
                    rep = dns_check.check_domain(d)
                    dns_check.save(conn, rep)
                    log_event(conn, "dns_check", "domain", d, "pass" if rep.ok else "blocked")
                    conn.commit()
                    flash(" | ".join(rep.lines()), "good" if rep.ok else "bad")
            elif act == "test":
                mb = conn.execute("SELECT address FROM mailboxes ORDER BY id LIMIT 1").fetchone()
                if not mb:
                    flash("Add a mailbox first", "bad")
                else:
                    to = (scfg.get("alerts_to") or mb["address"]).strip()
                    body = "Test from DFY Sender. If this landed in spam, tell Claude.\n\n" + "\n".join(
                        [scfg.get("sign_off", ""), scfg.get("company_line", ""), scfg.get("opt_out_line", "")])
                    try:
                        msg = sm.build_message(mb["address"], scfg.get("from_name") or "", to, "DFY Sender test", body,
                                               {"Message-ID": f"<{secrets.token_hex(12)}@{mb['address'].split('@')[1]}>"})
                        with sm.SmtpSession(mb["address"], sm.password_for(mb["address"])) as smtp:
                            smtp.send(msg)
                        log_event(conn, "test_send", "mailbox", mb["address"], None, to=to)
                        conn.commit()
                        flash(f"Test email sent to {to}. Check inbox and spam.", "good")
                    except Exception as e:  # noqa: BLE001
                        flash(f"Test failed: {type(e).__name__}: {e}", "bad")
            conn.close()
            return redirect(url_for("settings"))
        mbs = [dict(r) for r in conn.execute("SELECT m.*, d.dns_report, d.checked_at FROM mailboxes m"
                                              " LEFT JOIN domains d ON d.id=m.domain_id ORDER BY m.id")]
        for m in mbs:
            m["has_password"] = bool(os.environ.get(sm.env_key(m["address"])))
            rep = json.loads(m.get("dns_report") or "{}")
            m["dns_ok"] = rep.get("ok")
        conn.close()
        return render_template("settings.html", scfg=scfg, mbs=mbs)

    return app


def main(port: int = 8765, open_browser: bool = True) -> None:
    import threading
    import webbrowser
    from .worker import Worker
    w = Worker()
    w.start()
    app = create_app(worker=w)
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"DFY Sender running at {url}  (keep this window open, close it to stop)")
    try:
        from waitress import serve        # a proper server: no "development server" warning
        serve(app, host="127.0.0.1", port=port, threads=8, _quiet=True)
    except ImportError:
        os.environ.setdefault("FLASK_SKIP_DOTENV", "1")
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
