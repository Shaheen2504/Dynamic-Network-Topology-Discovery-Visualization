"""Tiered view of the topology graph, as a text tree and an SVG map.

The core sits at the centre; everything reachable from it is placed on rings by
hop distance: distribution one hop out, access two hops out. Nothing here knows
any device name, any vendor, or how many neighbours to expect - the core is the
polled device with the most links, and every tier is whatever the graph says is
that many hops away.

Links are the topology engine's deduplicated links, so a cable reported by both
of its ends is drawn once and marked confirmed; a cable seen from one end only
(the peer has not been polled) is drawn dashed.

Reads the topology graph and the Neighbor records that produced it. Writes a
self-contained HTML file: no server, no CDN, no framework, and no network
reference of any kind, so a map built from private data stays private.
"""

import html
import math
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import main
import topology
from parsers import base

TIER_NAMES = ("core", "distribution", "access")

# First line of a hand-written capture. The map carries a warning whenever one
# was loaded, so synthetic devices are never mistaken for the real network.
SYNTHETIC_MARKER = "SYNTHETIC TEST DATA"


def roots(neighbors, chassis_by_name=None):
    """Node ids of the devices that were actually polled.

    A device only appears as `local_device` if its own output was collected, so
    this is the set of vantage points - no guessing from degree.
    """
    neighbors = list(neighbors)
    if chassis_by_name is None:
        chassis_by_name = topology.learn_chassis(neighbors)
    return {topology.identity(n.local_device, chassis_by_name=chassis_by_name)
            for n in neighbors}


def pick_root(graph, polled):
    """The core: among polled devices, the one with the most links.

    Only a polled device can be the centre, since only its links are fully
    known. Ties break on id so the choice is stable.
    """
    degree = topology.summary(graph)["degree"]
    return min(polled, key=lambda d: (-degree.get(d, 0), d))


def tier_name(depth):
    return TIER_NAMES[depth] if depth < len(TIER_NAMES) else f"hop {depth}"


def tiered_view(graph, neighbors, root_id=None):
    """Everything reachable from the core, tiered by hop distance.

    Parallel cables between the same two devices are grouped into one pair,
    because the redundancy is the point. Each pair is oriented upstream first:
    `up` is the end nearer the core.
    """
    neighbors = list(neighbors)
    polled = roots(neighbors)
    if root_id is None:
        root_id = pick_root(graph, polled)
    capabilities = _capabilities_by_node(graph, neighbors)
    info = {n["id"]: n for n in graph["nodes"]}

    def label(node_id):
        return info.get(node_id, {}).get("label", node_id)

    adjacent = defaultdict(set)
    for link in graph["links"]:
        a, b = link["a"]["device"], link["b"]["device"]
        if a != b:
            adjacent[a].add(b)
            adjacent[b].add(a)

    # Breadth-first from the core: depth is hop distance, parent is the device
    # a node is drawn under. Neighbours are visited in label order so the layout
    # is stable between runs.
    depth, parent, order = {root_id: 0}, {root_id: None}, [root_id]
    queue = deque([root_id])
    while queue:
        node = queue.popleft()
        for nxt in sorted(adjacent[node], key=lambda i: (label(i).lower(), i)):
            if nxt not in depth:
                depth[nxt], parent[nxt] = depth[node] + 1, node
                order.append(nxt)
                queue.append(nxt)

    nodes = [{
        "id": i,
        "label": label(i),
        "platform": info.get(i, {}).get("platform", ""),
        "capabilities": sorted(capabilities.get(i, ())),
        "depth": depth[i],
        "tier": tier_name(depth[i]),
        "parent": parent[i],
        "polled": i in polled,
    } for i in order]

    grouped = defaultdict(list)
    for link in graph["links"]:
        up, down = sorted((link["a"], link["b"]),
                          key=lambda e: (depth.get(e["device"], math.inf),
                                         label(e["device"]).lower()))
        if up["device"] not in depth or up["device"] == down["device"]:
            continue                       # not connected to the core / self-loop
        grouped[(up["device"], down["device"])].append({
            "up_port": up["port"],
            "down_port": down["port"],
            "protocols": link["protocols"],
            "bidirectional": link["bidirectional"],
        })

    rank = {i: k for k, i in enumerate(order)}
    pairs = [{
        "up": up, "down": down,
        "links": sorted(links, key=lambda l: l["up_port"]),
        "tree": parent[down] == up,        # False: redundant path, not the drawing tree
    } for (up, down), links in sorted(grouped.items(),
                                      key=lambda kv: (rank[kv[0][1]], rank[kv[0][0]]))]

    tiers = defaultdict(int)
    for n in nodes:
        tiers[n["tier"]] += 1
    links = [l for p in pairs for l in p["links"]]
    return {
        "root": nodes[0],
        "nodes": nodes,
        "pairs": pairs,
        "totals": {
            "devices": len(nodes),
            "tiers": dict(tiers),
            "links": len(links),
            "confirmed": sum(l["bidirectional"] for l in links),
            "polled": sorted(label(i) for i in polled if i in depth),
            "unreached": len(graph["nodes"]) - len(nodes),
        },
    }


def _capabilities_by_node(graph, neighbors):
    """Union of the canonical capabilities reported for each node."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    chassis_by_name = topology.learn_chassis(neighbors)
    out = defaultdict(set)
    for n in neighbors:
        node_id = topology.identity(n.remote_device, n.remote_chassis, chassis_by_name)
        if node_id in nodes:
            out[node_id] |= set(n.capabilities)
    return out


def _role(capabilities):
    """Coarse grouping for colour and legend, from canonical terms only."""
    capabilities = set(capabilities)
    if capabilities & {base.SWITCH, base.BRIDGE}:
        return "switching"
    if base.ROUTER in capabilities:
        return "routing"
    return "other"


def _children(view):
    kids = defaultdict(list)
    for n in view["nodes"][1:]:
        kids[n["parent"]].append(n)        # BFS order keeps each list sorted
    return kids


# ------------------------------------------------------------------ text tree

def render_tree(view):
    root, kids = view["root"], _children(view)
    pairs = {(p["up"], p["down"]): p for p in view["pairs"]}
    lines = [root["label"]]

    def walk(node, prefix):
        children = kids[node["id"]]
        for i, child in enumerate(children):
            last = i == len(children) - 1
            stem, cont = ("└──", "    ") if last else ("├──", "│   ")
            links = pairs[(node["id"], child["id"])]["links"]
            badge = f"  ({len(links)} links)" if len(links) > 1 else ""
            platform = f"  [{child['platform']}]" if child["platform"] else ""
            polled = "  (polled)" if child["polled"] else ""
            lines.append(f"{prefix}{stem} {child['label']}{platform}{badge}{polled}")
            for link in links:
                confirmed = "" if link["bidirectional"] else "  *"
                lines.append(f"{prefix}{cont}   {link['up_port']} -> {link['down_port']}"
                             f"  [{'/'.join(link['protocols'])}]{confirmed}")
            walk(child, prefix + cont)

    walk(root, "")
    labels = {n["id"]: n["label"] for n in view["nodes"]}
    extra = [p for p in view["pairs"] if not p["tree"]]
    if extra:
        lines += ["", "redundant paths outside the tree:"]
        for p in extra:
            for link in p["links"]:
                lines.append(f"  {labels[p['up']]} {link['up_port']} -> "
                             f"{labels[p['down']]} {link['down_port']}")
    if any(not l["bidirectional"] for p in view["pairs"] for l in p["links"]):
        lines += ["", "* seen from one end only; the peer has not been polled yet"]
    return "\n".join(lines)


# ------------------------------------------------------------------- svg map

_ROLE_COLOURS = {"switching": "#2f6f4f", "routing": "#8a5a1b", "other": "#5a5a6a"}
_ROOT_COLOUR = "#1f5fa8"


def _short_port(port):
    """Display form for an on-link label: `TenGigabitEthernet1/0/16` -> `Te1/0/16`.

    Only long alphabetic prefixes are cut, so real short names (`port32`, `LAN`,
    `1/1/1`) pass through. The full name stays in the tooltip and table.
    """
    m = re.fullmatch(r"([A-Za-z][A-Za-z-]{7,})(\d.*)", port)
    return m[1][:2] + m[2] if m else port


def _readable(degrees):
    """Rotation that keeps text along a line of this angle upright."""
    flip = 90 < degrees % 360 < 270
    return (degrees + 180 if flip else degrees), flip


def _layout(view):
    """Radial tree: each node gets an angular sector sized by its leaf count,
    so a distribution switch with many access switches gets room for them."""
    kids = _children(view)
    leaves = {}

    def count(node_id):
        leaves[node_id] = sum(count(k["id"]) for k in kids[node_id]) or 1
        return leaves[node_id]

    root = view["root"]["id"]
    count(root)
    depth_max = max(n["depth"] for n in view["nodes"])
    first = min(max(240, 62 + 11 * leaves[root]), 470) if depth_max <= 1 else 330
    ring = lambda d: 0 if d == 0 else first + 210 * (d - 1)
    size = 2 * (ring(depth_max) + 210)     # 210: room for outward labels
    cx = cy = size / 2

    angle = {root: -math.pi / 2}

    def place(node_id, start, span):
        for kid in kids[node_id]:
            share = span * leaves[kid["id"]] / leaves[node_id]
            angle[kid["id"]] = start + share / 2
            place(kid["id"], start, share)
            start += share

    place(root, -math.pi / 2, 2 * math.pi)
    pos = {n["id"]: (cx + ring(n["depth"]) * math.cos(angle[n["id"]]),
                     cy + ring(n["depth"]) * math.sin(angle[n["id"]]))
           for n in view["nodes"]}
    # Crop to the drawn nodes plus label room: only some sectors reach the
    # outer rings, and the rest of the square would be empty.
    xs, ys = [x for x, _ in pos.values()], [y for _, y in pos.values()]
    box = (max(min(xs) - 210, 0), max(min(ys) - 210, 0),
           min(max(xs) + 210, size), min(max(ys) + 210, size))
    return pos, angle, box, {n["id"] for n in view["nodes"] if not kids[n["id"]]}


def render_html(view, warning=""):
    nodes = {n["id"]: n for n in view["nodes"]}
    pos, angle, (x0, y0, x1, y1), leaf_ids = _layout(view)
    root = view["root"]

    edges, ports, dots, labels = [], [], [], []
    for pair in view["pairs"]:
        (ax, ay), (bx, by) = pos[pair["up"]], pos[pair["down"]]
        colour = _ROLE_COLOURS[_role(nodes[pair["down"]]["capabilities"])]
        confirmed = all(l["bidirectional"] for l in pair["links"])
        dash = "" if confirmed else ' stroke-dasharray="5 4"'
        weight = 1.2 + 0.9 * (len(pair["links"]) - 1)
        edges.append(
            f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
            f'stroke="{colour}" stroke-width="{weight:.1f}" stroke-opacity=".5"{dash}/>')

        # Port names sit on the link itself: the upstream port near the upstream
        # device, the downstream port near the downstream one.
        rotate, _ = _readable(math.degrees(math.atan2(by - ay, bx - ax)))
        for t, key in ((0.3, "up_port"), (0.72, "down_port")):
            text = ", ".join(_short_port(l[key]) for l in pair["links"])
            tx, ty = ax + (bx - ax) * t, ay + (by - ay) * t
            ports.append(
                f'<text transform="translate({tx:.1f},{ty:.1f}) rotate({rotate:.1f})" '
                f'y="-3" text-anchor="middle" class="port">{html.escape(text)}</text>')

    up_links = {p["down"]: p["links"] for p in view["pairs"] if p["tree"]}
    for node in view["nodes"][1:]:
        x, y = pos[node["id"]]
        colour = _ROLE_COLOURS[_role(node["capabilities"])]
        detail = "&#10;".join(
            f"{l['up_port']} -> {l['down_port']}  [{'/'.join(l['protocols'])}]"
            for l in up_links.get(node["id"], ()))
        tip = html.escape(
            f"{node['label']}\n{node['platform']}\n{node['tier']}"
            f" via {nodes[node['parent']]['label']}\n").replace("\n", "&#10;") + detail
        ring = (f' stroke="{colour}" stroke-width="2" stroke-opacity=".45"'
                if node["polled"] else "")
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{9 if node["polled"] else 6}" '
                    f'fill="{colour}"{ring}><title>{tip}</title></circle>')

        n_links = len(up_links.get(node["id"], ()))
        badge = f' &#215;{n_links}' if n_links > 1 else ""
        name = f'{html.escape(node["label"])}{badge}'
        if node["id"] in leaf_ids:
            # Leaves: label runs outward along the spoke, past the dot.
            rotate, flip = _readable(math.degrees(angle[node["id"]]))
            labels.append(
                f'<g transform="translate({x:.1f},{y:.1f}) rotate({rotate:.1f})">'
                f'<text x="{-14 if flip else 14}" y="4" text-anchor="{"end" if flip else "start"}" '
                f'class="peer">{name}</text></g>')
        else:
            # Inner nodes have links leaving outward, so the label sits flat
            # above the dot instead of along them.
            labels.append(f'<text x="{x:.1f}" y="{y:.1f}" dy="-14" text-anchor="middle" '
                          f'class="peer inner">{name}</text>')

    rows = []
    for pair in view["pairs"]:
        down, up = nodes[pair["down"]], nodes[pair["up"]]
        rows.append(
            f'<tr><td>{html.escape(down["label"])}</td><td>{down["tier"]}</td>'
            f'<td>{html.escape(up["label"])}</td><td>{html.escape(down["platform"])}</td><td>' +
            "<br>".join(
                f'{html.escape(l["up_port"])} &#8594; {html.escape(l["down_port"])}'
                f' <span class="proto">{"/".join(l["protocols"])}'
                f'{" &middot; both ends" if l["bidirectional"] else " &middot; one end"}</span>'
                for l in pair["links"]) + "</td></tr>")

    legend = "".join(
        f'<span class="key"><i style="background:{c}"></i>{r}</span>'
        for r, c in _ROLE_COLOURS.items())
    totals = view["totals"]
    tiers = ", ".join(f"{count} {name}" for name, count in totals["tiers"].items())
    unreached = (f' &middot; {totals["unreached"]} not connected to the core'
                 if totals["unreached"] else "")
    root_x, root_y = pos[root["id"]]
    return _TEMPLATE.format(
        root=html.escape(root["label"]),
        devices=totals["devices"], tiers=tiers, unreached=unreached,
        links=totals["links"], confirmed=totals["confirmed"],
        polled=html.escape(", ".join(totals["polled"])),
        warning=f'<div class="warn">{html.escape(warning)}</div>' if warning else "",
        box=f"{x0:.0f} {y0:.0f} {x1 - x0:.0f} {y1 - y0:.0f}",
        width=f"{x1 - x0:.0f}", height=f"{y1 - y0:.0f}",
        cx=f"{root_x:.1f}", cy=f"{root_y:.1f}", root_colour=_ROOT_COLOUR,
        edges="".join(edges), ports="".join(ports),
        dots="".join(dots), labels="".join(labels),
        legend=legend, rows="".join(rows))


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{root} — topology map</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1a1a1a; --bg:#fbfaf8; --mut:#6b6b73; --line:#dedcd6;
          --warn-bg:#fff3d6; --warn-fg:#6b4a00; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#e9e8e4; --bg:#17171a; --mut:#9a9aa3; --line:#33333a;
            --warn-bg:#3a2f12; --warn-fg:#f2d48a; }}
  }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
         font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  h1 {{ font-size:19px; margin:0 0 2px; }}
  .sub {{ color:var(--mut); margin-bottom:18px; }}
  .warn {{ background:var(--warn-bg); color:var(--warn-fg); padding:8px 12px;
           border-radius:6px; margin-bottom:14px; font-weight:500; }}
  .wrap {{ overflow-x:auto; }}
  svg {{ display:block; max-width:100%; height:auto; }}
  svg text {{ paint-order:stroke; stroke:var(--bg); stroke-width:3px; stroke-linejoin:round; }}
  .hub {{ font-weight:600; font-size:15px; fill:var(--fg); }}
  .peer {{ font-size:12px; fill:var(--fg); }}
  .inner {{ font-weight:600; }}
  .port {{ font-size:9px; fill:var(--mut); }}
  .key {{ margin-right:16px; color:var(--mut); font-size:12px; }}
  .key i {{ display:inline-block; width:10px; height:10px; border-radius:50%;
            margin-right:5px; vertical-align:-1px; }}
  table {{ border-collapse:collapse; margin-top:26px; width:100%; font-size:13px; }}
  th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line);
           vertical-align:top; }}
  th {{ color:var(--mut); font-weight:500; }}
  .proto {{ color:var(--mut); font-size:11px; }}
</style></head><body>
<h1>{root}</h1>
{warning}<div class="sub">{devices} devices ({tiers}){unreached} &middot;
{links} links, {confirmed} confirmed by both ends &middot; polled: {polled}<br>
{legend}<span class="key">solid: confirmed by both ends &middot; dashed: one end only
&middot; ringed: polled</span></div>
<div class="wrap"><svg viewBox="{box}" width="{width}" height="{height}"
     xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Network topology map">
  <g>{edges}</g>
  <g>{ports}</g>
  <circle cx="{cx}" cy="{cy}" r="15" fill="{root_colour}"/>
  <text x="{cx}" y="{cy}" dy="-24" text-anchor="middle" class="hub">{root}</text>
  <g>{dots}</g><g>{labels}</g>
</svg></div>
<table><thead><tr><th>Device</th><th>Tier</th><th>Upstream</th><th>Platform</th>
<th>Upstream port &#8594; device port</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>
"""


def is_synthetic(directories):
    return any(SYNTHETIC_MARKER in path.read_text()
               for d in directories for path in Path(d).glob("*__*.txt"))


def build_view(directories):
    neighbors = [n for d in directories for n in main.collect(d)]
    if not neighbors:
        raise SystemExit(f"no neighbour records found in {', '.join(map(str, directories))}")
    return tiered_view(topology.build(neighbors), neighbors)


if __name__ == "__main__":
    # mapview.py [capture_dir ...] [out.html] - several directories are merged,
    # e.g. the real core capture plus a distribution switch's.
    args = sys.argv[1:]
    out = Path(next((a for a in args if a.endswith(".html")), "map.html"))
    dirs = ([a for a in args if not a.endswith(".html")]
            or [Path(__file__).parent.parent / "tests/fixtures"])
    view = build_view(dirs)
    warning = ("Includes SYNTHETIC test data - devices and links from hand-written "
               "captures are not real." if is_synthetic(dirs) else "")
    if warning:
        print(f"WARNING: {warning}\n")
    print(render_tree(view))
    out.write_text(render_html(view, warning))
    print(f"\nwrote {out}  ({view['totals']['devices']} devices, "
          f"{view['totals']['links']} links, {view['totals']['confirmed']} confirmed)")
