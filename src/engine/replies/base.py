"""ReplySource adapter interface. file and fake sources now, Spacemail IMAP in phase 8."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol


@dataclass
class IncomingReply:
    from_address: str
    body: str
    subject: str = ""
    to_address: str = ""
    received_at: str = ""
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list = field(default_factory=list)


class ReplySource(Protocol):
    def replies(self) -> Iterator[IncomingReply]: ...


def _from_dict(d: dict) -> IncomingReply:
    refs = d.get("references") or []
    if isinstance(refs, str):
        refs = refs.split()
    return IncomingReply(from_address=d.get("from") or d.get("from_address") or "", body=d.get("body") or "",
                         subject=d.get("subject") or "", to_address=d.get("to") or d.get("to_address") or "",
                         received_at=d.get("received_at") or "", message_id=d.get("message_id"),
                         in_reply_to=d.get("in_reply_to"), references=refs)


class FileReplySource:
    """a json list of replies, e.g. pasted in or saved from a mail client."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def replies(self):
        data = json.loads(self.path.read_text())
        for d in data if isinstance(data, list) else data.get("replies", []):
            yield _from_dict(d)


class FakeReplySource:
    def __init__(self, items: list[dict]):
        self.items = items

    def replies(self):
        for d in self.items:
            yield _from_dict(d)
