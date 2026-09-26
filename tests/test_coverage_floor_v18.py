"""GATE-1.8 coverage-floor restoration (v1.8 milestone close, Phase 43).

Phase 42 Plan 42-03 raised the CI coverage gate to ``--cov-fail-under=70`` with
only a ~0.1% margin. At the v1.8 milestone-close pre-flight the measured floor
slipped to 69.49% (coverage.py 7.14.x), tripping GATE-1.8 assert (f). These
tests exercise genuine error/edge branches that the existing suite left
uncovered — they restore a durable margin above 70% rather than padding it.

Targets (all real, reachable behavior):
- ``frame_parser._decode_param`` signed-int + ascii_str + unknown-type branches
- ``codec.format_message`` generic MSG_DEBUG sub-payload render + decode-error
  fall-through
- ``codec.decode_id_frame`` truncated-frame + param-shape-mismatch guards
- ``chip_resolver.resolve_chip`` default-database construction seam
- ``main.exit_gracefully`` SIGINT handler
- ``config.ConfigManager._save_config`` mkdir/write failure handling +
  ``set_value(None, persist=False)`` in-memory pop
"""

import pytest

from firestarter.codec import decode_id_frame, format_message
from firestarter.config import ConfigManager
from firestarter.frame_parser import _crc8_ccitt, _decode_param
from firestarter.messages import CATALOG, MSG_DEBUG, MSG_OK_REV


class TestDecodeParamUncoveredTypes:
    """``_decode_param`` signed-width + ascii_str + unknown-type branches."""

    def test_i8_negative(self):
        assert _decode_param("i8", bytes([0xFF]), 0) == (-1, 1)

    def test_i16_negative(self):
        assert _decode_param("i16", bytes([0xFF, 0xFF]), 0) == (-1, 2)

    def test_i32_negative(self):
        assert _decode_param("i32", bytes([0xFF, 0xFF, 0xFF, 0xFF]), 0) == (-1, 4)

    def test_ascii_str_decodes_length_prefixed(self):
        value, cursor = _decode_param("ascii_str", bytes([3]) + b"abc", 0)
        assert value == "abc"
        assert cursor == 4

    def test_unknown_ptype_raises(self):
        with pytest.raises(ValueError, match="Unknown param type"):
            _decode_param("not_a_type", b"", 0)


class TestFormatMessageDebugGenericRender:
    """``format_message`` MSG_DEBUG generic sub-payload path (non-DBG_CMD)."""

    def test_debug_subentry_with_params(self):
        # sub_id 0x18 -> "Checking VPP voltage %u mV" (u16 param).
        entry = CATALOG[MSG_DEBUG]
        result = format_message(MSG_DEBUG, [0x18, bytes([0x01, 0x2C])], entry)
        assert result == "Checking VPP voltage 300 mV"

    def test_debug_subentry_no_params(self):
        # sub_id 0x05 -> "Setup" (empty params -> returns the format verbatim).
        entry = CATALOG[MSG_DEBUG]
        result = format_message(MSG_DEBUG, [0x05, b""], entry)
        assert result == "Setup"

    def test_debug_subentry_decode_error_falls_through(self):
        # sub_id 0x18 needs a 2-byte u16; a 1-byte body raises struct.error,
        # which the renderer catches and returns None for generic fall-through.
        entry = CATALOG[MSG_DEBUG]
        result = format_message(MSG_DEBUG, [0x18, bytes([0x01])], entry)
        assert result is None

    def test_debug_unknown_subid_returns_none(self):
        entry = CATALOG[MSG_DEBUG]
        result = format_message(MSG_DEBUG, [0x7F, b""], entry)
        assert result is None


class TestDecodeIdFrameGuards:
    """``decode_id_frame`` defensive guards (DoS-resilience per T-06-12)."""

    def test_frame_too_short(self):
        assert decode_id_frame(1, b"\x04") is None

    def test_frame_length_mismatch(self):
        assert decode_id_frame(5, b"\x04\x00") is None

    def test_param_shape_mismatch(self):
        # MSG_OK_REV (0x04) declares param_bytes=2; build a CRC-valid frame
        # carrying only ONE param byte so the shape guard rejects it.
        msg_id = MSG_OK_REV
        params = bytes([0x00])
        crc = _crc8_ccitt(bytes([msg_id]) + params)
        body = bytes([msg_id]) + params + bytes([crc])
        assert decode_id_frame(len(body), body) is None


class TestResolveChipDefaultDatabase:
    """``resolve_chip`` default-DB construction seam (db is None branch)."""

    def test_default_db_resolves_known_chip(self):
        from firestarter.chip_resolver import resolve_chip

        config = resolve_chip("W27C512")
        assert isinstance(config, dict)
        assert "algorithm" in config
        assert "memory-size" in config


class TestExitGracefully:
    """``main.exit_gracefully`` SIGINT handler exits with status 1."""

    def test_exit_gracefully_raises_systemexit_1(self):
        from firestarter.main import cli, exit_gracefully, main

        assert main is cli
        with pytest.raises(SystemExit) as exc:
            exit_gracefully(2, None)
        assert exc.value.code == 1


class TestConfigSaveFailures:
    """``ConfigManager._save_config`` mkdir/write failure handling."""

    def test_save_config_mkdir_failure_is_logged_not_raised(
        self, tmp_path, monkeypatch
    ):
        missing = tmp_path / "no_such_dir"
        monkeypatch.setattr("firestarter.config.HOME_PATH", str(missing))
        ConfigManager._instances.clear()
        ConfigManager._initialized_configs.clear()
        cm = ConfigManager(config_filename="t_mkdir_fail.json")

        def boom(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr("firestarter.config.os.makedirs", boom)
        # persist=True triggers _save_config; mkdir failure is swallowed (logged).
        cm.set_value("port", "/dev/ttyACM0", persist=True)
        assert cm.get_value("port") == "/dev/ttyACM0"

    def test_save_config_write_failure_is_logged_not_raised(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("firestarter.config.HOME_PATH", str(tmp_path))
        ConfigManager._instances.clear()
        ConfigManager._initialized_configs.clear()
        cm = ConfigManager(config_filename="t_write_fail.json")

        import builtins

        real_open = builtins.open

        def bad_open(path, mode="r", *args, **kwargs):
            if "w" in mode:
                raise OSError("disk full")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", bad_open)
        cm.set_value("port", "/dev/ttyACM0", persist=True)
        assert cm.get_value("port") == "/dev/ttyACM0"

    def test_set_value_none_persist_false_pops_in_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("firestarter.config.HOME_PATH", str(tmp_path))
        ConfigManager._instances.clear()
        ConfigManager._initialized_configs.clear()
        cm = ConfigManager(config_filename="t_none_nopersist.json")
        cm.set_value("port", "/dev/ttyACM0", persist=False)
        cm.set_value("port", None, persist=False)
        assert cm.get_value("port") is None
