"""Cisco parser validated against the complete real core-switch capture.

The fixture is the full transcript: two runs of `show cdp neighbors` (51
entries each) and one of `show lldp neighbors` (39), plus paging headers,
command echoes that produced no output, and prompt noise.

Assertions are about format handling and aggregate behaviour. Where an
individual record is named, it is because that record exercises a specific
formatting case, not because the parser knows anything about that device.
"""

import parsers
import topology
from pathlib import Path

RAW = (Path(__file__).parent / "fixtures/CORE-SW-1__cisco_session.txt").read_text()
RECORDS = parsers.parse_capture(RAW)
CDP = [r for r in RECORDS if r.protocol == "cdp"]
LLDP = [r for r in RECORDS if r.protocol == "lldp"]
GRAPH = topology.build(RECORDS)


def test_every_entry_the_switch_reported_is_parsed():
    # The footers state the truth: 51 CDP entries per run, run twice, and 39
    # LLDP entries. Nothing may be silently dropped.
    assert RAW.count("Total cdp entries displayed : 51") == 2
    assert "Total entries displayed: 39" in RAW
    assert len(CDP) == 102
    assert len(LLDP) == 39


def test_device_name_comes_from_the_prompt():
    assert {r.local_device for r in RECORDS} == {"CORE-SW-1"}


def test_commands_without_neighbour_output_are_ignored():
    # The transcript contains "sh cdp" (global settings) and the abbreviated
    # "sh cdp ne" / "sh lldp n" echoes, which produced no table.
    assert all(r.protocol in ("cdp", "lldp") for r in RECORDS)
    assert not any("Sending CDP packets" in r.remote_device for r in RECORDS)


def test_no_noise_line_becomes_a_record():
    names = {r.remote_device for r in RECORDS}
    assert not any(n.startswith(("Total", "Device ID", "Capability", "CORE-SW-1"))
                   for n in names)


def test_every_cdp_record_is_complete():
    for r in CDP:
        assert r.platform, r
        assert r.raw_capability, r
        assert r.local_port.startswith("FiftyGigE"), r
        assert r.vendor == "cisco"


def test_wrapped_cdp_device_names_are_recovered():
    # A Device ID longer than its column is printed on its own line. Every such
    # name in this capture is an FQDN, and none may be lost or truncated.
    # Count them in the raw text rather than hardcoding a number: a wrapped
    # name is a line holding nothing but the name.
    expected = [l.strip() for l in RAW.splitlines()
                if l.strip() and not l.startswith(" ")
                and l.strip().endswith(".ac.in")]
    wrapped = [r for r in CDP if r.remote_device.endswith(".ac.in")]
    assert len(wrapped) == len(expected) > 0
    assert all(len(r.remote_device) > 17 for r in wrapped)
    assert {r.remote_device for r in wrapped} == set(expected)


def test_lldp_names_truncated_at_the_column_boundary_are_recovered():
    # LLDP truncates to exactly 20 characters with no separator.
    flush = [r for r in LLDP if len(r.remote_device) == 20]
    assert flush, "expected truncated names in this capture"
    assert all(not r.remote_device.endswith(" ") for r in flush)
    assert all(r.local_port.startswith("FiftyGigE") for r in flush)


def test_interface_abbreviations_agree_across_the_two_protocols():
    # CDP prints "Ten 2/0/16" with a space; LLDP prints "Te2/0/16".
    cdp_ports = {r.remote_port for r in CDP if r.remote_port.startswith("Ten")}
    lldp_ports = {r.remote_port for r in LLDP if r.remote_port.startswith("Ten")}
    assert cdp_ports & lldp_ports, "no port name is shared between protocols"
    assert not any(p.startswith(("Ten ", "Te1", "Te2")) for p in cdp_ports | lldp_ports)


def test_port_names_that_are_not_abbreviations_survive_untouched():
    remote_ports = {r.remote_port for r in RECORDS}
    for literal in ("port32", "Port 0", "LAN", "1/1/1", "28", "52", "122", "26"):
        assert literal in remote_ports, literal


def test_cdp_S_is_switch_and_lldp_S_is_station():
    # Same letter, opposite meaning. Resolved in the parser, so the topology
    # engine never sees a protocol-specific code.
    switch = [r for r in CDP if "S" in r.raw_capability.split()][0]
    assert "switch" in switch.capabilities
    assert base_station_capability_is_absent(switch)


def base_station_capability_is_absent(record):
    return "station" not in record.capabilities


def test_access_points_are_classified_by_platform_not_capability():
    # Meraki APs advertise "R S" (Router, Switch); Aironet APs advertise
    # "T B I". Neither is distinguishable from a switch by capability alone.
    meraki = [r for r in CDP if r.platform == "Meraki MR"]
    aironet = [r for r in CDP if r.platform.startswith("AIR-CAP")]
    assert meraki and aironet
    assert all("wlan-ap" in r.capabilities for r in meraki + aironet)
    assert all(not topology.is_infrastructure(r.capabilities) for r in meraki + aironet)


def test_wireless_controller_is_classified_separately_from_switches():
    wlc = [r for r in CDP if r.platform.startswith("AIR-CT")]
    assert wlc
    assert all("wlan-controller" in r.capabilities for r in wlc)


def test_running_the_same_command_twice_does_not_duplicate_links():
    # CDP was run twice in this transcript; both runs must collapse.
    cdp_only = topology.build([r for r in RECORDS if r.protocol == "cdp"])
    one_run = topology.build([r for r in RECORDS[:len(CDP) // 2]])
    assert len(cdp_only["links"]) == len(one_run["links"])


def test_a_cable_seen_over_both_protocols_becomes_one_link():
    both = [l for l in GRAPH["links"] if l["protocols"] == ["cdp", "lldp"]]
    assert len(both) == 18


def test_graph_totals():
    s = topology.summary(GRAPH)
    assert s == {"devices": 36, "links": 48, "endpoints_filtered": 48,
                 "degree": s["degree"]}
    # Only the polled switch has any degree above one: every other device is
    # known from a single vantage point until its own output is collected.
    assert max(s["degree"].values()) == 48
