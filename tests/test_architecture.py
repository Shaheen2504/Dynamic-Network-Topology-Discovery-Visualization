"""Architecture invariants.

These tests are about the shape of the design, not about any one vendor. They
are what should fail if vendor knowledge leaks out of the parser layer again.
"""

import inspect
from pathlib import Path

import parsers
import topology
from parsers import base

ROOT = Path(__file__).resolve().parent.parent


def test_every_dialect_is_registered_with_a_vendor():
    assert parsers.DIALECTS
    assert set(parsers.VENDORS) == set(parsers.DIALECTS)
    assert set(parsers.VENDORS.values()) == {"cisco", "hpe", "aruba"}


def test_every_parser_emits_the_common_schema():
    """Whatever the vendor, records carry the same fields with the same meaning."""
    for name, raw, dialect, device in _all_fixture_inputs():
        for record in parsers.parse(dialect, raw, device):
            assert isinstance(record, base.Neighbor), name
            assert record.local_device and record.local_port
            assert record.remote_device and record.remote_port
            assert record.vendor == parsers.VENDORS[dialect]
            assert record.protocol in ("cdp", "lldp")
            assert record.capabilities <= base.CAPABILITIES, (
                f"{name}: {record.capabilities - base.CAPABILITIES} is outside "
                "the canonical vocabulary")


def test_topology_engine_is_vendor_and_protocol_neutral():
    """The engine must not contain vendor names, platforms or capability codes."""
    source = inspect.getsource(topology).lower()
    for forbidden in ("cisco", "aruba", "meraki", "comware", "hpe", "air-cap",
                      "air-ct", "c9500", "fortigate", "cdp", "lldp"):
        assert forbidden not in source, f"{forbidden!r} leaked into topology.py"


def test_topology_filtering_uses_only_canonical_capabilities():
    assert topology.is_infrastructure({base.SWITCH}) is True
    assert topology.is_infrastructure({base.ROUTER}) is True
    assert topology.is_infrastructure({base.BRIDGE}) is True
    assert topology.is_infrastructure({base.STATION}) is False
    assert topology.is_infrastructure({base.WLAN_AP}) is False
    assert topology.is_infrastructure(frozenset()) is False
    # An endpoint capability beats an infrastructure one: phones and APs
    # legitimately advertise Bridge because they contain a small switch.
    assert topology.is_infrastructure({base.BRIDGE, base.TELEPHONE}) is False
    assert topology.is_infrastructure({base.SWITCH, base.WLAN_AP}) is False


def test_unknown_capability_tokens_are_dropped_not_guessed():
    assert base.canonical(["Z", "banana"], {"r": base.ROUTER}) == frozenset()


def test_no_device_names_from_the_capture_appear_in_parser_or_engine():
    """Validation data must not be baked into the code."""
    code = "\n".join((ROOT / "src" / p).read_text().lower()
                     for p in ("topology.py", "main.py", "parsers/base.py",
                               "parsers/cisco.py", "parsers/comware.py",
                               "parsers/aruba_cx.py"))
    for name in ("iith", "core-sw-1", "m-hostel", "rcc_distribution",
                 "sncc", "corelab", "fortigate", "acad-"):
        assert name not in code, f"capture-specific name {name!r} is hardcoded"


def _all_fixture_inputs():
    out = []
    for path in sorted((ROOT / "sample_data").glob("*__*.txt")):
        device, _, dialect = path.stem.partition("__")
        out.append((path.name, path.read_text(), dialect, device))
    return out
