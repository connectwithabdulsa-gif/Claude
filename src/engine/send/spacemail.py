"""Spacemail sending (phase 8). SMTP over SSL on 465, settings from CLAUDE.md section 12 and
Spaceship's Spacemail configuration screen (mail.spacemail.com, IMAP 993 SSL, SMTP 465 SSL).

Spacemail limits (Spaceship knowledgebase, read 2026-11-16): 500 emails an hour per mailbox on paid
plans, 20 an hour on trial. our caps (10 to 30 a DAY) stay far below. Spaceship may limit or suspend
a mailbox for warm-up style or high volume sending, so the slow ramp matters.
https://www.spaceship.com/knowledgebase/spacemail-email-limits/

passwords come only from the environment (.env on the sending machine), never from the db or chat:
  SPACEMAIL_PASSWORD_<ADDRESS>   e.g. abdul@trydfy.com -> SPACEMAIL_PASSWORD_ABDUL_TRYDFY_COM
  or SPACEMAIL_USER + SPACEMAIL_PASSWORD for a single mailbox.
"""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate

HOST = "mail.spacemail.com"


class MissingPassword(Exception):
    pass


class PermanentFailure(Exception):
    """the server refused this recipient or message (5xx). counted as a bounce."""


class TemporaryFailure(Exception):
    """network trouble or a 4xx. the send stays queued and is tried again next tick."""


def env_key(address: str) -> str:
    return "SPACEMAIL_PASSWORD_" + re.sub(r"[^A-Z0-9]+", "_", address.upper().replace("@", "_")).strip("_")


def password_for(address: str) -> str:
    pw = os.environ.get(env_key(address))
    if not pw and os.environ.get("SPACEMAIL_USER", "").lower() == address.lower():
        pw = os.environ.get("SPACEMAIL_PASSWORD")
    if not pw:
        raise MissingPassword(f"no password for {address}. add {env_key(address)}=... to .env on this machine")
    return pw


class QueueSender:
    """used while planning a live day: nothing leaves, sends are queued for the runner."""
    mode = "live"
    defer = True

    def send(self, email):              # never called while defer is set
        raise RuntimeError("QueueSender does not send")


def build_message(from_addr: str, from_name: str, to_addr: str, subject: str, body: str, headers: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    for k, v in (headers or {}).items():
        if k.lower() in ("from", "to", "subject", "date"):
            continue
        msg[k] = v
    msg.set_content(body)               # plain text only (CLAUDE.md section 7)
    return msg


def _default_smtp(host: str, port: int, timeout: float):
    ctx = ssl.create_default_context()
    if port == 465:
        return smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout)
    s = smtplib.SMTP(host, port, timeout=timeout)
    s.starttls(context=ctx)
    return s


class SmtpSession:
    """one login per mailbox per tick. use as a context manager."""

    def __init__(self, address: str, password: str, host: str = HOST, port: int = 465,
                 smtp_factory=None, timeout: float = 30):
        self.address, self.password = address, password
        self.host, self.port, self.timeout = host, port, timeout
        self.factory = smtp_factory or _default_smtp
        self.smtp = None

    def __enter__(self):
        try:
            self.smtp = self.factory(self.host, self.port, self.timeout)
            self.smtp.login(self.address, self.password)
        except smtplib.SMTPAuthenticationError as e:
            raise PermanentFailure(f"login refused for {self.address}: {e.smtp_code}") from e
        except (OSError, smtplib.SMTPException) as e:
            raise TemporaryFailure(f"could not connect: {type(e).__name__}") from e
        return self

    def __exit__(self, *exc):
        try:
            if self.smtp:
                self.smtp.quit()
        except Exception:  # noqa: BLE001
            pass

    def send(self, msg: EmailMessage) -> None:
        try:
            refused = self.smtp.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            codes = [c for c, _ in e.recipients.values()]
            if any(500 <= c < 600 for c in codes):
                raise PermanentFailure(f"recipient refused {codes}") from e
            raise TemporaryFailure(f"recipient deferred {codes}") from e
        except smtplib.SMTPResponseException as e:
            if 500 <= e.smtp_code < 600:
                raise PermanentFailure(f"server refused {e.smtp_code}") from e
            raise TemporaryFailure(f"server deferred {e.smtp_code}") from e
        except (OSError, smtplib.SMTPException) as e:
            raise TemporaryFailure(f"{type(e).__name__}") from e
        if refused:
            raise PermanentFailure(f"refused: {refused}")
