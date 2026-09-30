"""Core-centric view of the topology graph, as a text tree and an SVG map.

A first-phase visualisation: one polled switch at the centre and the
infrastructure directly attached to it. Nothing here knows any device name, any
vendor, or how many neighbours to expect - the root is whichever device was
polled, and its peers are whatever the graph says is adjacent to it.

Reads the topology graph and the Neighbor records that produced it. Writes a
self-contained HTML file: no server, no CDN, no framework, and no network
reference of any kind, so a map built from private data stays private.
"""

import html
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import main
import topology
from parsers import base


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


def core_view(graph, neighbors, root_id):
    """Group everything adjacent to `root_id` by peer.

    Several peers are reached over more than one cable; those links are kept
    together under the peer rather than drawn as unrelated edges, because the
    redundancy is the point.
    """
    capabilities = _capabilities_by_node(graph, neighbors)
    nodes = {n["id"]: n for n in graph["nodes"]}
    by_peer = defaultdict(list)

    for link in graph["links"]:
        ends = (link["a"], link["b"])
        local = next((e for e in ends if e["device"] == root_id), None)
        if local is None:
            continue                       # link between two other devices
        remote = ends[1] if ends[0] is local else ends[0]
        if remote["device"] == root_id:
            continue                       # self-loop, should not happen
        by_peer[remote["device"]].append({
            "local_port": local["port"],
            "remote_port": remote["port"],
            "protocols": link["protocols"],
            "bidirectional": link["bidirectional"],
        })

    peers = []
    for peer_id, links in by_peer.items():
        node = nodes.get(peer_id, {})
        peers.append({
            "id": peer_id,
            "label": node.get("label", peer_id),
            "platform": node.get("platform", ""),
            "capabilities": sorted(capabilities.get(peer_id, ())),
            "links": sorted(links, key=lambda l: l["local_port"]),
            "link_count": len(links),
        })
    peers.sort(key=lambda p: p["label"].lower())

    root = nodes.get(root_id, {"id": root_id, "label": root_id})
    return {
        "root": {"id": root_id, "label": root.get("label", root_id),
                 "platform": root.get("platform", "")},
        "peers": peers,
        "totals": {"peers": len(peers),
                   "links": sum(p["link_count"] for p in peers)},
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


# ------------------------------------------------------------------ text tree

def render_tree(view):
    lines = [view["root"]["label"]]
    peers = view["peers"]
    for i, peer in enumerate(peers):
        stem = "└──" if i == len(peers) - 1 else "├──"
        cont = "   " if i == len(peers) - 1 else "│  "
        badge = f"  ({peer['link_count']} links)" if peer["link_count"] > 1 else ""
        platform = f"  [{peer['platform']}]" if peer["platform"] else ""
        lines.append(f"{stem} {peer['label']}{platform}{badge}")
        for link in peer["links"]:
            confirmed = "" if link["bidirectional"] else "  *"
            lines.append(f"{cont}     {link['local_port']} -> {link['remote_port']}"
                         f"  [{'/'.join(link['protocols'])}]{confirmed}")
    if any(not l["bidirectional"] for p in peers for l in p["links"]):
        lines.append("")
        lines.append("* seen from this device only; the peer has not been polled yet")
    return "\n".join(lines)


# ------------------------------------------------------------------- svg map

def _short_port(port):
    """Display form for an on-link label: `TenGigabitEthernet1/0/16` -> `Te1/0/16`.

    Only long alphabetic prefixes are cut, so real short names (`port32`, `LAN`,
    `1/1/1`) pass through. The full name stays in the tooltip and table.
    """
    m = re.fullmatch(r"([A-Za-z][A-Za-z-]{7,})(\d.*)", port)
    return m[1][:2] + m[2] if m else port


_ROLE_COLOURS = {"switching": "#2f6f4f", "routing": "#8a5a1b", "other": "#5a5a6a"}


def render_html(view):
    peers = view["peers"]
    n = max(len(peers), 1)
    radius = min(max(240, 62 + 11 * n), 470)
    margin = 210
    size = 2 * (radius + margin)
    cx = cy = size / 2

    spokes, dots, labels, ports = [], [], [], []
    for i, peer in enumerate(peers):
        angle = -math.pi / 2 + (2 * math.pi * i / n)
        px, py = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
        colour = _ROLE_COLOURS[_role(peer["capabilities"])]
        detail = "&#10;".join(
            f"{l['local_port']} -> {l['remote_port']}  [{'/'.join(l['protocols'])}]"
            for l in peer["links"])
        tip = html.escape(f"{peer['label']}\n{peer['platform']}\n").replace("\n", "&#10;") + detail
        weight = 1.2 + 0.9 * (peer["link_count"] - 1)

        spokes.append(
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{px:.1f}" y2="{py:.1f}" '
            f'stroke="{colour}" stroke-width="{weight:.1f}" stroke-opacity=".45"/>')
        dots.append(
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="6" fill="{colour}">'
            f'<title>{tip}</title></circle>')

        degrees = math.degrees(angle)
        flip = 90 < degrees % 360 < 270
        anchor = "end" if flip else "start"
        rotate = degrees + 180 if flip else degrees
        offset = -14 if flip else 14
        # Port names sit on the spoke itself: core side near the hub, peer side
        # near the peer, as on a hand-drawn network diagram.
        for t, key in ((0.3, "local_port"), (0.72, "remote_port")):
            text = ", ".join(_short_port(l[key]) for l in peer["links"])
            tx, ty = cx + radius * t * math.cos(angle), cy + radius * t * math.sin(angle)
            ports.append(
                f'<text transform="translate({tx:.1f},{ty:.1f}) rotate({rotate:.1f})" '
                f'y="-3" text-anchor="middle" class="port">{html.escape(text)}</text>')

        lx, ly = cx + (radius + 0) * math.cos(angle), cy + (radius + 0) * math.sin(angle)
        badge = f' &#215;{peer["link_count"]}' if peer["link_count"] > 1 else ""
        labels.append(
            f'<g transform="translate({lx:.1f},{ly:.1f}) rotate({rotate:.1f})">'
            f'<text x="{offset}" y="4" text-anchor="{anchor}" class="peer">'
            f'{html.escape(peer["label"])}{badge}</text></g>')

    rows = "".join(
        f'<tr><td>{html.escape(p["label"])}</td><td>{html.escape(p["platform"])}</td>'
        f'<td>{p["link_count"]}</td><td>' +
        "<br>".join(f'{html.escape(l["local_port"])} &#8594; {html.escape(l["remote_port"])}'
                    f' <span class="proto">{"/".join(l["protocols"])}</span>'
                    for l in p["links"]) +
        "</td></tr>"
        for p in peers)

    legend = "".join(
        f'<span class="key"><i style="background:{c}"></i>{r}</span>'
        for r, c in _ROLE_COLOURS.items())

    return _TEMPLATE.format(
        root=html.escape(view["root"]["label"]),
        peers=view["totals"]["peers"],
        links=view["totals"]["links"],
        size=f"{size:.0f}",
        cx=f"{cx:.1f}", cy=f"{cy:.1f}",
        spokes="".join(spokes), dots="".join(dots), labels="".join(labels),
        ports="".join(ports),
        legend=legend, rows=rows)


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{root} — topology map</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1a1a1a; --bg:#fbfaf8; --mut:#6b6b73; --line:#dedcd6; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#e9e8e4; --bg:#17171a; --mut:#9a9aa3; --line:#33333a; }}
  }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
         font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  h1 {{ font-size:19px; margin:0 0 2px; }}
  .sub {{ color:var(--mut); margin-bottom:18px; }}
  .wrap {{ overflow-x:auto; }}
  svg {{ display:block; max-width:100%; height:auto; }}
  .hub {{ font-weight:600; font-size:15px; fill:var(--fg); }}
  .peer {{ font-size:12px; fill:var(--fg); }}
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
<div class="sub">{peers} directly connected devices &middot; {links} links &middot; {legend}</div>
<div class="wrap"><svg viewBox="0 0 {size} {size}" width="{size}" height="{size}"
     xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Network topology map">
  <g>{spokes}</g>
  <g>{ports}</g>
  <circle cx="{cx}" cy="{cy}" r="15" fill="#1f5fa8"/>
  <text x="{cx}" y="{cy}" dy="-24" text-anchor="middle" class="hub">{root}</text>
  <g>{dots}</g><g>{labels}</g>
</svg></div>
<table><thead><tr><th>Device</th><th>Platform</th><th>Links</th>
<th>Core port &#8594; remote port</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>
"""


def build_view(directory):
    neighbors = main.collect(directory)
    graph = topology.build(neighbors)
    found = sorted(roots(neighbors))
    if not found:
        raise SystemExit(f"no polled device found in {directory}")
    return core_view(graph, neighbors, found[0]), found


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent / "tests/fixtures"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("map.html")
    view, found = build_view(src)
    if len(found) > 1:
        print(f"note: {len(found)} polled devices found, mapping {found[0]}\n")
    print(render_tree(view))
    out.write_text(render_html(view))
    print(f"\nwrote {out}  ({view['totals']['peers']} devices, "
          f"{view['totals']['links']} links)")
