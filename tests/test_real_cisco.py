"""Tests against the real CORE-SW-1 capture.

Every assertion here is derived from actual output of the production core
switch, not from an invented format.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import parser
import topology

RAW = (ROOT / "tests/fixtures/CORE-SW-1__cisco_session.txt").read_text()
RECORDS = parser.parse_capture(RAW)
CDP = [r for r in RECORDS if r.protocol == "cdp"]
LLDP = [r for r in RECORDS if r.protocol == "lldp"]
GRAPH = topology.build(RECORDS)


def find(records, remote, port=None):
    hits = [r for r in records if r.remote_device == remote
            and (port is None or r.local_port == port)]
    assert hits, f"{remote} {port or ''} not parsed"
    return hits[0]


def test_capture_yields_every_row():
    assert (len(CDP), len(LLDP)) == (15, 12)


def test_device_name_is_taken_from_the_cli_prompt():
    assert {r.local_device for r in RECORDS} == {"CORE-SW-1"}


def test_cdp_row_with_a_wrapped_device_name():
    # A name longer than its column sits on its own line, data on the next.
    r = find(CDP, "M-HOSTEL-00-DIS.iith.ac.in")
    assert r.local_port == "FiftyGigE2/2/0/21"
    assert r.remote_port == "TenGigabitEthernet2/0/16"
    assert r.platform == "C9500-16X"
    assert r.capabilities == ("R", "S", "I")


def test_cdp_row_on_a_single_line():
    r = find(CDP, "008e7320880b")
    assert (r.local_port, r.remote_port) == ("FiftyGigE1/2/0/25", "GigabitEthernet25")
    assert r.platform == "SG350-28"


def test_lldp_name_truncated_flush_against_the_interface():
    # "M-HOSTEL-00-DIS.iithFif2/2/0/21" has no separator at all.
    r = find(LLDP, "M-HOSTEL-00-DIS.iith")
    assert r.local_port == "FiftyGigE2/2/0/21"
    assert r.remote_port == "TenGigabitEthernet2/0/16"
    assert r.capabilities == ("B", "R")


def test_platform_variety_is_captured():
    assert {r.platform for r in CDP} >= {
        "C9500-16X", "C9500-40X", "WS-C3750X", "Meraki MR",
        "AIR-CAP36", "AIR-CAP37", "AIR-CT576", "SG350-28"}


def test_interface_abbreviations_normalize_across_protocols():
    # CDP prints "Ten 2/0/16" with a space, LLDP prints "Te2/0/16".
    assert parser.normalize_port("Ten 2/0/16") == parser.normalize_port("Te2/0/16")
    assert parser.normalize_port("Fif 2/2/0/21") == parser.normalize_port("Fif2/2/0/21")
    assert parser.normalize_port("Ten-GigabitEthernet1/0/52") == "TenGigabitEthernet1/0/52"


def test_non_cisco_port_names_are_left_alone():
    # Real ports on FortiGate, Meraki, Aruba and HP kit - not abbreviations.
    for raw in ("port32", "Port 0", "LAN", "1/1/1", "28", "52", "122"):
        assert parser.normalize_port(raw) == raw


def test_cdp_S_means_switch_not_station():
    # The same letter is Station in LLDP. Sharing one table deletes the core.
    assert topology.is_infrastructure(("R", "S", "I"), "cdp", "C9500-16X") is True
    assert topology.is_infrastructure(("S",), "lldp") is False


def test_access_points_are_dropped_on_platform_not_capability():
    # Meraki APs advertise Router,Switch; Cisco APs advertise Trans Bridge.
    assert topology.is_infrastructure(("R", "S"), "cdp", "Meraki MR") is False
    assert topology.is_infrastructure(("T", "B", "I"), "cdp", "AIR-CAP37") is False
    dropped = {d[0] for d in GRAPH["dropped_endpoints"]}
    assert "APc08c.600f.378e" in dropped
    assert "e455a81a14f9" in dropped


def test_distribution_switches_survive_filtering():
    labels = {n["label"] for n in GRAPH["nodes"]}
    for expected in ("M-HOSTEL-00-DIS", "RCC_DISTRIBUTION", "IGH-00-DIS",
                     "SNCC-00-DIS", "AC-DSW", "TIP-00-DIS"):
        assert expected in labels


def test_truncated_lldp_name_merges_with_full_cdp_fqdn():
    # LLDP "RCC_DISTRIBUTION.iit" and CDP "RCC_DISTRIBUTION.iith.ac.in" are one
    # device, so its uplink must be a single link confirmed by both protocols.
    link = [l for l in GRAPH["links"]
            if "rcc_distribution" in (l["a"]["device"], l["b"]["device"])
            and "FiftyGigE1/2/0/4" in (l["a"]["port"], l["b"]["port"])]
    assert len(link) == 1
    assert link[0]["protocols"] == ["cdp", "lldp"]


def test_noise_lines_are_not_parsed_as_records():
    devices = {r.remote_device for r in RECORDS}
    assert not any(d.startswith(("Total", "Device ID", "CORE-SW-1#", "Capability"))
                   for d in devices)
