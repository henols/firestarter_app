"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

`FakeChip` -- a bench-free `EpromOperator`-shaped double that models UV
physics and absolute-offset reads (quick task 260821-wna, Task 3).

NOT a test module (no `test_` prefix) -- pytest does not collect this file.
It backs every behavioural test in Task 4 (test_chip_test.py's execution-time
mask/slot/region tests); without it, those tests would be theatre.

Two properties this double gets right that a plain `Mock` cannot:

1. `uv=True` -- a write ANDs the incoming bytes into the existing content
   (`existing & incoming`), the physical fact D-A is built on. `uv=False`
   overwrites. A fresh UV instance is all-0xFF (virgin).
2. `read_eprom` writes the requested bytes at their ABSOLUTE offset,
   reproducing `eprom_operations._write_to_file`'s `file_handle.seek
   (address)` (finding M-3). A double that wrote the payload at offset 0
   would make the engine's region-slice `[start:start+length]` look correct
   while testing nothing -- this is the single most important property of
   this double.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from firestarter.constants import FLAG_SKIP_BLANK_CHECK
from firestarter.messages import MSG_ERR_NOT_BLANK


def _parse_addr_or_size(s: str | None) -> int | None:
    """Mirror `address_parser.parse_address`/`parse_size` exactly: hex when
    the string carries `0x` (case-insensitive), else decimal; `None` means
    "not supplied"."""
    if s is None:
        return None
    return int(s, 16) if "0x" in s.lower() else int(s)


class FakeChip:
    """An in-memory chip backing store with real UV-AND-write physics and
    real absolute-offset read semantics.

    `calls` is a plain list of `(method_name, kwargs)` tuples so a test can
    assert which addresses/sizes were actually requested.
    """

    def __init__(self, memory_size: int, *, uv: bool = False):
        self.memory_size = memory_size
        self.uv = uv
        self.data = bytearray((b"\xff" if uv else b"\x00") * memory_size)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.id_ok: bool = True
        self.id_value: int | None = None
        self.blank_override: bool | None = None
        self.sdp_lock_ok: bool = True
        self.sdp_unlock_ok: bool = True

    # -- constructors for the shapes Task 4 needs ------------------------

    @classmethod
    def virgin_uv(cls, memory_size: int) -> FakeChip:
        """A fresh, all-0xFF UV chip -- D-C's "blank" starting state."""
        return cls(memory_size=memory_size, uv=True)

    @classmethod
    def uv_with_slot_saturated(
        cls, memory_size: int, slot_start: int, slot_length: int
    ) -> FakeChip:
        """A UV chip whose given slot is pre-saturated under the
        address-derived pattern: its content is set to the bitwise
        complement of that slot's pattern, so `bits_cleared_by` there is
        high (there ARE 1-bits differing from the pattern... no -- see
        below) while the MASKED image (`current & pattern`) is all-zero,
        the slot-saturation shape D-B's selector must skip.

        Concretely: `content = ~pattern`, so `mask_write_pattern(content,
        pattern) == content & pattern == (~pattern) & pattern == 0` for
        every byte -- the masked write at this slot is degenerate, exactly
        the case `WriteTarget.__post_init__` must refuse.
        """
        from firestarter.chip_test import generate_pattern

        chip = cls(memory_size=memory_size, uv=True)
        pattern = generate_pattern(slot_start, slot_length)
        complement = bytes(~b & 0xFF for b in pattern)
        chip.data[slot_start : slot_start + slot_length] = complement
        return chip

    @classmethod
    def uv_with_slot_zeroed(
        cls, memory_size: int, slot_start: int, slot_length: int
    ) -> FakeChip:
        """A UV chip whose given slot already reads all-0x00 -- the
        `WriteTarget` vacuous-pass refusal's other named case."""
        chip = cls(memory_size=memory_size, uv=True)
        chip.data[slot_start : slot_start + slot_length] = b"\x00" * slot_length
        return chip

    @classmethod
    def uv_all_saturated(cls, memory_size: int, slot_length: int) -> FakeChip:
        """A UV chip where EVERY candidate slot is saturated under the
        address-derived pattern -- the walk-exhausted refusal case."""
        from firestarter.chip_test import generate_pattern

        chip = cls(memory_size=memory_size, uv=True)
        pattern = generate_pattern(0, memory_size)
        complement = bytes(~b & 0xFF for b in pattern)
        chip.data[:] = complement
        return chip

    @classmethod
    def uv_with_content(
        cls, memory_size: int, content: bytes, *, start: int = 0
    ) -> FakeChip:
        """A UV chip with arbitrary prior content at `start`."""
        chip = cls(memory_size=memory_size, uv=True)
        chip.data[start : start + len(content)] = content
        return chip

    @classmethod
    def non_uv(cls, memory_size: int, *, content: bytes | None = None) -> FakeChip:
        """A non-UV (EEPROM/Flash) chip -- writes always overwrite."""
        chip = cls(memory_size=memory_size, uv=False)
        if content is not None:
            chip.data[: len(content)] = content
        return chip

    # -- operator-shaped methods, matching EpromOperator's real signatures --

    def write_eprom(
        self,
        name: str,
        eprom_data: dict[str, Any],
        input_file_path: str,
        operation_flags: int = 0,
        address_str: str | None = None,
        pulse_us: int = 0,
    ) -> bool:
        self.calls.append(("write_eprom", {"address_str": address_str}))
        start = _parse_addr_or_size(address_str) or 0
        incoming = Path(input_file_path).read_bytes()
        end = start + len(incoming)
        if start < 0 or end > self.memory_size:
            return False
        if self.uv:
            existing = bytes(self.data[start:end])
            merged = bytes(c & d for c, d in zip(existing, incoming))
            self.data[start:end] = merged
        else:
            self.data[start:end] = incoming
        return True

    def verify_eprom(
        self,
        name: str,
        eprom_data: dict[str, Any],
        input_file_path: str,
        operation_flags: int = 0,
        address_str: str | None = None,
        full: bool = False,
    ) -> int:
        # 202-01 D-10: int, 0 == match, 1 == mismatch (matches the real
        # EpromOperator.verify_eprom contract).
        self.calls.append(("verify_eprom", {"address_str": address_str}))
        start = _parse_addr_or_size(address_str) or 0
        expected = Path(input_file_path).read_bytes()
        end = start + len(expected)
        actual = bytes(self.data[start:end])
        return 0 if actual == expected else 1

    def read_eprom(
        self,
        name: str,
        eprom_data: dict[str, Any],
        output_file: str | None = None,
        operation_flags: int = 0,
        address_str: str | None = None,
        size_str: str | None = None,
    ) -> bool:
        """Write the requested bytes at their ABSOLUTE offset (finding
        M-3): with no address/size, write the whole device. A double that
        wrote the payload at offset 0 would make the engine's region-slice
        look correct while testing nothing -- this reproduces
        `_write_to_file`'s `file_handle.seek(address)` exactly.
        """
        self.calls.append(
            ("read_eprom", {"address_str": address_str, "size_str": size_str})
        )
        start = _parse_addr_or_size(address_str) or 0
        length = _parse_addr_or_size(size_str)
        if length is None:
            length = self.memory_size - start
        end = start + length
        chunk = bytes(self.data[start:end])
        if output_file is not None:
            with open(output_file, "wb") as fh:
                fh.seek(start)
                fh.write(chunk)
        return True

    def check_eprom_blank(
        self, name: str, eprom_data: dict[str, Any], operation_flags: int = 0
    ) -> int:
        """Returns an int (202-05 D-10, mirroring `verify_eprom`'s own
        202-01 migration): 0 == blank, 1 == not blank. `blank_override`
        stays bool internally (the caller-facing verdict, not the wire
        contract) -- only this method's own return value changed shape."""
        self.calls.append(("check_eprom_blank", {}))
        if self.blank_override is not None:
            is_blank = self.blank_override
        else:
            is_blank = bytes(self.data) == b"\xff" * self.memory_size
        return 0 if is_blank else 1

    def check_eprom_id(
        self, name: str, eprom_data: dict[str, Any], operation_flags: int = 0
    ) -> tuple[bool, int | None]:
        self.calls.append(("check_eprom_id", {}))
        return self.id_ok, self.id_value

    def erase_eprom(
        self,
        name: str,
        eprom_data: dict[str, Any],
        operation_flags: int = 0,
        address_str: str | None = None,
    ) -> bool:
        self.calls.append(("erase_eprom", {}))
        self.data[:] = b"\xff" * self.memory_size
        return True

    def sdp_lock(
        self, name: str, eprom_data: dict[str, Any], operation_flags: int = 0
    ) -> bool:
        self.calls.append(("sdp_lock", {}))
        return self.sdp_lock_ok

    def sdp_unlock(
        self, name: str, eprom_data: dict[str, Any], operation_flags: int = 0
    ) -> bool:
        self.calls.append(("sdp_unlock", {}))
        return self.sdp_unlock_ok


class WriteInitPreflightChip(FakeChip):
    """A `FakeChip` subclass modelling the firmware write-init blank-check
    pre-flight (`firestarter/src/proms/eprom.cpp:143-145`), with the REAL
    host contract: `EpromOperator.write_eprom` does NOT raise on a firmware
    refusal -- `eprom_operations._run_state_machine` catches the
    `EpromOperationError` and returns `(False, str(e))`, stamping
    `last_firmware_error_code`/`last_firmware_error_message` on itself, and
    `chip_test._firmware_error` is what reads them back. A double that
    raises is NOT firmware-faithful and would exercise a code path real
    hardware never reaches.

    `write_flags_seen` records every `operation_flags` value this instance
    is handed, which is what the UV-03 legs assert on.
    """

    def __init__(self, memory_size: int, *, uv: bool = False):
        super().__init__(memory_size, uv=uv)
        self.last_firmware_error_code: int | None = None
        self.last_firmware_error_message: str | None = None
        self.write_flags_seen: list[int] = []
        setattr(self, "check_eprom_id", Mock(return_value=(True, self.id_value)))

    def _is_blank(self, start: int = 0, end: int | None = None) -> bool:
        if self.blank_override is not None:
            return self.blank_override
        if end is None:
            end = self.memory_size
        return bytes(self.data[start:end]) == b"\xff" * (end - start)

    def write_eprom(
        self,
        name: str,
        eprom_data: dict[str, Any],
        input_file_path: str,
        operation_flags: int = 0,
        address_str: str | None = None,
        pulse_us: int = 0,
    ) -> bool:
        self.last_firmware_error_code = None
        self.last_firmware_error_message = None
        self.write_flags_seen.append(operation_flags)
        # BLANK-01 / BLANK-03 / D-16.2: scope the pre-flight check to the
        # region this write actually touches -- the same start/end
        # arithmetic FakeChip.write_eprom already does above (start from the
        # address argument, end from the payload size) -- rather than the
        # whole buffer. Before this, a non-blank byte anywhere outside the
        # target region would refuse a write that real firmware now accepts.
        start = _parse_addr_or_size(address_str) or 0
        end = start + os.path.getsize(input_file_path)
        if not (operation_flags & FLAG_SKIP_BLANK_CHECK) and not self._is_blank(
            start, end
        ):
            self.last_firmware_error_code = MSG_ERR_NOT_BLANK
            self.last_firmware_error_message = (
                "Error: EPROM not blank at address 0x000000, value 0xAB"
            )
            return False
        return super().write_eprom(
            name,
            eprom_data,
            input_file_path,
            operation_flags,
            address_str=address_str,
            pulse_us=pulse_us,
        )

    def check_eprom_blank(
        self, name: str, eprom_data: dict[str, Any], operation_flags: int = 0
    ) -> int:
        """Returns an int (202-05 D-10) -- see `FakeChip.check_eprom_blank`."""
        self.last_firmware_error_code = None
        self.last_firmware_error_message = None
        verdict = super().check_eprom_blank(name, eprom_data, operation_flags)
        if verdict != 0:
            self.last_firmware_error_code = MSG_ERR_NOT_BLANK
            self.last_firmware_error_message = (
                "Error: EPROM not blank at address 0x000000, value 0xAB"
            )
        return verdict
