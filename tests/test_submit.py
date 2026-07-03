"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

Unit tests for firestarter.submit (v1.21 Phase 113).

No PATH, network, or browser is ever touched -- every seam (`which_fn`,
`run_fn`) is injected with a `Mock`.
"""

from __future__ import annotations

from firestarter import submit

# ---------------------------------------------------------------------------
# Task 1: sanitize_dict -- one test per leak vector (A3 fails-open discipline)
# ---------------------------------------------------------------------------


def test_sanitize_home_dir_path():
    d = {"reason": "failed reading /home/alice/scratch/file.bin"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "/home/<user>/scratch/file.bin" in out["reason"]
    assert "alice" not in out["reason"]


def test_sanitize_users_path():
    d = {"reason": "failed at /Users/alice/Desktop/dump.bin"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "/Users/<user>/Desktop/dump.bin" in out["reason"]
    assert "alice" not in out["reason"]


def test_sanitize_windows_users_path():
    d = {"reason": r"temp file at C:\Users\alice\AppData\Local\Temp\x.bin"}
    out = submit.sanitize_dict(d, user="somebody")
    assert r"C:\Users\<user>\AppData\Local\Temp\x.bin" in out["reason"]
    assert "alice" not in out["reason"]


def test_sanitize_windows_users_path_case_insensitive_drive():
    d = {"reason": r"temp file at c:\Users\alice\AppData"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "alice" not in out["reason"]
    assert "<user>" in out["reason"]


def test_sanitize_dev_tty_acm():
    d = {"reason": "no response from /dev/ttyACM0"}
    out = submit.sanitize_dict(d, user="somebody")
    assert out["reason"] == "no response from /dev/tty<redacted>"


def test_sanitize_dev_tty_usb():
    d = {"reason": "port /dev/ttyUSB1 timed out"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "/dev/tty<redacted>" in out["reason"]
    assert "ttyUSB1" not in out["reason"]


def test_sanitize_dev_tty_macos():
    d = {"reason": "port /dev/tty.usbserial-A1 timed out"}
    out = submit.sanitize_dict(d, user="somebody")
    assert "/dev/tty<redacted>" in out["reason"]
    assert "usbserial-A1" not in out["reason"]


def test_sanitize_com_port():
    d = {"reason": "no response from COM3"}
    out = submit.sanitize_dict(d, user="somebody")
    assert out["reason"] == "no response from COM<redacted>"


def test_sanitize_tmp_path():
    d = {"reason": "wrote scratch file to /tmp/firestarter-xyz123/dump.bin"}
    out = submit.sanitize_dict(d, user="somebody")
    assert out["reason"] == "wrote scratch file to /tmp/<redacted>"


def test_sanitize_username():
    d = {"reason": "run by alicetest on this machine"}
    out = submit.sanitize_dict(d, user="alicetest")
    assert out["reason"] == "run by <user> on this machine"


def test_sanitize_username_too_short_not_scrubbed():
    # len(user) < 3 -- guard against over-scrubbing a short/common token.
    d = {"reason": "the value is ab and stays ab"}
    out = submit.sanitize_dict(d, user="ab")
    assert out["reason"] == "the value is ab and stays ab"


def test_sanitize_bytes_leaf_base64_encoded():
    d = {"raw": b"\x00\x01\x02binary"}
    out = submit.sanitize_dict(d, user="somebody")
    assert isinstance(out["raw"], str)
    assert out["raw"] == "AAECYmluYXJ5"


def test_sanitize_clean_value_passes_through():
    d = {"chip": "W27C512", "count": 3, "flag": True, "nothing": None}
    out = submit.sanitize_dict(d, user="somebody")
    assert out == d


def test_sanitize_nested_structure():
    d = {
        "steps": [
            {"op": "read", "reason": "ok at /home/alice/x"},
            {"op": "write", "reason": "port /dev/ttyACM0 gone"},
        ],
        "nested": {"deep": {"path": "/tmp/scratch.bin"}},
    }
    out = submit.sanitize_dict(d, user="alice")
    assert out["steps"][0]["reason"] == "ok at /home/<user>/x"
    assert out["steps"][1]["reason"] == "port /dev/tty<redacted> gone"
    assert out["nested"]["deep"]["path"] == "/tmp/<redacted>"


def test_sanitize_does_not_mutate_input():
    original = {"reason": "path /home/alice/x", "steps": [{"reason": "COM3 gone"}]}
    snapshot = {"reason": "path /home/alice/x", "steps": [{"reason": "COM3 gone"}]}
    submit.sanitize_dict(original, user="alice")
    assert original == snapshot


def test_sanitize_uses_getpass_default_when_user_omitted(monkeypatch):
    monkeypatch.setattr(submit.getpass, "getuser", lambda: "ciuser")
    d = {"reason": "run by ciuser here"}
    out = submit.sanitize_dict(d)
    assert out["reason"] == "run by <user> here"
