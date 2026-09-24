"""Comware and Aruba AOS-CX parsers, plus the Cisco detail formats.

EVERY fixture behind these tests was written by hand from published output
formats. No capture from a real HPE or Aruba device has been seen. These tests
demonstrate that the architecture extends to another vendor without touching
anything downstream. They are NOT evidence that these parsers are correct
against real hardware, and must not be cited as such.
"""

from pathlib import Path

import main
import parsers
import topology
from parsers import base

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = main.collect(ROOT / "sample_data")


def _by_dialect(dialect):
    path = next(p for p in (ROOT / "sample_data").glob(f"*__{dialect}.txt"))
    device, _, _ = path.stem.partition("__")
    return parsers.parse(dialect, path.read_text(), device)


def test_aruba_records_reach_the_common_schema():
    records = _by_dialect("aruba_cx_lldp_detail")
    assert len(records) == 2
    uplink = next(r for r in records if "bridge" in r.capabilities)
    assert uplink.vendor == "aruba"
    assert uplink.protocol == "lldp"
    assert uplink.capabilities == {base.BRIDGE, base.ROUTER}
    assert uplink.remote_chassis == "00:1a:1e:3f:4b:20"


def test_aruba_positional_port_names_are_not_expanded():
    records = _by_dialect("aruba_cx_lldp_detail")
    assert {r.local_port for r in records} == {"1/1/49", "1/1/12"}


def test_comware_word_capabilities_map_to_the_canonical_vocabulary():
    records = _by_dialect("comware_lldp_detail")
    assert all(r.vendor == "hpe" for r in records)
    assert any(r.capabilities == {base.BRIDGE, base.ROUTER} for r in records)
    assert any(r.capabilities == {base.STATION} for r in records)


def test_station_only_is_recognised_as_an_endpoint():
    # Comware and AOS-CX both spell it "Station only", not "Station".
    assert base.canonical(["Station only"],
                          {"station only": base.STATION}) == {base.STATION}


def test_cisco_detail_formats_still_parse():
    for dialect in ("cisco_cdp_detail", "cisco_lldp_detail"):
        records = _by_dialect(dialect)
        assert records, dialect
        assert all(r.vendor == "cisco" for r in records)


def test_chassis_identity_merges_a_device_seen_over_two_protocols():
    # The synthetic Cisco fixtures report the same switch over CDP and LLDP,
    # and CDP detail advertises no chassis MAC.
    cisco_only = [r for r in SYNTHETIC if r.vendor == "cisco"]
    graph = topology.build(cisco_only)
    merged = [l for l in graph["links"] if l["protocols"] == ["cdp", "lldp"]]
    assert merged


def test_endpoints_are_filtered_regardless_of_which_vendor_reported_them():
    graph = topology.build(SYNTHETIC)
    labels = {n["label"] for n in graph["nodes"]}
    dropped = {d[0] for d in graph["dropped_endpoints"]}
    assert "LAB-PRINTER-07" in dropped and "LAB-PRINTER-07" not in labels
    assert "LAB-PC-114" in dropped
    assert any(d.startswith("SEP") for d in dropped)      # IP phone


def test_mixed_vendor_records_build_one_graph():
    graph = topology.build(SYNTHETIC)
    assert {r.vendor for r in SYNTHETIC} == {"cisco", "hpe", "aruba"}
    assert graph["nodes"] and graph["links"]
