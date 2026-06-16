"""
Project Name: Firestarter
Copyright (c) 2024 Henrik Olsson

Permission is hereby granted under MIT license.

EPROM Information Module
"""

import json
import logging
import re
from typing import Dict, Optional  # noqa: UP035

from firestarter.database import EpromDatabase  # Changed import
from firestarter.ic_layout import EpromSpecBuilder  # Import renamed class

logger = logging.getLogger("EpromConsolePresenter")


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
        eprom_details: Optional[
            Dict  # noqa: UP006
        ],  # Pre-fetched from db.get_eprom(name)  # noqa: UP006
        eprom_data_for_programmer: Optional[
            Dict  # noqa: UP006
        ],  # Pre-fetched from db.get_eprom(name)
        raw_config_data: Optional[
            Dict  # noqa: UP006
        ],  # Pre-fetched from db.get_eprom_config()  # noqa: UP006
        manufacturer: Optional[str],  # Pre-fetched from db.get_eprom_config()
        include_export_config: bool = False,
        include_adapter: bool = False,
    ) -> Optional[Dict]:  # noqa: UP006
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
        # Pass electrical_type from the raw config so D-01/D-02 use ground-truth.
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
        # non-supported chips (DB-04 SC#1). Gated on support_status != "supported"
        # so supported chips get no new line (Pitfall 3 — avoids snapshot regression).
        if raw_config_data:
            ss = raw_config_data.get("support_status", "supported")
            if ss != "supported":
                combined_data["support_status"] = ss
                combined_data["unsupported_reason"] = raw_config_data.get(
                    "unsupported_reason", ""
                )

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
        raw_config_data: Optional[Dict],  # noqa: UP006
        manufacturer: Optional[str],
        eprom_name: str,
    ) -> Optional[Dict]:  # noqa: UP006
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
        chip_data: Optional[Dict],  # noqa: UP006
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
        # DB-04 SC#1: render status-specific support block for non-supported chips.
        # Gated on chip_data.get("support_status") — only present when != "supported"
        # (Pitfall 3: injection guard prevents a "Support status: supported" line).
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
        if "vpp_str" in chip_data:
            logger.info(f"{'VPP:': <{pos}}{chip_data.get('vpp_str')}")
        if "chip_id_hex" in chip_data:
            logger.info(f"{'Chip ID:': <{pos}}{chip_data.get('chip_id_hex')}")
        if "pulse_delay_us_str" in chip_data:
            logger.info(
                f"{'Pulse delay:': <{pos}}{chip_data.get('pulse_delay_us_str')}"
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

        for key, jumper_data in chip_data.get("jumpers", {}).items():
            logger.info("")
            logger.info(f"Jumper config (Rev {key}):")
            for jp, data in jumper_data.items():
                logger.info(
                    f"  {jp.upper()}: {data['display']} ({data['config_text']}, {data['pin_text']} = {data['selected_label']})"  # noqa: E501
                )

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

    Column layout (D-01, D-02, D-03, D-04 — Phase 61):
    - Name: dynamic width clamped to [13, 20]; names longer than 20 chars are
      truncated with a trailing ellipsis ('…') that counts toward the 20-char cap.
    - Manufacturer: fixed 17; Pins: fixed 5; Chip ID: fixed 11; Type: fixed 12.
    - VPP: fixed 5 (every voltage string is 5 chars; '-' padded to 5).
    - Type is sourced from electrical-type via spec_builder.resolve_type_label (D-04).
    - VPP shown only when vpp_mv > 0 AND electrical-type != 'SRAM' (D-03 parity gate).
    """
    if not eproms_data:
        logger.info("No EPROMs to display.")
        return

    # D-01: dynamic Name column width clamped to [13, 20].
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

        # D-03: VPP gate mirrors info view — show voltage only when
        # vpp_mv > 0 AND electrical-type != "SRAM".
        # Defensive int() coercion matches build_specifications (user-override entries
        # may store vpp_mv as a string).
        try:
            _vpp_mv = int(ic.get("vpp_mv", 0) or 0)
        except (TypeError, ValueError):
            _vpp_mv = 0
        _etype = ic.get("electrical-type", "")
        if _etype != "SRAM" and _vpp_mv > 0:
            # WR-02: mirror the info view's fallback ('N/A') so the two views
            # produce identical output when vpp_mv > 0 but vpp_volts is absent
            # (e.g. operator-override entries). Previously the list view fell
            # back to '-' here, diverging from info's 'N/A' (D-03 parity).
            vpp_str = f"{ic.get('vpp_volts', 'N/A')}v"
        else:
            vpp_str = "-"

        # D-04: Type via the single shared helper (resolve_type_label).
        type_str = spec_builder.resolve_type_label(
            ic.get("electrical-type"),
            ic.get("type", 0),
            ic.get("protocol-id"),
        )
        # WR-01: the Type column is a fixed-width 12-char cell. The
        # protocol-based fallback in resolve_type_label can return labels of
        # 13-39 chars (e.g. legacy/operator-override entries lacking
        # electrical-type), which would rupture table alignment. Clamp to 12
        # so the column stays aligned. The info view (present_eprom_details)
        # is a free-form line and intentionally shows the full label, so it
        # is NOT clamped — D-04 parity is about the shared label *source*,
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
