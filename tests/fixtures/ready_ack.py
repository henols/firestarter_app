"""The MSG_OK_READY setup ack, as the firmware sends it.

params: [buffer_size u16 BE][hw_revision u8][len u8][identity ascii]. Firmware
before CAP-02 sends only the buffer size.
"""

from __future__ import annotations

import struct

from firestarter.messages import MSG_OK_READY

from ..conftest import build_frame, id_frame_body


def ready_ack_params(
    buffer_size: int = 512,
    revision: int | None = None,
    identity: str | None = None,
) -> bytes:
    """The ack params. Without `revision` and `identity`, the 2-byte form."""
    params = struct.pack(">H", buffer_size)
    if revision is None and identity is None:
        return params
    raw = (identity or "").encode("ascii")
    return params + bytes([revision or 0, len(raw)]) + raw


def ready_ack(
    buffer_size: int = 512,
    revision: int | None = None,
    identity: str | None = None,
) -> bytes:
    """The id-frame body `_decode_id_frame` receives: id | params | crc8."""
    return id_frame_body(
        MSG_OK_READY, ready_ack_params(buffer_size, revision, identity)
    )


def ready_ack_frame(
    buffer_size: int = 512,
    revision: int | None = None,
    identity: str | None = None,
) -> bytes:
    """The complete wire frame, to feed to a fake port."""
    return build_frame(MSG_OK_READY, ready_ack_params(buffer_size, revision, identity))
