"""Smallest checks that fail if the topology logic breaks."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import main
import parser
import topology

NEIGHBORS = main.collect(ROOT / "sample_data")
GRAPH = topology.build(NEIGHBORS)


def test_port_names_normalize_across_protocols():
    # CDP prints the long form, LLDP the short one; same physical port.
    assert parser.normalize_port("Gi1/0/24") == parser.normalize_port("GigabitEthernet1/0/24")
    assert parser.normalize_port("Te1/1/1") == "TenGigabitEthernet1/1/1"


def test_chassis_id_normalizes_across_vendor_notation():
    assert (parser.normalize_chassis("00e1.6d2a.1b00")
            == parser.normalize_chassis("00e1-6d2a-1b00")
            == parser.normalize_chassis("00:E1:6D:2A:1B:00")
            == "00:e1:6d:2a:1b:00")


def test_all_three_dialects_parse():
    by_protocol = {n.protocol for n in NEIGHBORS}
    assert by_protocol == {"lldp", "cdp"}
    assert len(NEIGHBORS) == 8


def test_both_ends_of_a_cable_collapse_to_one_link():
    # SW-CORE-01 and SW-ECE-03 each report this cable, over two protocols.
    assert len(GRAPH["links"]) == 2
    assert all(link["bidirectional"] for link in GRAPH["links"])


def test_one_switch_seen_over_cdp_and_lldp_is_one_node():
    assert len(GRAPH["nodes"]) == 3
    core_uplink = [l for l in GRAPH["links"] if "TenGigabitEthernet1/1/1" in
                   (l["a"]["port"], l["b"]["port"])]
    assert len(core_uplink) == 1
    assert core_uplink[0]["protocols"] == ["cdp", "lldp"]


def test_endpoints_are_filtered_out():
    labels = {n["label"] for n in GRAPH["nodes"]}
    assert "LAB-PC-114" not in labels
    assert "HOSTEL-PRINTER-02" not in labels
    # An IP phone advertises Bridge because it contains a small switch - the
    # capability allowlist alone would wrongly keep it.
    assert "SEP001646221F01" not in labels
    assert topology.is_infrastructure(("B", "T")) is False
    assert topology.is_infrastructure(("B", "R")) is True
