"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Information Module
"""

import json
import logging
import re
from typing import Dict  # noqa: UP035

from firestarter.database import EpromDatabase, format_mv  # Changed import
from firestarter.ic_layout import (
    CAN_ERASE_NOT_SUPPORTED,
    CAN_ERASE_YES,
    EpromSpecBuilder,
)
from firestarter.vpp_display import shows_programming_vpp

logger = logging.getLogger("EpromConsolePresenter")

# The shield's programming VCC rail is fixed hardware — it is not a firmware
# constant with a header counterpart, so it lives here rather than in
# firestarter/constants.py (that module mirrors three firmware headers and
# every entry there must change in the same commit pair as its header; this
# value has none).
_SHIELD_FIXED_VCC_MV = 5000


def _format_v_prose(mv: int) -> str:
    """Render a millivolt integer as prose voltage, e.g. `6.0 V` for 6000.

    Delegates to `format_mv` and re-spells only the unit suffix (lowercase
    `v` -> space + capital `V`), so the numeric rendering has exactly one
    definition in the project. The field-row cell spells the same value
    `6.0v` via `format_mv` directly; the warning prose spells it `6.0 V`.
    Both spellings are deliberate (D-04) and both call the same formatter.
    """
    return format_mv(mv).replace("v", " V")


def programming_vcc_over_rail_mv(raw_config_data: dict | None) -> int | None:
    """Return the decoded `electrical.vdd_mv` when it exceeds the shield's
    fixed rail, else `None`.

    D-03: the predicate is `vdd_mv > _SHIELD_FIXED_VCC_MV`, not
    `vdd_mv > vcc_mv` — the shield delivers a fixed rail regardless of what a
    row's `vcc_mv` says.

    D-06: this must fail open. `raw_config_data` may be `None`, its
    `electrical` key may be absent, and `vdd_mv` may be absent, `None`, `0`,
    or (from a hand-edited `~/.firestarter/database.json` override) a
    non-numeric string. `EpromDatabase` merges that operator file over the
    packaged database unless `skip_local_override=True`, and `_map_data`'s
    own comment names a stale override missing electrical keys as a real
    anticipated case — so the fail-open branch below is not speculative,
    even though the live 746-row shipped database has zero rows that reach
    it (every row gets a non-zero int from `build_db.py`'s own `.get(...,
    5000)` default).

    Chained `.get()` is used deliberately, unlike `_map_data`'s direct
    indexing on `vcc_mv`/`vpp_mv` — that style exists so a stale override
    fails loudly, which is the opposite of what D-06 requires here.
    """
    if not raw_config_data:
        return None
    try:
        vdd_mv = int((raw_config_data.get("electrical") or {}).get("vdd_mv", 0) or 0)
    except (TypeError, ValueError):
        return None
    if vdd_mv > _SHIELD_FIXED_VCC_MV:
        return vdd_mv
    return None


class EpromConsolePresenter:
    """
    Manages the retrieval, structuring, and console presentation of EPROM information.
    It uses EpromSpecBuilder to get detailed EPROM specifications,
    constructs display-specific elements like DIP layouts, and formats
    the output for the command-line interface.
    """

    def __init__(self, db_instance: EpromDatabase):
        self.db = db_instance
        self.spec_builder = EpromSpecBuilder(db_instance)  # Instantiate renamed class

    def _json_output_formatted(self, data: dict) -> str:
        """
        Formats a dictionary as a pretty-printed JSON string with special formatting for lists of numbers.
        """  # noqa: E501
        json_str = json.dumps(data, indent=4)
        # Compact lists of numbers (e.g., bus configurations)
        json_str = re.sub(
            # Regex to find a list that contains numbers, commas, and whitespace.
            # Group 1: Opening bracket and any leading whitespace/newline.
            # Group 2: The actual content (numbers, commas, whitespace).
            # Group 3: Closing bracket and any trailing whitespace/newline.
            r"(\[)[\s\n]*([\d,\s\n]+?)[\s\n]*(\])",  # Adjusted regex
            lambda match: (
                match.group(1)
                + ", ".join(re.findall(r"\d+", match.group(2)))
                + match.group(3)
            ),
            json_str,
        )
        return json_str

    def _clean_config_for_export(self, raw_config: dict) -> dict:
        """
        Cleans and structures raw EPROM config for JSON export.
        """
        cleaned = {}
        # Define expected keys and their defaults or how to fetch them
        key_map = {
            "name": "Unknown",
            "pin-count": 0,
            "can-erase": False,
            "has-chip-id": False,
            # "chip-id": "0x0", # Only if has-chip-id is True
            # "pin-map": "default", # Handled below with variant
            "protocol-id": "0x0",
            "memory-size": "0x0",
            "type": "unknown",  # String type from raw JSON
            "voltages": {},
            "pulse-delay": "0",
            "flags": "0x00",
            "verified": False,
        }
        for key, default_val in key_map.items():
            cleaned[key] = raw_config.get(key, default_val)

        if cleaned.get("has-chip-id"):
            cleaned["chip-id"] = raw_config.get("chip-id", "0x0")
        else:  # Remove chip-id if not present
            if "chip-id" in cleaned:
                del cleaned["chip-id"]

        cleaned["pin-map"] = raw_config.get(
            "pin-map", raw_config.get("variant", "default")
        )

        # Clean up voltages sub-dictionary if it exists
        if "voltages" in cleaned and isinstance(cleaned["voltages"], dict):
            cleaned["voltages"].pop("vdd", None)  # Remove 'vdd' if present
            # 'vcc' is kept as per original logic, 'vpp' is also kept
        return cleaned

    def prepare_detailed_eprom_data(
        self,
        eprom_name: str,  # For logging and titles
        eprom_details: dict | None,
        eprom_data_for_programmer: dict | None,
        raw_config_data: dict | None,
        manufacturer: str | None,  # Pre-fetched from db.get_eprom_config()
        include_export_config: bool = False,
        include_adapter: bool = False,
    ) -> Dict | None:  # noqa: UP006
        """
        Prepares a comprehensive data structure for a specific EPROM,
        ready for presentation. It fetches raw specifications, constructs
        display elements like the DIP layout, and optionally includes
        export or programmer-specific configurations.
        """
        if not eprom_details:
            logger.error(f"EPROM '{eprom_name}' not found in the database.")
            return None

        # Get base specifications from EpromSpecBuilder.
        # Pass electrical_type from the raw config so both use ground-truth.
        electrical_type = None
        if raw_config_data:
            electrical_type = raw_config_data.get("electrical", {}).get("type") or None
        eprom_specifications = self.spec_builder.build_specifications(
            eprom_details, electrical_type=electrical_type
        )
        if not eprom_specifications:
            logger.error(f"Could not generate layout data for {eprom_name}.")
            return None  # Should not happen if eprom_details_full was valid

        # Start with data from EpromSpecBuilder
        combined_data = eprom_specifications

        if not eprom_details.get("bus-config"):
            combined_data["no_pinout_warning"] = True

        # The dip_layout is now expected to be directly provided by spec_builder
        if not combined_data.get("dip_layout"):
            logger.warning(
                f"DIP layout not generated by spec_builder for {eprom_name}."
            )

        # Inject support_status + unsupported_reason into combined_data for
        # non-supported chips. Gated on support_status != "supported"
        # so supported chips get no new line, which would be a snapshot regression.
        if raw_config_data:
            ss = raw_config_data.get("support_status", "supported")
            if ss != "supported":
                combined_data["support_status"] = ss
                combined_data["unsupported_reason"] = raw_config_data.get(
                    "unsupported_reason", ""
                )
                # `erase` refuses a chip that is not supported, so "Can be erased" must not
                # say yes.
                if combined_data.get("can_erase_str") == CAN_ERASE_YES:
                    combined_data["can_erase_str"] = CAN_ERASE_NOT_SUPPORTED

        # Inject the elevated-programming-supply row/warning data into
        # combined_data. Gated on the predicate returning a value, so a part
        # at or below the shield's rail gets no new key at all — D-06's
        # fail-open, and the same reason the 462 at-or-below-5000 rows keep
        # byte-stable `info` output. Mirrors the support_status injection
        # immediately above, which uses the same raw_config_data seam.
        if raw_config_data:
            over_rail_mv = programming_vcc_over_rail_mv(raw_config_data)
            if over_rail_mv is not None:
                combined_data["programming_vcc_mv"] = over_rail_mv
                combined_data["programming_vcc_str"] = format_mv(over_rail_mv)

        if eprom_data_for_programmer:
            combined_data["programmer_config_json_str"] = self._json_output_formatted(
                eprom_data_for_programmer
            )

        if include_adapter:
            pin_count = eprom_details.get("pin-count")
            pinout_key = eprom_details.get("pin-map")
            if pin_count and pinout_key:
                table = self.db.get_adapter_table(pin_count, pinout_key)
                if table:
                    combined_data["adapter_table"] = {
                        "pinout_name": pinout_key,
                        "pin_count": pin_count,
                        "rows": table,
                    }

        if include_export_config:
            export_details = self._prepare_export_configuration_data(
                raw_config_data, manufacturer, eprom_name
            )
            if export_details:
                combined_data["export_config"] = export_details

        return combined_data

    def _prepare_export_configuration_data(
        self,
        raw_config_data: Dict | None,  # noqa: UP006
        manufacturer: str | None,
        eprom_name: str,
    ) -> Dict | None:  # noqa: UP006
        """
        Prepares EPROM and Pin Map configuration data formatted for export.
        """
        if not (raw_config_data and manufacturer):
            logger.error(f"Could not retrieve raw config for {eprom_name} for export.")
            return None

        cleaned_raw_config = self._clean_config_for_export(raw_config_data)
        export_eprom_data_dict = {manufacturer: [cleaned_raw_config]}

        export_data_to_return = {
            "eprom_config_title": f"{cleaned_raw_config['name']} EPROM config (for ~/.firestarter/database.json):",  # noqa: E501
            "eprom_config_json_str": self._json_output_formatted(
                export_eprom_data_dict
            ),
        }

        pin_map_id = cleaned_raw_config.get("pin-map")
        pin_count = cleaned_raw_config.get("pin-count")
        if not pin_map_id == None and pin_count:  # noqa: E711
            pin_map_details = self.db.get_pin_map(pin_count, pin_map_id)
            if pin_map_details:
                export_pin_map_dict = {
                    str(pin_count): {str(pin_map_id): pin_map_details}
                }
                export_data_to_return["pin_map_config_title"] = (
                    f"{eprom_name} Pin Map (for pin-maps.json):"
                )
                export_data_to_return["pin_map_config_json_str"] = (
                    self._json_output_formatted(export_pin_map_dict)
                )
            else:
                logger.warning(
                    f"Pin map '{pin_map_id}' for {pin_count}-pin {eprom_name} not found for export."  # noqa: E501
                )
        return export_data_to_return

    def present_eprom_details(
        self,
        chip_data: Dict | None,  # noqa: UP006
        show_export_config: bool = False,
        show_adapter: bool = False,
    ):
        """
        Formats and prints the structured chip data to the console.
        This method now incorporates the logic from the former print_structured_chip_data.
        """  # noqa: E501
        if not chip_data:
            # prepare_detailed_eprom_data already logs an error if chip not found
            return

        pos = 20  # For alignment
        logger.info(f"{'Eprom Info': <{pos}}{chip_data.get('verified_str', '')}")
        logger.info(f"{'Name:': <{pos}}{chip_data.get('name')}")
        logger.info(f"{'Manufacturer:': <{pos}}{chip_data.get('manufacturer')}")
        # Render the status-specific support block for non-supported chips.
        # Gated on chip_data.get("support_status") — only present when != "supported",
        # so the injection guard prevents a "Support status: supported" line.
        if chip_data.get("support_status"):
            support_status = chip_data["support_status"]
            logger.warning("Support status:      " + support_status)
            unsupported_reason = chip_data.get("unsupported_reason", "")
            if unsupported_reason:
                logger.warning("Reason:              " + unsupported_reason)
        logger.info(f"{'Number of pins:': <{pos}}{chip_data.get('pin_count')}")
        logger.info(f"{'Memory size': <{pos}}{chip_data.get('memory_size_hex')}")
        logger.info(f"{'Type:': <{pos}}{chip_data.get('type_str')}")
        if "can_erase_str" in chip_data:
            logger.info(f"{'Can be erased:': <{pos}}{chip_data.get('can_erase_str')}")
        logger.info(f"{'VCC:': <{pos}}{chip_data.get('vcc_str')}")
        if "programming_vcc_str" in chip_data:
            logger.info(
                f"{'Programming VCC:': <{pos}}{chip_data.get('programming_vcc_str')}"
            )
        if "vpp_str" in chip_data:
            logger.info(f"{'VPP:': <{pos}}{chip_data.get('vpp_str')}")
        if "chip_id_hex" in chip_data:
            logger.info(f"{'Chip ID:': <{pos}}{chip_data.get('chip_id_hex')}")
        if "pulse_delay_us_str" in chip_data:
            logger.info(
                f"{'Pulse delay:': <{pos}}{chip_data.get('pulse_delay_us_str')}"
            )

        # This part's programming supply decodes above the shield's fixed
        # rail (D-03). Gated on the same "programming_vcc_str" key the row
        # above is gated on, so the row and the warning can never disagree
        # about whether a part is elevated. D-05: state and proceed — this
        # must never become a refusal. D-04 (amended 2026-09-19): the
        # sentence states what the value decodes to, not what the part
        # requires — DECODE-NOTES.md § 9 established that most of these
        # values are a programmer rail-table slot rather than a transcribed
        # datasheet requirement, so the sentence claims exactly what is
        # known and nothing more.
        if "programming_vcc_str" in chip_data:
            rail_v = _format_v_prose(_SHIELD_FIXED_VCC_MV)
            logger.warning("")
            logger.warning(
                f"WARNING: Programming VCC decodes to "
                f"{_format_v_prose(chip_data['programming_vcc_mv'])}; "
                f"using {rail_v}."
            )

        if chip_data.get("no_pinout_warning"):
            logger.warning("")
            logger.warning(
                "WARNING: No pinout defined for this chip — hardware operations will fail."  # noqa: E501
            )
            logger.warning(
                "Add a pin-map entry to ~/.firestarter/pin-maps.json to enable it."
            )

        if chip_data.get("dip_layout"):
            layout = chip_data["dip_layout"]
            logger.info("")
            logger.info(f"       {layout.get('title')}")
            logger.info(layout.get("dent"))
            for pair in layout.get("pin_pairs", []):
                logger.info(
                    f"  {pair['left_name']:<3} -| {pair['left_num']:2}     {pair['right_num']:2} |- {pair['right_name']:<6}"  # noqa: E501
                )
            logger.info(layout.get("bottom"))

        for key, block in chip_data.get("jumpers", {}).items():
            logger.info("")
            logger.info(f"Jumper config (Rev {key}):")
            for line in block["drawing"]:
                logger.info(line)
            for note in block["notes"]:
                logger.info(f"  Note: {note}")
        if chip_data.get("jumper_note"):
            logger.info("")
            logger.info(f"Jumper config: {chip_data['jumper_note']}")

        if chip_data.get("protocol_info"):
            protocol = chip_data["protocol_info"]
            logger.info("")
            logger.info(f"Protocol: {protocol['type']} (ID: {protocol['id_hex']})")
            if "description_points" in protocol:
                for point in protocol["description_points"]:
                    logger.info(f"  - {point}")

        if chip_data.get("flags_info"):
            flags = chip_data["flags_info"]
            logger.info("")
            logger.info(f"Flags: {flags['value_hex']}")
            if flags["properties"]:
                for prop in flags["properties"]:
                    logger.info(f"  - {prop}")

        if "programmer_config_json_str" in chip_data:
            logger.debug(
                f"\nProgrammer Config JSON:\n{chip_data['programmer_config_json_str']}"
            )

        if show_adapter and chip_data.get("adapter_table"):
            tbl = chip_data["adapter_table"]
            rows = tbl["rows"]
            n = tbl["pin_count"]
            half = (n + 1) // 2
            logger.info("")
            logger.info(f"Adapter pin wiring — {tbl['pinout_name']} ({n}-pin):")
            logger.info(f"  {'Pin':>4}  {'Signal':<10}    {'Pin':>4}  Signal")
            logger.info(f"  {'':->4}  {'':->10}    {'':->4}  ----------")
            for i in range(half):
                left_pin, left_sig = rows[i]
                right_pin, right_sig = rows[n - 1 - i]
                logger.info(
                    f"  {left_pin:>4}  {left_sig:<10}    {right_pin:>4}  {right_sig}"
                )

        if show_export_config and "export_config" in chip_data:
            export_details = chip_data["export_config"]
            logger.info(
                f"\n{export_details['eprom_config_title']}\n{export_details['eprom_config_json_str']}"
            )
            if "pin_map_config_title" in export_details:
                logger.info(
                    f"\n{export_details['pin_map_config_title']}\n{export_details['pin_map_config_json_str']}"
                )


# --- Helper function for printing EPROM list (for testing/CLI) ---
def print_eprom_list_table(eproms_data: list, spec_builder: EpromSpecBuilder):
    """Prints a list of EPROM data in a table format. For CLI/testing.

    Column layout:
    - Name: dynamic width clamped to [13, 20]; names longer than 20 chars are
      truncated with a trailing ellipsis ('…') that counts toward the 20-char cap.
    - Manufacturer: fixed 17; Pins: fixed 5; Chip ID: fixed 11; Type: fixed 12.
    - VPP: fixed 5 (every voltage string is 5 chars; '-' padded to 5).
    - Type is sourced from electrical-type via spec_builder.resolve_type_label.
    - VPP shown only when vpp_mv > 0 AND electrical-type != 'SRAM' (parity gate).
    """
    if not eproms_data:
        logger.info("No EPROMs to display.")
        return

    # Dynamic Name column width clamped to [13, 20].
    # Compute the widest rendered name (including any [!] suffix) across all rows,
    # then clamp to the [13, 20] range.  Names that would exceed 20 chars are
    # truncated to 19 chars + '…' (ellipsis counts toward the 20-char cap).
    def _render_name(raw_name: str, has_bus_config: bool) -> str:
        """Return the name string as it will be rendered in the table cell."""
        name = raw_name
        if not has_bus_config:
            name = (name[:11] + "[!]") if len(name) > 11 else f"{name}[!]"
        if len(name) > 20:
            name = name[:19] + "…"  # ellipsis counts toward the 20-char cap
        return name

    rendered_names = [
        _render_name(ic.get("name", ""), bool(ic.get("bus-config")))
        for ic in eproms_data
    ]
    name_w = max(13, min(20, max((len(n) for n in rendered_names), default=13)))

    divider = f"+{'':-<{name_w + 1}}+{'':-<18}+{'':-<6}+{'':-<12}+{'':-<13}+{'':-<6}+"
    logger.info(divider)
    logger.info(
        f"| {'Name': <{name_w}}| {'Manufacturer': <17}| {'Pins': <5}| {'Chip ID': <11}| {'Type': <12}| {'VPP': <5}|"  # noqa: E501
    )
    logger.info(divider)
    for name, ic in zip(rendered_names, eproms_data):
        chip_id_str = f"0x{ic.get('chip-id', 0):04X}" if ic.get("chip-id") else ""

        # The info view calls the same predicate, so the two views cannot disagree.
        if shows_programming_vpp(ic.get("protocol-id"), ic.get("vpp_mv")):
            vpp_str = format_mv(int(ic["vpp_mv"]))
        else:
            vpp_str = "-"

        # Type via the single shared helper (resolve_type_label).
        type_str = spec_builder.resolve_type_label(
            ic.get("electrical-type"),
            ic.get("protocol-id"),
        )
        # The Type column is a fixed-width 12-char cell. The
        # protocol-based fallback in resolve_type_label can return labels of
        # 13-39 chars (e.g. legacy/operator-override entries lacking
        # electrical-type), which would rupture table alignment. Clamp to 12
        # so the column stays aligned. The info view (present_eprom_details)
        # is a free-form line and intentionally shows the full label, so it
        # is NOT clamped — parity is about the shared label *source*,
        # not the per-view presentation width.
        type_str_display = type_str[:12]

        logger.info(
            f"| {name: <{name_w}}| {ic.get('manufacturer', ''): <17}|{ic.get('pin-count', 0): >5} | {chip_id_str: <11}| {type_str_display: <12}| {vpp_str: <5}|"  # noqa: E501
        )
    logger.info(divider)


# Standalone test function
def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # db.init_db() # Old way
    db_instance = EpromDatabase()  # New way
    presenter = EpromConsolePresenter(db_instance)

    logger.info("\n--- Searching for '27C' ---")
    # Directly use the database method for searching
    search_results = db_instance.search_eprom("27C", include_unverified=True)
    if not search_results:
        logger.info(f"No EPROMs found matching '27C'.")  # noqa: F541

    if search_results:
        print_eprom_list_table(search_results, presenter.spec_builder)
    else:
        logger.info("No results for '27C'.")

    # logger.info("\n--- Listing all (first few if many) ---")
    # all_eproms = presenter.get_all_eproms_data()
    # if all_eproms:
    #     print_eprom_list_table(all_eproms[:10], presenter.spec_builder) # Print first 10  # noqa: E501

    logger.info("\n--- Info for 27C256 (with export) ---")
    eprom_name_test = "2732"
    details_full = db_instance.get_eprom(eprom_name_test)
    data_prog = None
    if details_full:
        data_prog = db_instance.convert_to_programmer(details_full)
    raw_conf, manuf = db_instance.get_eprom_config(eprom_name_test)

    if details_full and data_prog and raw_conf:
        structured_details = presenter.prepare_detailed_eprom_data(
            eprom_name_test,
            details_full,
            data_prog,
            raw_conf,
            manuf,
            include_export_config=True,
        )
        if structured_details:
            presenter.present_eprom_details(structured_details, show_export_config=True)


if __name__ == "__main__":
    main()
