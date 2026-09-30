"""Merging a second polled switch into the real core capture.

SYNTHETIC: `tests/synthetic/LH-00-DIS__cisco_session.txt` is hand-written, not
captured. Its two core uplinks mirror what the real CORE-SW-1 capture reports
for LH-00-DIS; everything below LH-00-DIS is invented. These tests prove the
merge, dedup and tiering logic, not that any real distribution switch looks like
this. Replace the file with a real capture as soon as one exists.
"""

from pathlib import Path

import main
import mapview
import topology

ROOT = Path(__file__).resolve().parent.parent
REAL_DIR, SYNTH_DIR = ROOT / "tests/fixtures", ROOT / "tests/synthetic"
REAL = main.collect(REAL_DIR)
SYNTH = main.collect(SYNTH_DIR)
BOTH = REAL + SYNTH
GRAPH = topology.build(BOTH)
VIEW = mapview.tiered_view(GRAPH, BOTH)
NODES = {n["id"]: n for n in VIEW["nodes"]}

CORE = VIEW["root"]["id"]
DIST = sorted(mapview.roots(SYNTH))[0]


def _key(link):
    return (link["a"]["device"], link["a"]["port"], link["b"]["device"], link["b"]["port"])


def _uplinks(graph):
    return [l for l in graph["links"]
            if {l["a"]["device"], l["b"]["device"]} == {CORE, DIST}]


def test_synthetic_capture_is_marked_and_flagged_on_the_map():
    assert mapview.is_synthetic([SYNTH_DIR])
    assert not mapview.is_synthetic([REAL_DIR])
    assert mapview.SYNTHETIC_MARKER in (SYNTH_DIR / "LH-00-DIS__cisco_session.txt").read_text()
    assert "SYNTHETIC" in mapview.render_html(VIEW, "SYNTHETIC test data")


def test_synthetic_uplinks_agree_with_the_real_core_capture():
    # The fake must not invent core-side facts: every cable it reports to the
    # core is one the core itself reported, with the ports swapped.
    real_view = {(n.local_port, n.remote_port) for n in REAL
                 if topology.short_name(n.remote_device).lower() == DIST}
    fake_view = {(n.remote_port, n.local_port) for n in SYNTH
                 if topology.short_name(n.remote_device).lower() == CORE}
    assert fake_view and fake_view <= real_view


def test_core_stays_the_root_when_a_second_switch_is_polled():
    assert mapview.roots(BOTH) == {CORE, DIST}
    assert NODES[CORE]["depth"] == 0 and NODES[DIST]["depth"] == 1


def test_both_ends_of_a_cable_merge_into_one_confirmed_link():
    real_uplinks = _uplinks(topology.build(REAL))
    merged = _uplinks(GRAPH)
    assert len(merged) == len(real_uplinks) == 2
    assert all(l["bidirectional"] for l in merged)
    assert not any(l["bidirectional"] for l in real_uplinks)


def test_adding_a_switch_adds_only_its_new_cables():
    new_cables = {(n.local_port, topology.short_name(n.remote_device).lower())
                  for n in SYNTH
                  if topology.is_infrastructure(n.capabilities)
                  and topology.short_name(n.remote_device).lower() != CORE}
    assert len(GRAPH["links"]) == len(topology.build(REAL)["links"]) + len(new_cables)


def test_only_cables_polled_from_both_ends_are_confirmed():
    for link in GRAPH["links"]:
        ends = {link["a"]["device"], link["b"]["device"]}
        assert link["bidirectional"] == (ends == {CORE, DIST})
    assert VIEW["totals"]["confirmed"] == 2


def test_merge_does_not_depend_on_which_capture_is_read_first():
    forward = {_key(l): l["bidirectional"] for l in GRAPH["links"]}
    reverse = {_key(l): l["bidirectional"] for l in topology.build(SYNTH + REAL)["links"]}
    assert forward == reverse


def test_access_switches_hang_off_the_distribution_switch():
    below = [n for n in VIEW["nodes"] if n["depth"] == 2]
    assert below
    assert all(n["parent"] == DIST and n["tier"] == "access" for n in below)
    downstream = {topology.short_name(n.remote_device).lower() for n in SYNTH
                  if topology.is_infrastructure(n.capabilities)} - {CORE}
    assert {n["id"] for n in below} == downstream


def test_endpoints_seen_by_the_distribution_switch_are_filtered():
    dropped = {n.remote_device for n in SYNTH if not topology.is_infrastructure(n.capabilities)}
    assert dropped
    assert not dropped & {n["label"] for n in VIEW["nodes"]}


def test_tree_nests_access_under_distribution():
    tree = mapview.render_tree(VIEW).splitlines()
    for node in VIEW["nodes"]:
        if node["depth"] == 2:
            line = next(l for l in tree if f"── {node['label']}" in l)
            assert line.startswith(("│   ", "    ")), line


def test_confirmed_pairs_are_drawn_solid_and_the_rest_dashed():
    page = mapview.render_html(VIEW)
    solid = sum(all(l["bidirectional"] for l in p["links"]) for p in VIEW["pairs"])
    assert page.count("<line ") == len(VIEW["pairs"])
    assert page.count("stroke-dasharray") == len(VIEW["pairs"]) - solid
    assert solid == 1
