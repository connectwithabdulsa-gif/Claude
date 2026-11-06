"""Sender adapter interface. only the simulator exists. real adapters (Spacemail SMTP,
Smartlead, Instantly) come in phase 8, after reading their docs and with explicit approval."""
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class OutgoingEmail:
    lead_id: int
    step: int
    from_addr: str
    to_addr: str
    subject: str
    body: str
    send_at_utc: str
    headers: dict = field(default_factory=dict)


class Sender(Protocol):
    mode: str
    def send(self, email: OutgoingEmail) -> str: ...


class SimulatorSender:
    """writes what would be sent, and when, to a jsonl outbox. never opens a network connection."""
    mode = "dry_run"

    def __init__(self, outbox: Path):
        self.outbox = Path(outbox)
        self.outbox.parent.mkdir(parents=True, exist_ok=True)

    def send(self, email: OutgoingEmail) -> str:
        provider_id = f"sim-{uuid.uuid4().hex[:12]}"
        with self.outbox.open("a") as f:
            f.write(json.dumps({**email.__dict__, "provider_id": provider_id}) + "\n")
        return provider_id


def build_headers(email: OutgoingEmail, in_reply_to: str | None, message_id: str, list_unsub: bool) -> dict:
    h = {"Message-ID": message_id}
    if in_reply_to:                                   # real thread, so no fake "re:" is ever needed
        h["In-Reply-To"] = in_reply_to
        h["References"] = in_reply_to
    if list_unsub:
        h["List-Unsubscribe"] = f"<mailto:{email.from_addr}?subject=unsubscribe>"
        h["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    return h
