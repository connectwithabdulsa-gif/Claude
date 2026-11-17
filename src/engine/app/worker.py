"""background sender for the app: calls runner.tick every few minutes while the app window is open.
same gates and lock as the cron job, so both can never send twice."""
import threading
import time
from datetime import datetime, timezone

from ..db import connect
from ..paths import CONFIG_DIR, db_path, load_dotenv
from ..send import runner
from ..sequence import scheduler as sch


def refresh_dns(conn, max_age_h: int = 20, lookup=None, dns_lookup=None) -> int:
    """re-check each sending domain once a day, so the 24h DNS gate never blocks a running app."""
    from ..normalize import norm_domain
    from ..send import dns_check
    n = 0
    now_dt = datetime.now(timezone.utc)
    for d in sorted({norm_domain(r["address"]) for r in conn.execute("SELECT address FROM mailboxes")}):
        row = conn.execute("SELECT checked_at FROM domains WHERE domain=?", (d,)).fetchone()
        if row and row["checked_at"] and (now_dt - datetime.fromisoformat(row["checked_at"])).total_seconds() < max_age_h * 3600:
            continue
        dns_check.save(conn, dns_check.check_domain(d, lookup or dns_lookup))
        n += 1
    conn.commit()
    return n


class Worker:
    def __init__(self, interval_s: int = 300, config_dir=CONFIG_DIR):
        self.interval = interval_s
        self.config_dir = config_dir
        self.last_run: str | None = None
        self.last_lines: list[str] = []
        self.next_run: float = time.time() + 5
        self.running = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="dfy-sender", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def run_now(self):
        self.next_run = time.time()

    def _loop(self):
        while not self._stop.is_set():
            if time.time() >= self.next_run:
                self.tick_once()
                self.next_run = time.time() + self.interval
            self._stop.wait(2)

    def tick_once(self) -> list[str]:
        cfg = sch.load_cfg(self.config_dir)
        lock = runner.RunLock(runner._p(cfg["sending"]["live"]["lock_file"]))
        if not lock.acquire():
            self.last_lines = ["another tick is still running, skipped"]
            return self.last_lines
        self.running = True
        try:
            load_dotenv()
            conn = connect(db_path())
            refresh_dns(conn)
            res = runner.tick(conn, cfg)
            self.last_lines = res.lines()
            if res.alerts:
                mbs = [dict(r) for r in conn.execute("SELECT * FROM mailboxes WHERE paused=0 ORDER BY id")]
                runner.send_alert(cfg, res.alerts, mbs)
            conn.close()
        except Exception as e:  # noqa: BLE001  the app must keep running
            self.last_lines = [f"error: {type(e).__name__}: {e}"]
        finally:
            self.running = False
            self.last_run = datetime.now(timezone.utc).isoformat(timespec="seconds")
            lock.release()
        return self.last_lines
