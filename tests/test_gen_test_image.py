"""Pinning tests for tools/gen_test_image.py.

Verifies the four core properties required by Phase 82 Plan 01 Task 1:
  (a) output length == size_bytes
  (b) two calls with the same seed are byte-identical (determinism)
  (c) seed=1 output != seed=2 output (distinct seeds, non-vacuous A->B proof)
  (d) output is neither all-0xFF nor all-0x00 (non-trivial content)
"""

import hashlib
import sys
from pathlib import Path

import pytest

# Add the tools directory to sys.path so gen_test_image is importable as a module
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from gen_test_image import generate_image  # noqa: E402, I001  (after sys.path mutation)


# ---------------------------------------------------------------------------
# (a) Size correctness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size_bytes", [1, 128, 1024, 65536])
def test_output_length_equals_size_bytes(size_bytes: int) -> None:
    """generate_image must return exactly size_bytes bytes."""
    data = generate_image(size_bytes, seed=1)
    assert len(data) == size_bytes


# ---------------------------------------------------------------------------
# (b) Determinism — same (size_bytes, seed) -> byte-identical
# ---------------------------------------------------------------------------


def test_same_seed_is_deterministic() -> None:
    """Two calls with the same seed produce byte-identical output."""
    size = 1024
    first = generate_image(size, seed=1)
    second = generate_image(size, seed=1)
    assert first == second


def test_determinism_confirmed_by_sha256() -> None:
    """SHA-256 of two runs with the same seed must be identical."""
    size = 65536
    sha_a = hashlib.sha256(generate_image(size, seed=1)).hexdigest()
    sha_b = hashlib.sha256(generate_image(size, seed=1)).hexdigest()
    assert sha_a == sha_b


# ---------------------------------------------------------------------------
# (c) Distinct seeds produce different output
# ---------------------------------------------------------------------------


def test_distinct_seeds_produce_different_output() -> None:
    """seed=1 and seed=2 must produce different content (non-vacuous A->B proof)."""
    size = 1024
    image_a = generate_image(size, seed=1)
    image_b = generate_image(size, seed=2)
    assert image_a != image_b


# ---------------------------------------------------------------------------
# (d) Non-trivial content — not all-0xFF and not all-0x00
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2])
def test_output_is_not_all_0xff(seed: int) -> None:
    """Output must not be a uniform 0xFF fill (an all-FF image cannot prove writes landed)."""
    data = generate_image(128, seed=seed)
    assert data != bytes([0xFF] * 128)


@pytest.mark.parametrize("seed", [1, 2])
def test_output_is_not_all_0x00(seed: int) -> None:
    """Output must not be a uniform 0x00 fill."""
    data = generate_image(128, seed=seed)
    assert data != bytes([0x00] * 128)


# ---------------------------------------------------------------------------
# CLI smoke test (subprocess-free, tests __main__ path via importlib)
# ---------------------------------------------------------------------------


def test_cli_writes_file_and_prints_sha256(tmp_path: Path) -> None:
    """Simulate the CLI: write a file and verify the printed SHA-256 matches."""
    import importlib.util
    import io
    from contextlib import redirect_stdout
    from unittest.mock import patch

    output_file = tmp_path / "test_img.bin"
    cli_args = ["gen_test_image.py", "1024", "1", str(output_file)]

    spec = importlib.util.spec_from_file_location(
        "gen_test_image_cli",
        Path(__file__).parent.parent / "tools" / "gen_test_image.py",
    )
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    buf = io.StringIO()
    with patch("sys.argv", cli_args), redirect_stdout(buf):
        mod.main()  # type: ignore[attr-defined]

    printed_sha = buf.getvalue().strip()
    assert len(printed_sha) == 64, f"Expected 64-char hex SHA-256, got: {printed_sha!r}"

    written_data = output_file.read_bytes()
    assert len(written_data) == 1024
    expected_sha = hashlib.sha256(written_data).hexdigest()
    assert printed_sha == expected_sha
