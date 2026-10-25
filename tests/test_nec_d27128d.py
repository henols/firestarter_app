"""Exact NEC D27128D record and shared 27128 read pinout."""

from firestarter.database import EpromDatabase


def test_d27128d_is_a_no_id_16k_nec_part() -> None:
    db = EpromDatabase(skip_local_override=True)
    chip = db.get_eprom("D27128D")
    assert chip is not None
    assert chip == db.get_eprom("UPD27128D")
    assert chip["manufacturer"] == "NEC"
    assert chip["memory-size"] == 16_384
    assert chip["pin-count"] == 28
    assert chip["protocol-id"] == 7
    assert chip["chip-id"] == 0
    assert chip["info-flags"] & 0x20 == 0
    assert chip["vpp_mv"] == 21_000
    assert chip["pulse-delay"] == 1_000
    assert chip["pin-map"] == "DIP28_2764"
    assert chip["bus-config"]["bus"] == list(range(14))
    assert chip["bus-config"]["rw-pin"] == 14


def test_27128_pinout_drives_pgm_on_read_without_local_override() -> None:
    db = EpromDatabase(skip_local_override=True)
    pinout = db.pin_maps["DIP28_2764"]["pins"]
    assert pinout["pgm-pin"] == [27]
    assert pinout["rw-pin"] == [27]
    assert pinout["address-bus-pins"][-1] == 26  # A13, unlike 8KB 2764
    for name in ("D27128D", "UPD27128", "AM27128A"):
        chip = db.get_eprom(name)
        assert chip is not None
        assert chip["bus-config"]["rw-pin"] == 14
