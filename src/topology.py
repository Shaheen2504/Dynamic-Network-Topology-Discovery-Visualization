"""Turn neighbour records into one deduplicated network graph.

This module is vendor- and protocol-neutral by construction. It consumes only
the normalized `Neighbor` schema and the canonical capability vocabulary; it
contains no capability letters, no platform strings and no vendor names. If a
new vendor ever requires a change here, the normalization is wrong.

Three jobs, deliberately separate:
  1. identity   - decide when two records name the same physical device
  2. filtering  - keep infrastructure, drop endpoints
  3. dedup      - collapse both ends' view of one cable into one edge
"""

from collections import defaultdict

from parsers import base

INFRASTRUCTURE = {base.SWITCH, base.BRIDGE, base.ROUTER}
ENDPOINT = {base.WLAN_AP, base.WLAN_CONTROLLER, base.TELEPHONE, base.STATION,
            base.REPEATER, base.DOCSIS}


def short_name(device):
    """SW-ECE-03.campus.local -> SW-ECE-03. Display label only, never the key."""
    return (device or "").split(".")[0].strip()


def identity(device_name, chassis="", chassis_by_name=None):
    """Stable node id.

    Chassis MAC when one is known, because hostnames differ between protocols
    and may repeat across sites. A dialect that advertises no chassis id falls
    back to a MAC learned for the same hostname elsewhere, then to the name.
    """
    name = short_name(device_name).lower()
    return chassis or (chassis_by_name or {}).get(name) or name


def is_infrastructure(capabilities):
    """Keep switches, bridges and routers; drop endpoints.

    An endpoint capability wins over an infrastructure one: devices that
    contain a small built-in switch, such as IP phones and some access points,
    legitimately advertise both.
    """
    capabilities = set(capabilities)
    if capabilities & ENDPOINT:
        return False
    return bool(capabilities & INFRASTRUCTURE)


def learn_chassis(neighbors):
    """Map short hostname -> chassis MAC, pooled across every record.

    A polled device never reports its own chassis id, and some dialects report
    none at all. Whichever record did advertise a MAC for a hostname supplies
    the identity for the records that did not.
    """
    learned = {}
    for n in neighbors:
        if n.remote_chassis:
            learned.setdefault(short_name(n.remote_device).lower(), n.remote_chassis)
    return learned


def edge_key(a_id, a_port, b_id, b_port):
    """Direction-free key: both ends of one cable produce the same tuple."""
    return tuple(sorted([(a_id, a_port), (b_id, b_port)]))


def build(neighbors, chassis_by_name=None):
    """neighbors: iterable of Neighbor. Returns {"nodes": …, "links": …}."""
    neighbors = list(neighbors)
    if chassis_by_name is None:
        chassis_by_name = learn_chassis(neighbors)
    nodes, links, dropped = {}, {}, []

    for n in neighbors:
        if not is_infrastructure(n.capabilities):
            dropped.append((n.remote_device, n.raw_capability, n.platform))
            continue

        local_id = identity(n.local_device, chassis_by_name=chassis_by_name)
        remote_id = identity(n.remote_device, n.remote_chassis, chassis_by_name)

        nodes.setdefault(local_id, {"id": local_id, "label": short_name(n.local_device)})
        remote = nodes.setdefault(remote_id, {"id": remote_id,
                                              "label": short_name(n.remote_device)})
        if n.platform and not remote.get("platform"):
            remote["platform"] = n.platform

        key = edge_key(local_id, n.local_port, remote_id, n.remote_port)
        link = links.setdefault(key, {
            "a": {"device": key[0][0], "port": key[0][1]},
            "b": {"device": key[1][0], "port": key[1][1]},
            "protocols": set(),
            "seen_from": set(),
        })
        link["protocols"].add(n.protocol)
        link["seen_from"].add(local_id)

    for link in links.values():
        link["protocols"] = sorted(link["protocols"])
        # Confirmed by both ends = high confidence. One end only means the peer
        # is unpolled, or is not running a neighbour protocol.
        link["bidirectional"] = len(link["seen_from"]) == 2
        del link["seen_from"]

    return {
        "nodes": sorted(nodes.values(), key=lambda n: n["label"]),
        "links": sorted(links.values(), key=lambda l: (l["a"]["device"], l["a"]["port"])),
        "dropped_endpoints": dropped,
    }


def summary(graph):
    degree = defaultdict(int)
    for link in graph["links"]:
        degree[link["a"]["device"]] += 1
        degree[link["b"]["device"]] += 1
    return {
        "devices": len(graph["nodes"]),
        "links": len(graph["links"]),
        "endpoints_filtered": len(graph["dropped_endpoints"]),
        "degree": dict(degree),
    }
