"""Spacemail connection check. logs in over IMAP and SMTP, then logs out.
it never sends an email and never prints the password.

settings (CLAUDE.md section 12, from Spaceship's help page):
  IMAP mail.spacemail.com 993 SSL, SMTP mail.spacemail.com 465 SSL, username = full address.

usage:  python spacemail_check.py      (reads .env in this folder)
"""
import imaplib
import os
import smtplib
import ssl
import sys
from pathlib import Path


def load_env(path=Path(__file__).with_name(".env")):
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_env()
    user = os.environ.get("SPACEMAIL_USER", "")
    password = os.environ.get("SPACEMAIL_PASSWORD", "")
    if not user or not password or user.startswith("you@"):
        print("missing SPACEMAIL_USER or SPACEMAIL_PASSWORD in .env")
        return 2
    imap_host = os.environ.get("SPACEMAIL_IMAP_HOST", "mail.spacemail.com")
    imap_port = int(os.environ.get("SPACEMAIL_IMAP_PORT", "993"))
    smtp_host = os.environ.get("SPACEMAIL_SMTP_HOST", "mail.spacemail.com")
    smtp_port = int(os.environ.get("SPACEMAIL_SMTP_PORT", "465"))
    ctx = ssl.create_default_context()
    ok = True

    try:
        with imaplib.IMAP4_SSL(imap_host, imap_port, ssl_context=ctx, timeout=20) as imap:
            imap.login(user, password)
            status, data = imap.select("INBOX", readonly=True)
            count = data[0].decode() if status == "OK" else "?"
            print(f"imap ok   {imap_host}:{imap_port}  inbox messages: {count}")
            imap.logout()
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"imap FAIL {imap_host}:{imap_port}  {type(e).__name__}: {e}")

    try:
        if smtp_port == 465:
            smtp = smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=20)
        else:
            smtp = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
            smtp.starttls(context=ctx)
        with smtp:
            smtp.login(user, password)
            print(f"smtp ok   {smtp_host}:{smtp_port}  login accepted (nothing sent)")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"smtp FAIL {smtp_host}:{smtp_port}  {type(e).__name__}: {e}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
