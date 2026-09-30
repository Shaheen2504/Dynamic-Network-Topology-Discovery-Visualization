"""Tiered map view over the real core capture alone.

Derived entirely from the real capture. No device name, link, port or expected
count is written into these tests as a literal - every expectation is computed
from the topology graph the parser produced.
"""

import html
import re
from pathlib import Path

import mapview
import parsers
import topology

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures"
RECORDS = parsers.parse_capture((FIXTURES / "CORE-SW-1__cisco_session.txt").read_text())
GRAPH = topology.build(RECORDS)
ROOT_ID = sorted(mapview.roots(RECORDS))[0]
VIEW = mapview.tiered_view(GRAPH, RECORDS)
HTML = mapview.render_html(VIEW)
TREE = mapview.render_tree(VIEW)
PEERS = [n for n in VIEW["nodes"] if n["depth"] == 1]


def _links_touching_root():
    return [l for l in GRAPH["links"]
            if ROOT_ID in (l["a"]["device"], l["b"]["device"])]


def test_root_is_the_polled_device_not_the_busiest_node():
    # The root comes from `local_device` on the records, which only a device
    # whose own output was collected can have.
    assert mapview.roots(RECORDS) == {ROOT_ID}
    assert VIEW["root"]["id"] == ROOT_ID
    assert ROOT_ID in {n["id"] for n in GRAPH["nodes"]}


def test_every_link_appears_exactly_once_in_the_view():
    in_view = sum(len(p["links"]) for p in VIEW["pairs"])
    assert in_view == len(GRAPH["links"]) == len(_links_touching_root())
    assert VIEW["totals"]["links"] == in_view
    assert VIEW["totals"]["unreached"] == 0


def test_first_tier_is_exactly_the_nodes_adjacent_to_the_root():
    expected = set()
    for link in _links_touching_root():
        expected |= {link["a"]["device"], link["b"]["device"]} - {ROOT_ID}
    assert {n["id"] for n in PEERS} == expected
    assert all(n["parent"] == ROOT_ID and n["tier"] == "distribution" for n in PEERS)


def test_root_is_not_listed_as_its_own_peer():
    assert ROOT_ID not in {n["id"] for n in PEERS}
    assert [n["id"] for n in VIEW["nodes"]].count(ROOT_ID) == 1


def test_redundant_uplinks_are_grouped_under_one_pair():
    # Several peers are reached over more than one cable; each must appear once
    # with its links collected, not once per cable.
    multi = [p for p in VIEW["pairs"] if len(p["links"]) > 1]
    assert multi, "capture is expected to contain redundant uplinks"
    for pair in multi:
        assert len({l["up_port"] for l in pair["links"]}) == len(pair["links"])
    ends = [(p["up"], p["down"]) for p in VIEW["pairs"]]
    assert len(set(ends)) == len(ends)


def test_one_polled_switch_confirms_nothing():
    # Every link is seen from the core only; confirmation needs the far end.
    assert VIEW["totals"]["confirmed"] == 0
    assert 'stroke-dasharray' in HTML and not mapview.is_synthetic([FIXTURES])


def test_filtered_endpoints_never_reach_the_map():
    dropped = {d[0] for d in GRAPH["dropped_endpoints"]}
    labels = {n["label"] for n in VIEW["nodes"]}
    assert dropped
    assert not (labels & dropped)
    for name in dropped:
        assert name not in HTML


def test_every_device_and_its_ports_are_rendered():
    for node in VIEW["nodes"]:
        assert node["label"] in HTML
        assert node["label"] in TREE
    for pair in VIEW["pairs"]:
        for link in pair["links"]:
            assert link["up_port"] in HTML
            assert link["down_port"] in HTML


def test_tree_lists_one_top_level_branch_per_peer():
    branches = [l for l in TREE.splitlines() if l.startswith(("├──", "└──"))]
    assert len(branches) == len(PEERS)
    assert TREE.splitlines()[0] == VIEW["root"]["label"]


def test_html_is_self_contained_so_private_data_stays_local():
    # No CDN, no framework, no remote fetch: the file must reference nothing
    # outside itself.
    for marker in ("http://", "https://", "//cdn", "<script", "fetch(", "import "):
        assert marker not in HTML.replace(
            'xmlns="http://www.w3.org/2000/svg"', ""), marker


def test_roles_come_from_canonical_capabilities_only():
    from parsers import base
    assert mapview._role({base.SWITCH}) == "switching"
    assert mapview._role({base.BRIDGE, base.ROUTER}) == "switching"
    assert mapview._role({base.ROUTER}) == "routing"
    assert mapview._role(set()) == "other"
    for node in VIEW["nodes"]:
        assert set(node["capabilities"]) <= base.CAPABILITIES


def test_view_survives_a_capture_with_a_different_root():
    # Nothing is tied to this particular switch: the synthetic multi-vendor
    # captures have their own polled devices and build a view just as well.
    import main
    records = main.collect(ROOT / "sample_data")
    graph = topology.build(records)
    for root_id in sorted(mapview.roots(records)):
        view = mapview.tiered_view(graph, records, root_id)
        assert view["root"]["id"] == root_id
        assert mapview.render_tree(view).splitlines()[0] == view["root"]["label"]
        mapview.render_html(view)


def test_every_link_is_labelled_with_both_port_names_on_the_map():
    labels = " ".join(re.findall(r'class="port">([^<]*)<', HTML))
    for pair in VIEW["pairs"]:
        for link in pair["links"]:
            for port in (link["up_port"], link["down_port"]):
                assert html.escape(mapview._short_port(port)) in labels


def test_short_port_cuts_long_names_only():
    assert mapview._short_port("TenGigabitEthernet1/0/16") == "Te1/0/16"
    for real in ("port32", "LAN", "1/1/1", "28"):
        assert mapview._short_port(real) == real
