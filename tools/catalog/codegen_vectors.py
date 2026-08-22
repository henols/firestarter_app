#!/usr/bin/env python3
"""
Firestarter v1.10 frame-vector catalog codegen.

Reads tools/catalog/frame-vectors.toml (or a vendored copy in a sub-repo's
tools/catalog/ directory) and emits one of two deterministic outputs:

  --language cpp-vectors    -> include/frame_vectors.h
                               (PROGMEM const struct array)
  --language python-vectors -> firestarter/frame_vectors.py
                               (Python NamedTuple list)

Determinism contract (LCAT-05): two consecutive runs against the same catalog
file produce byte-identical output. Achieved by:
  - sorting vectors by id ascending before emission
  - no timestamps, hostnames, or hashes in the banner
  - LF line endings via Path.write_text(..., newline='\\n')
  - upper-case 2-digit hex literals ("0x%02X")
  - explicit dict iteration via sorted(...)

Validation (--check): validates the catalog and exits 0/1 without writing
files. Must not require [[messages]] — this catalog uses [[vectors]].

This script is SEPARATE from codegen.py to avoid entangling the [[messages]]
validator with the [[vectors]] schema (RESEARCH Open Q3 + Pitfall 6, Phase 52).
The host sub-repo keeps a byte-identical copy at
firestarter_app/tools/catalog/codegen_vectors.py (D-04/D-09).

Stdlib only (Python 3.11+ for tomllib).
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

# ===========================================================================
# 1. CONFIGURATION
# ===========================================================================

VECTOR_NAME_RE = re.compile(r"^VEC_[A-Z0-9][A-Z0-9_]*$")

# Max payload / frame sizes for the C++ struct array dimensions.
# Sized to the largest D-05 corpus member: 1024-byte payload, 1031-byte frame.
MAX_PAYLOAD_LEN = 1024
MAX_FRAME_LEN   = 1031

# ===========================================================================
# 2. CATALOG LOADING + VALIDATION
# ===========================================================================


class CatalogError(Exception):
    pass


def _load_catalog(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _parse_hex_bytes(hex_str: str, field: str, vec_id: int) -> bytes:
    """Parse a space-separated hex string into bytes. Empty string -> b''."""
    hex_str = hex_str.strip()
    if not hex_str:
        return b""
    parts = hex_str.split()
    for p in parts:
        if len(p) != 2 or not all(c in "0123456789ABCDEFabcdef" for c in p):
            raise CatalogError(
                f"Vector id=0x{vec_id:02X}: {field} contains invalid hex token {p!r}"
            )
    return bytes(int(p, 16) for p in parts)


def validate_catalog(catalog: dict) -> None:
    """Validate a [[vectors]] catalog dict. Raises CatalogError on any violation."""
    if "catalog" not in catalog:
        raise CatalogError("Missing [catalog] table")
    if "version" not in catalog["catalog"]:
        raise CatalogError("[catalog] missing 'version' key")
    if "project" not in catalog["catalog"]:
        raise CatalogError("[catalog] missing 'project' key")

    if "vectors" not in catalog or not catalog["vectors"]:
        raise CatalogError("[[vectors]] array is missing or empty")

    seen_ids: set[int] = set()
    seen_names: set[str] = set()

    for vec in catalog["vectors"]:
        # Required keys
        for key in ("id", "name", "description", "payload_hex", "frame_hex"):
            if key not in vec:
                raise CatalogError(f"Vector missing required key: {key!r}")

        vid = vec["id"]
        if not isinstance(vid, int) or vid < 0 or vid > 255:
            raise CatalogError(f"Vector id={vid!r} must be a u8 integer (0..255)")
        if vid in seen_ids:
            raise CatalogError(f"Duplicate vector id=0x{vid:02X}")
        seen_ids.add(vid)

        name = vec["name"]
        if not isinstance(name, str) or not VECTOR_NAME_RE.match(name):
            raise CatalogError(
                f"Vector id=0x{vid:02X}: name {name!r} must match VEC_[A-Z][A-Z0-9_]*"
            )
        if name in seen_names:
            raise CatalogError(f"Duplicate vector name {name!r}")
        seen_names.add(name)

        # Validate payload_hex and frame_hex parse as even-length hex
        _parse_hex_bytes(vec["payload_hex"], "payload_hex", vid)
        _parse_hex_bytes(vec["frame_hex"], "frame_hex", vid)


# ===========================================================================
# 3. EMITTERS
# ===========================================================================


def _sorted_vectors(catalog: dict) -> list[dict]:
    return sorted(catalog["vectors"], key=lambda v: v["id"])


# ---------------------------------------------------------------------------
# 3a. cpp-vectors emitter — include/frame_vectors.h
# ---------------------------------------------------------------------------

_CPP_BANNER = """\
/*
 * Project Name: Firestarter
 * Copyright (c) 2024 Henrik Olsson
 *
 * Permission is hereby granted under MIT license.
 *
 * Firestarter -- v1.10 frame-vector catalog (C++ firmware side)
 *
 * DO NOT EDIT -- generated by tools/catalog/codegen_vectors.py from
 *               tools/catalog/frame-vectors.toml.
 * Re-run codegen after editing the canonical catalog.
 *
 * Catalog version: {version}
 * Total vectors: {count}
 */
"""

_CPP_HEADER_GUARD_OPEN = """\
#ifndef __FRAME_VECTORS_H__
#define __FRAME_VECTORS_H__

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <avr/pgmspace.h>

"""

_CPP_STRUCT = """\
/* --- Frame vector struct (PROGMEM) --- */
typedef struct {{
    uint8_t  id;
    uint8_t  payload[{max_payload}];
    uint16_t payload_len;
    uint8_t  frame[{max_frame}];
    uint16_t frame_len;
}} frame_vector_t;

"""

_CPP_HEADER_GUARD_CLOSE = """\

#ifdef __cplusplus
}
#endif

#endif /* __FRAME_VECTORS_H__ */
"""


def _bytes_to_cpp_array(data: bytes) -> str:
    """Emit a C-style byte initializer list: {0x01, 0x02, ...}"""
    if not data:
        return "{}"
    parts = ["0x%02X" % b for b in data]
    return "{" + ", ".join(parts) + "}"


def emit_cpp_vectors(catalog: dict) -> str:
    vectors = _sorted_vectors(catalog)
    version = catalog["catalog"]["version"]
    count = len(vectors)

    lines: list[str] = []
    lines.append(_CPP_BANNER.format(version=version, count=count))
    lines.append(_CPP_HEADER_GUARD_OPEN)
    lines.append(_CPP_STRUCT.format(max_payload=MAX_PAYLOAD_LEN,
                                    max_frame=MAX_FRAME_LEN))
    lines.append("/* --- Frame vectors (sorted by id ascending) --- */\n")
    lines.append("static const frame_vector_t FRAME_VECTORS[] PROGMEM = {\n")

    for vec in vectors:
        payload_bytes = _parse_hex_bytes(vec["payload_hex"], "payload_hex", vec["id"])
        frame_bytes   = _parse_hex_bytes(vec["frame_hex"],   "frame_hex",   vec["id"])
        payload_arr   = _bytes_to_cpp_array(payload_bytes)
        frame_arr     = _bytes_to_cpp_array(frame_bytes)
        lines.append(
            f"    {{ 0x{vec['id']:02X}, {payload_arr}, {len(payload_bytes)}, "
            f"{frame_arr}, {len(frame_bytes)} }},  /* {vec['name']} */\n"
        )

    lines.append("};\n")
    lines.append(
        f"\nstatic const uint16_t FRAME_VECTOR_COUNT PROGMEM = {count};\n"
    )
    lines.append(_CPP_HEADER_GUARD_CLOSE)

    return "".join(lines)


# ---------------------------------------------------------------------------
# 3b. python-vectors emitter — firestarter/frame_vectors.py
# ---------------------------------------------------------------------------

_PY_BANNER = '''\
"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Firestarter -- v1.10 frame-vector catalog (host side)

DO NOT EDIT -- generated by tools/catalog/codegen_vectors.py from
              tools/catalog/frame-vectors.toml.
Re-run codegen after editing the canonical catalog.

Catalog version: {version}
Total vectors: {count}
"""

'''

_PY_IMPORTS = """\
from typing import NamedTuple


class FrameVector(NamedTuple):
    id: int
    name: str
    payload: bytes
    frame: bytes


"""


def _bytes_to_py_literal(data: bytes) -> str:
    """Emit a Python bytes literal suitable for inline use in a FrameVector arg.

    Uses bytes.fromhex with UPPER-CASE hex (LCAT-05 determinism contract).
    Returns just the hex string — the caller wraps it in the correct
    bytes.fromhex(...) call form (inline or wrapped) based on line length.
    """
    return data.hex().upper()


def _bytes_to_py_field(field_name: str, data: bytes, indent: str = "        ") -> str:
    """Emit one FrameVector keyword argument line (or wrapped block).

    Chooses between:
      {indent}{field_name}=b"",
      {indent}{field_name}=bytes.fromhex("SHORT"),
      {indent}{field_name}=bytes.fromhex(
      {indent}    "LONG_HEX_STRING"
      {indent}),
    so that the emitted text is ruff-format-stable at line-length=88.
    """
    if not data:
        return f'{indent}{field_name}=b"",\n'
    hex_str = _bytes_to_py_literal(data)
    # Test if the single-line form fits within 88 chars.
    # Line: '{indent}{field_name}=bytes.fromhex("{hex_str}"),'
    single_line = f'{indent}{field_name}=bytes.fromhex("{hex_str}"),'
    if len(single_line) <= 88:
        return single_line + "\n"
    # Wrapped form: ruff wraps long args to the next line with 4 extra spaces.
    inner_indent = indent + "    "
    return (
        f'{indent}{field_name}=bytes.fromhex(\n'
        f'{inner_indent}"{hex_str}"\n'
        f'{indent}),\n'
    )


def emit_python_vectors(catalog: dict) -> str:
    vectors = _sorted_vectors(catalog)
    version = catalog["catalog"]["version"]
    count = len(vectors)

    lines: list[str] = []
    lines.append(_PY_BANNER.format(version=version, count=count))
    lines.append(_PY_IMPORTS)
    lines.append("# --- Frame vectors (sorted by id ascending) ---\n")
    lines.append("FRAME_VECTORS: list[FrameVector] = [\n")

    for vec in vectors:
        payload_bytes = _parse_hex_bytes(vec["payload_hex"], "payload_hex", vec["id"])
        frame_bytes   = _parse_hex_bytes(vec["frame_hex"],   "frame_hex",   vec["id"])
        # Emit trailing-comma multi-line style — ruff-format-stable at any
        # line length.  Use double-quoted name to satisfy quote-style="double".
        entry = (
            f"    FrameVector(\n"
            f"        id=0x{vec['id']:02X},\n"
            f"        name=\"{vec['name']}\",\n"
            + _bytes_to_py_field("payload", payload_bytes)
            + _bytes_to_py_field("frame", frame_bytes)
            + "    ),\n"
        )
        lines.append(entry)

    lines.append("]\n")

    return "".join(lines)


# ===========================================================================
# 4. LANGUAGE DISPATCH
# ===========================================================================

LANGUAGE_EMITTERS = {
    "cpp-vectors":    emit_cpp_vectors,
    "python-vectors": emit_python_vectors,
}

# ===========================================================================
# 5. CLI
# ===========================================================================


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Firestarter frame-vector catalog codegen (v1.10)"
    )
    p.add_argument(
        "--catalog",
        type=Path,
        required=True,
        help="Path to frame-vectors.toml",
    )
    p.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Output file path (required unless --check)",
    )
    p.add_argument(
        "--language",
        choices=list(LANGUAGE_EMITTERS.keys()),
        default=None,
        help="Output language (required unless --check)",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Validate the catalog and exit 0/1. No files written.",
    )
    return p


def main() -> int:
    args = _build_argparser().parse_args()

    if not args.catalog.is_file():
        print(f"ERROR: catalog file not found: {args.catalog}", file=sys.stderr)
        return 2

    try:
        catalog = _load_catalog(args.catalog)
    except tomllib.TOMLDecodeError as e:
        print(f"ERROR: failed to parse TOML in {args.catalog}: {e}",
              file=sys.stderr)
        return 1

    try:
        validate_catalog(catalog)
    except CatalogError as e:
        print(f"ERROR: catalog validation failed: {e}", file=sys.stderr)
        return 1

    if args.check:
        n = len(catalog["vectors"])
        print(f"OK: catalog valid ({n} vectors, version "
              f"{catalog['catalog']['version']}).")
        return 0

    if args.target is None or args.language is None:
        print("ERROR: --target and --language are required unless --check.",
              file=sys.stderr)
        return 2

    emitter = LANGUAGE_EMITTERS[args.language]
    output = emitter(catalog)

    args.target.parent.mkdir(parents=True, exist_ok=True)
    # LF endings guaranteed on all platforms (LCAT-05):
    args.target.write_text(output, encoding="utf-8", newline="\n")
    print(f"OK: wrote {args.target} ({args.language}, "
          f"{len(catalog['vectors'])} vectors).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
