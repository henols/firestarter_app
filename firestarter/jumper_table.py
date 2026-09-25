"""
Project Name: Firestarter
Copyright (c) 2025 Henrik Olsson

Permission is hereby granted under MIT license.

Shield jumper settings for each pin map, for the three RURP revision families.

`firestarter info` shows the jumper settings from this table only. The table has one entry for each
key in `data/pinouts.json`. Each entry records where the pin map's VPP lands in the 32-pin socket
and what the chip's pin 1 carries. The jumper cells follow from those two facts. A test compares
the two recorded facts with the values that `derive_vpp_landing` and `derive_pin1_signal` calculate
from `pinouts.json`. So if a pin map moves VPP, the test fails until someone reviews its entry.

No shield revision can sense JP4 or JP5, and the revision byte cannot tell Rev 2.0 from Rev 2.2.
For this reason `info` shows all three revision blocks for every chip.

Polarity: fail closed. A pin map with no entry, for example a user pin map in
`~/.firestarter/pin-maps.json`, gets no jumper settings. It gets `NO_ENTRY_FORMAT` instead. If the
lookup failed open, it could show a guessed setting. A guessed JP4 setting can connect VPE to a
live address pin.

Sources:

- Rev 0/1 (JP1, JP2, JP3): `firestarter_fw/document/rurp_schematics_rev1.pdf`. The title block
  reads "Rev: 0". Rev 0 and Rev 1 have the same jumper circuits.
  - JP1 common goes to socket pin 28. Its other pins are the A13 net and +5 V.
  - JP2 common goes to socket pin 30. Its other pins are +5 V and the A17 net.
  - JP3 common is the Q8 collector (the pin-1 VPE switch). Its other pins are socket pin 3 (the
    "28pin" side) and socket pin 1 (the "32pin" side).
  - No Rev 0/1 path reaches socket pin 25.
- Rev 2.x (JP4): the upstream Rev 2.3 KiCad schematic and PCB, and the Rev 2.2 bench probe in v1.37
  Phase 182 (`182-06-bench-readings.md`, 2026-09-10).
  - The JP4 common pad goes to socket pin 1, which carries the pin-1 VPP node through the bridged
    JP5.
  - Rev 2.2/2.3: the pole toward the ZIF socket goes to socket pin 3 (28-pin pin 1). The pole toward
    the board edge goes to socket pin 25 (24-pin pin 21). Measured.
  - Rev 2.0/2.1: two positions. The "Closed" destination is inferred to be socket pin 3, but no one
    has measured it (PROBE-01). The entries that depend on it have `probe_pending=True`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping  # noqa: UP035

from firestarter.database import ROM_CE, ROM_OE, pin_conversions

# ---------------------------------------------------------------------------
# Where VPP lands, and what the chip's pin 1 carries
# ---------------------------------------------------------------------------


class VppLanding(Enum):
    """The place where a pin map's programming VPP lands in the 32-pin socket."""

    SOCKET_1 = "socket pin 1"
    SOCKET_3 = "socket pin 3"
    SOCKET_25 = "socket pin 25"
    OE = "the OE line"
    NONE = "none"


_SOCKET_PIN_TO_LANDING = {
    1: VppLanding.SOCKET_1,
    3: VppLanding.SOCKET_3,
    25: VppLanding.SOCKET_25,
}

# The shield bus line of each 32-pin socket pin. A 24-pin or 28-pin chip sits at the bottom of the
# socket, so its pin N reaches the same bus line as one socket pin. The offset therefore comes from
# `pin_conversions` and is not written here a second time.
_SOCKET_PIN_BY_BUS_LINE = {
    line: socket_pin
    for socket_pin, line in pin_conversions[32].items()
    if line not in (ROM_CE, ROM_OE)
}


def socket_pin_for(chip_pin: int, pin_count: int) -> int | None:
    """Return the 32-pin socket pin under `chip_pin`, or None if the shield has no bus line there."""
    line = pin_conversions.get(pin_count, {}).get(chip_pin)
    if line is None:
        return None
    return _SOCKET_PIN_BY_BUS_LINE.get(line)


def _pin_list(pins: Mapping[str, Any], key: str) -> list:
    value = pins.get(key)
    return value if isinstance(value, list) else []


def derive_vpp_landing(pins: Mapping[str, Any], pin_count: int) -> VppLanding | None:
    """Calculate where the VPP of one `pinouts.json` `pins` dict lands.

    Returns None when the VPP pin does not reach a socket pin that the table knows. The caller must
    treat None as "unknown", never as `VppLanding.NONE`.
    """
    vpp = _pin_list(pins, "vpp-pin")
    if not vpp:
        return VppLanding.NONE
    if vpp == _pin_list(pins, "oe-pin"):
        return VppLanding.OE
    socket_pin = socket_pin_for(vpp[0], pin_count)
    if socket_pin is None:
        return None
    return _SOCKET_PIN_TO_LANDING.get(socket_pin)


def derive_pin1_signal(pins: Mapping[str, Any]) -> str:
    """Return the signal on the chip's pin 1: "VPP", "NC", "A<n>", or "UNASSIGNED"."""
    if 1 in _pin_list(pins, "vpp-pin"):
        return "VPP"
    if 1 in _pin_list(pins, "nc-pin"):
        return "NC"
    address = _pin_list(pins, "address-bus-pins")
    if 1 in address:
        return f"A{address.index(1)}"
    return "UNASSIGNED"


# ---------------------------------------------------------------------------
# Jumper positions
# ---------------------------------------------------------------------------


class Jp1(Enum):
    """Rev 0/1 JP1: socket pin 28 to the A13 net, or to +5 V (24-pin VCC)."""

    A13 = "A13"
    VCC = "5V"


class Jp2(Enum):
    """Rev 0/1 JP2: socket pin 30 to +5 V (28-pin VCC), or to the A17 net."""

    VCC = "5V"
    A17 = "A17"
    DOES_NOT_MATTER = "does not matter"


class Jp3(Enum):
    """Rev 0/1 JP3: the pin-1 VPE switch to socket pin 3 ("28pin") or socket pin 1 ("32pin")."""

    PIN28 = "28pin"
    PIN32 = "32pin"
    NO_JUMPER = "no jumper"
    DOES_NOT_MATTER = "does not matter"


class Jp4Rev20(Enum):
    """Rev 2.0/2.1 JP4, two positions."""

    OPEN = "Open"
    CLOSED = "Closed"
    DOES_NOT_MATTER = "does not matter"


class Jp4Rev22(Enum):
    """Rev 2.2/2.3 JP4, three positions. There is no "does not matter" value (JMP-07).

    On these revisions the 24-pin pole connects socket pin 1 to socket pin 25. On a 28-pin or 32-pin
    chip, socket pin 25 is a live pin. So every chip that does not use JP4 gets "no jumper".
    """

    NO_JUMPER = "no jumper"
    POLE_28 = "28-pin pole, toward the ZIF socket"
    POLE_24 = "24-pin pole, toward the board edge"


class Note(Enum):
    """A note that a revision block shows under its jumper lines."""

    VPP_UNREACHABLE = "vpp_unreachable"
    PROBE_PENDING = "probe_pending"
    NO_PROGRAMMING_MODE = "no_programming_mode"
    JP5_A19 = "jp5_a19"


# ---------------------------------------------------------------------------
# Operator text (ASD-STE100). Tests import these constants.
# ---------------------------------------------------------------------------

REV01_KEY = "0 & 1"
REV20_KEY = "2.0 & 2.1"
REV22_KEY = "2.2 & 2.3"

JP4_SILKSCREEN_RULE = '"Only for ROMs with VPP on P1"'
JP5_SILKSCREEN_RULE = '"Cut for ROMs with A19 on P1"'

VPP_UNREACHABLE_TEXT = (
    "This shield revision cannot connect VPP to pin 21 of this chip. "
    "To program this chip, use a Rev 2.2 or later shield."
)
PROBE_PENDING_TEXT = (
    "This JP4 setting is not measured on a Rev 2.0 board. It comes from the schematic."
)
NO_PROGRAMMING_MODE_TEXT = (
    "Firestarter has no programming mode for this chip. "
    "The jumper setting is for reference only."
)
JP5_A19_TEXT = (
    f"Pin 1 of this chip is A19. Refer to the JP5 silkscreen: {JP5_SILKSCREEN_RULE}."
)
NO_ENTRY_FORMAT = (
    "No jumper data for pin map {pin_map}. "
    "Do not use the jumper settings of a different chip."
)

NOTE_TEXT = {
    Note.VPP_UNREACHABLE: VPP_UNREACHABLE_TEXT,
    Note.PROBE_PENDING: PROBE_PENDING_TEXT,
    Note.NO_PROGRAMMING_MODE: NO_PROGRAMMING_MODE_TEXT,
    Note.JP5_A19: JP5_A19_TEXT,
}

# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JumperEntry:
    """The jumper settings of one pin map on each revision family."""

    vpp_lands: VppLanding
    pin1_signal: str
    jp1: Jp1
    jp2: Jp2
    jp3: Jp3
    rev20_jp4: Jp4Rev20
    rev22_jp4: Jp4Rev22
    probe_pending: bool = False
    rev01_notes: tuple[Note, ...] = ()
    rev20_notes: tuple[Note, ...] = ()
    rev22_notes: tuple[Note, ...] = ()


_24PIN_UNREACHABLE = (Note.VPP_UNREACHABLE,)

JUMPER_TABLE: Mapping[str, JumperEntry] = {
    # --- 24-pin. JP1 feeds VCC to socket pin 28 (chip pin 24). Socket pins 1-4 and 29-32 are empty,
    # so JP2 does not matter, and Rev 2.0/2.1 JP4 joins two empty socket pins (PROBE-01).
    "DIP24_2716": JumperEntry(
        vpp_lands=VppLanding.SOCKET_25,
        pin1_signal="A7",
        jp1=Jp1.VCC,
        jp2=Jp2.DOES_NOT_MATTER,
        jp3=Jp3.DOES_NOT_MATTER,
        rev20_jp4=Jp4Rev20.DOES_NOT_MATTER,
        rev22_jp4=Jp4Rev22.POLE_24,
        probe_pending=True,
        rev01_notes=_24PIN_UNREACHABLE,
        rev20_notes=_24PIN_UNREACHABLE,
    ),
    "DIP24_2532": JumperEntry(
        vpp_lands=VppLanding.SOCKET_25,
        pin1_signal="A7",
        jp1=Jp1.VCC,
        jp2=Jp2.DOES_NOT_MATTER,
        jp3=Jp3.DOES_NOT_MATTER,
        rev20_jp4=Jp4Rev20.DOES_NOT_MATTER,
        rev22_jp4=Jp4Rev22.POLE_24,
        probe_pending=True,
        rev01_notes=_24PIN_UNREACHABLE,
        rev20_notes=_24PIN_UNREACHABLE,
        rev22_notes=(Note.NO_PROGRAMMING_MODE,),
    ),
    # VPP shares OE (chip pin 20, Q6). On Rev 2.2/2.3 the 24-pin pole joins socket pin 1 to A11.
    "DIP24_2732": JumperEntry(
        vpp_lands=VppLanding.OE,
        pin1_signal="A7",
        jp1=Jp1.VCC,
        jp2=Jp2.DOES_NOT_MATTER,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.DOES_NOT_MATTER,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
        probe_pending=True,
    ),
    # No VPP. WE is chip pin 21 (socket pin 25), so the Rev 2.2/2.3 24-pin pole would join it to
    # socket pin 1.
    "DIP24_2816": JumperEntry(
        vpp_lands=VppLanding.NONE,
        pin1_signal="A7",
        jp1=Jp1.VCC,
        jp2=Jp2.DOES_NOT_MATTER,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.DOES_NOT_MATTER,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
        probe_pending=True,
    ),
    "DIP24_6116": JumperEntry(
        vpp_lands=VppLanding.NONE,
        pin1_signal="A7",
        jp1=Jp1.VCC,
        jp2=Jp2.DOES_NOT_MATTER,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.DOES_NOT_MATTER,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
        probe_pending=True,
    ),
    # --- 28-pin. JP1 gives A13 to socket pin 28 (chip pin 26). JP2 gives +5 V to socket pin 30
    # (chip pin 28, VCC). Chip pin 1 is socket pin 3.
    "DIP28_2764": JumperEntry(
        vpp_lands=VppLanding.SOCKET_3,
        pin1_signal="VPP",
        jp1=Jp1.A13,
        jp2=Jp2.VCC,
        jp3=Jp3.PIN28,
        rev20_jp4=Jp4Rev20.CLOSED,
        rev22_jp4=Jp4Rev22.POLE_28,
        probe_pending=True,
    ),
    "DIP28_27256": JumperEntry(
        vpp_lands=VppLanding.SOCKET_3,
        pin1_signal="VPP",
        jp1=Jp1.A13,
        jp2=Jp2.VCC,
        jp3=Jp3.PIN28,
        rev20_jp4=Jp4Rev20.CLOSED,
        rev22_jp4=Jp4Rev22.POLE_28,
        probe_pending=True,
    ),
    # VPP shares OE (Q6). Chip pin 1 is A15, so the pin-1 VPE switch must not reach it.
    "DIP28_27512": JumperEntry(
        vpp_lands=VppLanding.OE,
        pin1_signal="A15",
        jp1=Jp1.A13,
        jp2=Jp2.VCC,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
    ),
    "DIP28_28C256": JumperEntry(
        vpp_lands=VppLanding.NONE,
        pin1_signal="A14",
        jp1=Jp1.A13,
        jp2=Jp2.VCC,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
    ),
    # Chip pin 26 is NC on these maps, so JP1 is A13 only to agree with the other 28-pin maps.
    "DIP28_28C64": JumperEntry(
        vpp_lands=VppLanding.NONE,
        pin1_signal="NC",
        jp1=Jp1.A13,
        jp2=Jp2.VCC,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
    ),
    "DIP28_JEDEC_SRAM_8K": JumperEntry(
        vpp_lands=VppLanding.NONE,
        pin1_signal="NC",
        jp1=Jp1.A13,
        jp2=Jp2.VCC,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
    ),
    # --- 32-pin. JP1 gives A13 to socket pin 28. JP2 gives A17 to socket pin 30. Chip pin 1 is
    # socket pin 1. On Rev 2.x, VPP reaches socket pin 1 through the bridged JP5, and JP4 stays open.
    "DIP32_STD": JumperEntry(
        vpp_lands=VppLanding.SOCKET_1,
        pin1_signal="VPP",
        jp1=Jp1.A13,
        jp2=Jp2.A17,
        jp3=Jp3.PIN32,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
    ),
    "DIP32_27C020": JumperEntry(
        vpp_lands=VppLanding.SOCKET_1,
        pin1_signal="VPP",
        jp1=Jp1.A13,
        jp2=Jp2.A17,
        jp3=Jp3.PIN32,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
    ),
    # VPP shares OE. Chip pin 1 is A19. The JP5 gate (jp5_gate.py) controls write and erase.
    "DIP32_27C801": JumperEntry(
        vpp_lands=VppLanding.OE,
        pin1_signal="A19",
        jp1=Jp1.A13,
        jp2=Jp2.A17,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
        rev20_notes=(Note.JP5_A19,),
        rev22_notes=(Note.JP5_A19,),
    ),
    "DIP32_SST39SF040": JumperEntry(
        vpp_lands=VppLanding.NONE,
        pin1_signal="A18",
        jp1=Jp1.A13,
        jp2=Jp2.A17,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
    ),
    "DIP32_28C512_EEPROM": JumperEntry(
        vpp_lands=VppLanding.NONE,
        pin1_signal="UNASSIGNED",
        jp1=Jp1.A13,
        jp2=Jp2.A17,
        jp3=Jp3.NO_JUMPER,
        rev20_jp4=Jp4Rev20.OPEN,
        rev22_jp4=Jp4Rev22.NO_JUMPER,
    ),
}


def lookup(pin_map_key: str | None) -> JumperEntry | None:
    """Return the entry for `pin_map_key`, or None when the table has no entry (fail closed)."""
    if not pin_map_key:
        return None
    return JUMPER_TABLE.get(pin_map_key)


# ---------------------------------------------------------------------------
# Rendering to display data
# ---------------------------------------------------------------------------

_THREE_PIN_OFF = " ● ● ● "
_THREE_PIN_LEFT = "(● ●)● "
_THREE_PIN_RIGHT = " ●(● ●)"
_TWO_PIN_OPEN = " ● ●   "
_TWO_PIN_CLOSED = "(● ●)  "

# Each Rev 0/1 jumper is a three-pin header. The left label is the left pin pair, the right label
# is the right pin pair.
_REV01_LAYOUT: dict[str, tuple[str, str]] = {
    "jp1": (Jp1.VCC.value, Jp1.A13.value),
    "jp2": (Jp2.VCC.value, Jp2.A17.value),
    "jp3": (Jp3.PIN28.value, Jp3.PIN32.value),
}


def _three_pin_display(setting: Enum, left: str, right: str) -> str:
    if setting.value == left:
        return _THREE_PIN_LEFT
    if setting.value == right:
        return _THREE_PIN_RIGHT
    return _THREE_PIN_OFF


def _rev01_jumper(name: str, setting: Enum) -> dict[str, str]:
    left, right = _REV01_LAYOUT[name]
    return {
        "display": _three_pin_display(setting, left, right),
        "choices": f"{left}, {right}",
        "selected_label": setting.value,
    }


def _rev20_jp4(setting: Jp4Rev20) -> dict[str, str]:
    display = _TWO_PIN_CLOSED if setting is Jp4Rev20.CLOSED else _TWO_PIN_OPEN
    return {
        "display": display,
        "choices": JP4_SILKSCREEN_RULE,
        "selected_label": setting.value,
    }


def _rev22_jp4(setting: Jp4Rev22) -> dict[str, str]:
    return {
        "display": "",
        "choices": JP4_SILKSCREEN_RULE,
        "selected_label": setting.value,
    }


def _notes(notes: tuple[Note, ...]) -> list[str]:
    return [NOTE_TEXT[note] for note in notes]


def render_blocks(entry: JumperEntry) -> dict[str, dict[str, Any]]:
    """Return the three revision blocks of one entry as display data.

    Each block is `{"jumpers": {name: {display, choices, selected_label}}, "notes": [text]}`.
    """
    rev20_notes = list(_notes(entry.rev20_notes))
    if entry.probe_pending:
        rev20_notes.append(PROBE_PENDING_TEXT)
    return {
        REV01_KEY: {
            "jumpers": {
                "jp1": _rev01_jumper("jp1", entry.jp1),
                "jp2": _rev01_jumper("jp2", entry.jp2),
                "jp3": _rev01_jumper("jp3", entry.jp3),
            },
            "notes": _notes(entry.rev01_notes),
        },
        REV20_KEY: {
            "jumpers": {"jp4": _rev20_jp4(entry.rev20_jp4)},
            "notes": rev20_notes,
        },
        REV22_KEY: {
            "jumpers": {"jp4": _rev22_jp4(entry.rev22_jp4)},
            "notes": _notes(entry.rev22_notes),
        },
    }
