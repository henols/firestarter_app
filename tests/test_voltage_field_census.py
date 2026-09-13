"""D-12's census (RPT-B1, plan 181-09): proves by test -- not by sentence -- that no
code path assigns `voltage.vpp_mv` or `voltage.vpe_mv`, the two standalone rail slots
schema 2.0 deletes from `DiagnosticReport`.

The trap this census exists to avoid: the same two names are DATABASE fields on an
unrelated code path. `firestarter/ic_layout.py` reads a chip record's `vpp_mv` dict
key; and roughly twenty more textual hits land across `tests/test_check_dispatch_
invariants.py`, `tests/test_chip_resolver.py`, `tests/test_eprom_database.py`,
`tests/test_extra_chips_supplement.py`, `tests/test_diff_db_gate.py`, `tests/
test_sdp_capability.py`, `tests/test_wire_dict_equivalence.py` and `tests/
test_variant_decode_evidence_stability.py` -- every one a different field that
happens to be spelled the same way as the report's deleted key. A textual scan
cannot tell a report attribute from a database key and would fail open on exactly
the sites it should be scanning. This census therefore matches an ATTRIBUTE STORE
on a report object -- an `ast.Attribute` assignment target -- never a substring, and
this module records the textual count beside its own so a reader can see the
difference between the two methods as a number, not an argument they have to take
on trust.

Idiom copied from `test_readback_inventory.py`'s `read_eprom` census: attribution of
each site to its innermost enclosing function by LINE-NUMBER containment (correct
regardless of `ast.walk` traversal order, which does not guarantee an
outer-before-inner visit), a source-length guard before any census runs (this
project has a recorded checker whose directory-relative path resolution scanned
nothing and exited 0 -- the same failure mode a mis-resolved `__file__` invites
here), and an anchor-uniqueness assertion before any in-memory string mutation.
Every mutation in this module is an in-memory `str.replace` on a loaded source
string; no fixture file is ever written."""

import ast
import pathlib
import types

import pytest

from firestarter import cli_handlers, diagnostic_report

_DELETED_NAMES = ("vpp_mv", "vpe_mv")
_SURVIVOR_NAMES = ("vpp_before_mv", "vpp_after_mv", "vpe_before_mv", "vpe_after_mv")
_ALL_RAIL_NAMES = _DELETED_NAMES + _SURVIVOR_NAMES

_PLANTED_ANCHOR = "            report.vpp_before_mv = vpp\n"

_FALSE_POSITIVE_CANDIDATE_NAMES = frozenset(
    {
        "ic_layout.py",
        "test_chip_resolver.py",
        "test_eprom_database.py",
        "test_extra_chips_supplement.py",
        "test_diff_db_gate.py",
        "test_sdp_capability.py",
        "test_wire_dict_equivalence.py",
        "test_variant_decode_evidence_stability.py",
    }
)

_SELF_REFERENTIAL_NAMES = frozenset(
    {
        "diagnostic_report.py",
        "cli_handlers.py",
        "test_voltage_field_census.py",
        "test_blast_radius_invariance.py",
        "test_diagnostic_report.py",
        "test_dev_test_cmd.py",
        "test_parse_devtest_issue.py",
    }
)


def _module_source(module: types.ModuleType) -> str:
    """Load `module`'s own source from its `__file__`, guarded by a length
    assertion so a mis-resolved path cannot pass this census vacuously."""
    assert module.__file__ is not None
    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert len(source) > 1000
    return source


def _assignment_sites(source: str, names: tuple[str, ...]) -> dict[str, set[str]]:
    """Parse `source` and return `{attribute_name: {enclosing_function_names}}`
    for every plain or annotated assignment whose target is an `ast.Attribute`
    with `.attr` in `names` -- a store onto some object's named slot, never a
    dict key or a bare identifier."""
    tree = ast.parse(source)
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    sites: dict[str, set[str]] = {name: set() for name in names}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets: list[ast.expr] = (
            list(node.targets) if isinstance(node, ast.Assign) else [node.target]
        )
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in names:
                containing = [
                    fn
                    for fn in functions
                    if fn.lineno <= node.lineno <= (fn.end_lineno or fn.lineno)
                ]
                innermost = (
                    min(
                        containing,
                        key=lambda fn: (fn.end_lineno or fn.lineno) - fn.lineno,
                    )
                    if containing
                    else None
                )
                sites[target.attr].add(innermost.name if innermost is not None else "?")
    return sites


def _dict_literal_keys(source: str) -> set[str]:
    """Return every string key appearing in any `ast.Dict` literal anywhere
    in `source` -- the emit-site half of the census, over the serializer
    module's own dict-building expressions."""
    tree = ast.parse(source)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key_node in node.keys:
                if isinstance(key_node, ast.Constant) and isinstance(
                    key_node.value, str
                ):
                    keys.add(key_node.value)
    return keys


def test_no_code_path_assigns_the_deleted_rail_fields() -> None:
    """The positive claim. Zero assignment sites for either deleted name
    across `firestarter/diagnostic_report.py` and `firestarter/
    cli_handlers.py`; the four surviving before/after names ARE found,
    attributed to the sampler's own enclosing function, so the census is
    visibly finding real sites rather than passing on an empty tree. The
    emitted-key half: no dict-literal key anywhere in the serializer
    module is named for either deleted field."""
    report_source = _module_source(diagnostic_report)
    handler_source = _module_source(cli_handlers)

    report_sites = _assignment_sites(report_source, _ALL_RAIL_NAMES)
    handler_sites = _assignment_sites(handler_source, _ALL_RAIL_NAMES)

    for name in _DELETED_NAMES:
        assert not report_sites[name], (name, report_sites[name])
        assert not handler_sites[name], (name, handler_sites[name])

    for name in _SURVIVOR_NAMES:
        assert handler_sites[name] == {"_sampler"}, (name, handler_sites[name])
        assert not report_sites[name], (name, report_sites[name])

    voltage_dict_keys = _dict_literal_keys(report_source)
    for name in _DELETED_NAMES:
        assert name not in voltage_dict_keys, (name, sorted(voltage_dict_keys))


def test_a_planted_assignment_reddens_the_census() -> None:
    """The anti-vacuity leg. The anchor occurs exactly once in the real,
    unmutated source; a single in-memory `str.replace` inserts one
    `report.vpp_mv = vpp` assignment right after it, and the census over
    that mutant reports a non-zero site for the deleted name -- proving
    the positive claim is falsifiable rather than trivially true. No
    fixture file is written; the mutation lives only in this process's
    memory for the duration of this test."""
    source = _module_source(cli_handlers)
    assert source.count(_PLANTED_ANCHOR) == 1
    mutated = source.replace(
        _PLANTED_ANCHOR,
        _PLANTED_ANCHOR + "            report.vpp_mv = vpp\n",
        1,
    )

    mutated_sites = _assignment_sites(mutated, _DELETED_NAMES)
    assert mutated_sites["vpp_mv"], "the planted assignment did not redden the census"

    with pytest.raises(AssertionError):
        assert not mutated_sites["vpp_mv"], (
            "vpp_mv",
            mutated_sites["vpp_mv"],
        )


def test_an_empty_expected_site_set_fails_rather_than_passing_vacuously() -> None:
    """The separate, explicitly named vacuity leg: comparing the real
    survivor sites against an empty expected set must fail, proving that
    an accidentally-empty expectation could never pass this census
    silently."""
    handler_source = _module_source(cli_handlers)
    handler_sites = _assignment_sites(handler_source, _SURVIVOR_NAMES)
    with pytest.raises(AssertionError):
        assert handler_sites["vpp_before_mv"] == set()


def test_a_textual_scan_would_return_false_positives_a_census_does_not() -> None:
    """The measurement that justifies the method. Counts every textual
    occurrence of the two deleted names across `firestarter/` and
    `tests/` (excluding this module and the modules the census itself
    scans, whose self-referential mentions are not the false-positive
    class being measured), and asserts that count is materially larger
    than the census's own zero -- with at least one hit landing in a
    module the census correctly ignores because the name there is the
    DATABASE field, not a report attribute (`ic_layout.py` is asserted
    by name; the full false-positive class is enumerated in this
    module's own docstring above and in `.claude/skills/devtest-triage/
    SKILL.md`'s D-12 measurement)."""
    package_root = pathlib.Path(diagnostic_report.__file__).parent
    tests_root = package_root.parent / "tests"

    textual_hits = 0
    false_positive_modules: set[str] = set()
    for root in (package_root, tests_root):
        for path in root.rglob("*.py"):
            if path.name in _SELF_REFERENTIAL_NAMES:
                continue
            text = path.read_text(encoding="utf-8")
            count = sum(text.count(name) for name in _DELETED_NAMES)
            if count:
                textual_hits += count
                if path.name in _FALSE_POSITIVE_CANDIDATE_NAMES:
                    false_positive_modules.add(path.name)

    report_source = _module_source(diagnostic_report)
    handler_source = _module_source(cli_handlers)
    report_sites = _assignment_sites(report_source, _DELETED_NAMES)
    handler_sites = _assignment_sites(handler_source, _DELETED_NAMES)
    census_count = sum(len(v) for v in report_sites.values()) + sum(
        len(v) for v in handler_sites.values()
    )

    assert textual_hits > census_count, (textual_hits, census_count)
    assert "ic_layout.py" in false_positive_modules, sorted(false_positive_modules)
