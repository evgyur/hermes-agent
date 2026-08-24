"""Exact delivery receipts for the pre-tool acknowledgement boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class StartAckReceipt:
    """Payload and transport identity proven accepted by the ACK callback."""

    text: str
    message_id: Optional[str] = None
    message_ids: Tuple[str, ...] = ()
    transport_identity: str = ""
