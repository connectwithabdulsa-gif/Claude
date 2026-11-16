"""Spacemail IMAP reply reader (phase 8), behind the ReplySource interface.

read only: it never sends anything and never marks mail as read (BODY.PEEK). it remembers the last
UID per mailbox folder (table imap_state), so each email is handed to replies.ingest once.
folders: INBOX plus the folder the server flags as \\Junk (special use), so spam-foldered replies
are not missed. our own sent copies are skipped.
"""
import email
import email.policy
import imaplib
import re
import ssl
from email.utils import getaddresses, parsedate_to_datetime

from ..db import now
from .base import IncomingReply

HOST = "mail.spacemail.com"


def _default_imap(host: str, port: int):
    return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)


def _text(msg) -> str:
    part = msg.get_body(preferencelist=("plain",)) if msg.is_multipart() else msg
    if part is None:
        part = msg.get_body(preferencelist=("html",))
        if part is None:
            return ""
        html = part.get_content()
        return re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html))
    try:
        return part.get_content()
    except Exception:  # noqa: BLE001  odd charsets
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def parse(raw: bytes) -> IncomingReply:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    frm = getaddresses([str(msg.get("From", ""))])
    to = getaddresses([str(msg.get("To", ""))])
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))).isoformat()
    except Exception:  # noqa: BLE001
        received = now()
    refs = str(msg.get("References", "") or "").split()
    return IncomingReply(from_address=(frm[0][1] if frm else "").lower(), body=_text(msg),
                         subject=str(msg.get("Subject", "") or ""), to_address=(to[0][1] if to else "").lower(),
                         received_at=received, message_id=str(msg.get("Message-ID", "") or "").strip() or None,
                         in_reply_to=str(msg.get("In-Reply-To", "") or "").strip() or None, references=refs)


def _folders(imap) -> list[str]:
    out = ["INBOX"]
    typ, data = imap.list()
    if typ == "OK":
        for line in data or []:
            s = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            if "\\Junk" in s:
                name = s.rsplit(' "', 1)[-1].rstrip('"') if s.endswith('"') else s.split()[-1]
                out.append(name)
    return out


class ImapReplySource:
    def __init__(self, conn, address: str, password: str, host: str = HOST, port: int = 993,
                 imap_factory=None, max_per_folder: int = 200):
        self.conn, self.address, self.password = conn, address.lower(), password
        self.host, self.port = host, port
        self.factory = imap_factory or _default_imap
        self.max = max_per_folder

    def _state(self, folder):
        r = self.conn.execute("SELECT uidvalidity, last_uid FROM imap_state WHERE mailbox=? AND folder=?",
                              (self.address, folder)).fetchone()
        return (r["uidvalidity"], r["last_uid"]) if r else (None, 0)

    def _save(self, folder, validity, last_uid):
        self.conn.execute("""INSERT INTO imap_state (mailbox, folder, uidvalidity, last_uid, updated_at)
                             VALUES (?,?,?,?,?) ON CONFLICT(mailbox, folder) DO UPDATE SET
                             uidvalidity=excluded.uidvalidity, last_uid=excluded.last_uid, updated_at=excluded.updated_at""",
                          (self.address, folder, validity, last_uid, now()))

    def replies(self):
        imap = self.factory(self.host, self.port)
        try:
            imap.login(self.address, self.password)
            for folder in _folders(imap):
                typ, data = imap.select(f'"{folder}"' if " " in folder else folder, readonly=True)
                if typ != "OK":
                    continue
                typ, vdata = imap.response("UIDVALIDITY")
                validity = (vdata[0].decode() if vdata and vdata[0] else None)
                old_validity, last = self._state(folder)
                if old_validity and validity and old_validity != validity:
                    last = 0                         # mailbox was rebuilt: re-read, ingest dedupes by Message-ID
                typ, data = imap.uid("SEARCH", None, f"UID {last + 1}:*")
                uids = sorted(int(u) for u in (data[0].split() if typ == "OK" and data and data[0] else [])
                              if int(u) > last)[: self.max]
                for uid in uids:
                    typ, msg_data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
                    raw = next((p[1] for p in msg_data or [] if isinstance(p, tuple)), None)
                    if typ == "OK" and raw:
                        r = parse(raw)
                        if r.from_address != self.address:          # skip our own sent copies
                            yield r
                    last = uid
                    self._save(folder, validity, last)
                if not uids:
                    self._save(folder, validity, last)
                self.conn.commit()
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass
