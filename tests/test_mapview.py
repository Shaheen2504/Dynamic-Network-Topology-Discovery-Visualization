"""Core-centric map view.

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
RECORDS = parsers.parse_capture(
    (ROOT / "tests/fixtures/CORE-SW-1__cisco_session.txt").read_text())
GRAPH = topology.build(RECORDS)
ROOT_ID = sorted(mapview.roots(RECORDS))[0]
VIEW = mapview.core_view(GRAPH, RECORDS, ROOT_ID)
HTML = mapview.render_html(VIEW)
TREE = mapview.render_tree(VIEW)


def _links_touching_root():
    return [l for l in GRAPH["links"]
            if ROOT_ID in (l["a"]["device"], l["b"]["device"])]


def test_root_is_the_polled_device_not_the_busiest_node():
    # The root comes from `local_device` on the records, which only a device
    # whose own output was collected can have.
    assert mapview.roots(RECORDS) == {ROOT_ID}
    assert ROOT_ID in {n["id"] for n in GRAPH["nodes"]}


def test_every_link_at_the_root_appears_exactly_once_in_the_view():
    in_view = sum(p["link_count"] for p in VIEW["peers"])
    assert in_view == len(_links_touching_root())
    assert VIEW["totals"]["links"] == in_view


def test_peers_are_exactly_the_nodes_adjacent_to_the_root():
    expected = set()
    for link in _links_touching_root():
        ends = {link["a"]["device"], link["b"]["device"]}
        expected |= ends - {ROOT_ID}
    assert {p["id"] for p in VIEW["peers"]} == expected


def test_root_is_not_listed_as_its_own_peer():
    assert ROOT_ID not in {p["id"] for p in VIEW["peers"]}


def test_redundant_uplinks_are_grouped_under_one_peer():
    # Several peers are reached over more than one cable; each must appear once
    # with its links collected, not once per cable.
    multi = [p for p in VIEW["peers"] if p["link_count"] > 1]
    assert multi, "capture is expected to contain redundant uplinks"
    for peer in multi:
        assert len({l["local_port"] for l in peer["links"]}) == peer["link_count"]
    assert len({p["id"] for p in VIEW["peers"]}) == len(VIEW["peers"])


def test_filtered_endpoints_never_reach_the_map():
    dropped = {d[0] for d in GRAPH["dropped_endpoints"]}
    labels = {p["label"] for p in VIEW["peers"]}
    assert dropped
    assert not (labels & dropped)
    for name in dropped:
        assert name not in HTML


def test_every_peer_and_its_ports_are_rendered():
    for peer in VIEW["peers"]:
        assert peer["label"] in HTML
        assert peer["label"] in TREE
        for link in peer["links"]:
            assert link["local_port"] in HTML
            assert link["remote_port"] in HTML


def test_tree_lists_one_branch_per_peer():
    branches = [l for l in TREE.splitlines() if l.startswith(("├──", "└──"))]
    assert len(branches) == len(VIEW["peers"])
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
    for peer in VIEW["peers"]:
        assert set(peer["capabilities"]) <= base.CAPABILITIES


def test_view_survives_a_capture_with_a_different_root():
    # Nothing is tied to this particular switch: the synthetic multi-vendor
    # captures have their own polled devices and build a view just as well.
    import main
    records = main.collect(ROOT / "sample_data")
    graph = topology.build(records)
    for root_id in sorted(mapview.roots(records)):
        view = mapview.core_view(graph, records, root_id)
        assert view["root"]["id"] == root_id
        assert mapview.render_tree(view).splitlines()[0] == view["root"]["label"]


def test_every_link_is_labelled_with_both_port_names_on_the_map():
    labels = " ".join(re.findall(r'class="port">([^<]*)<', HTML))
    for peer in VIEW["peers"]:
        for link in peer["links"]:
            for port in (link["local_port"], link["remote_port"]):
                assert html.escape(mapview._short_port(port)) in labels


def test_short_port_cuts_long_names_only():
    assert mapview._short_port("TenGigabitEthernet1/0/16") == "Te1/0/16"
    for real in ("port32", "LAN", "1/1/1", "28"):
        assert mapview._short_port(real) == real
