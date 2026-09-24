"""Turn per-device neighbor records into one deduplicated network graph.

Three jobs, deliberately separate:
  1. identity   - decide when two records name the same physical device
  2. filtering  - keep infrastructure, drop phones/APs/hosts
  3. dedup      - collapse both ends' view of one cable into one edge

rdx: plain dicts/sets, no networkx. Add networkx when an algorithm needs it
(shortest path, layout, components) - dedup alone does not.
"""

from collections import defaultdict

# Capability letters mean DIFFERENT THINGS in the two protocols. In CDP, S is
# Switch; in LLDP, S is Station (an end host). Sharing one set silently deletes
# every Cisco switch discovered over CDP, so the sets are kept per protocol.
#
#   CDP  : R Router, T Trans Bridge, B Source Route Bridge, S Switch, H Host,
#          I IGMP, r Repeater, P Phone, D Remote, C CVTA, M Two-port Mac Relay
#   LLDP : R Router, B Bridge, T Telephone, C DOCSIS, W WLAN AP, P Repeater,
#          S Station, O Other
CAPS = {
    # CDP summary prints letters ("R S I"); CDP detail spells them out
    # ("Switch IGMP"). Both forms have to be recognised.
    "cdp": {"infra": {"r", "s", "b", "router", "switch", "source route bridge"},
            "endpoint": {"h", "p", "host", "phone"}},
    "lldp": {"infra": {"b", "r", "bridge", "router", "switch"},
             "endpoint": {"t", "telephone", "s", "station", "station only",
                          "w", "wlan", "h", "host"}},
}

# Capabilities alone cannot exclude wireless kit on CDP: Meraki access points
# advertise "R S" (Router, Switch) and Cisco APs advertise "T B I". Only the
# platform string distinguishes them from real switches.
ENDPOINT_PLATFORMS = ("air-cap", "air-lap", "air-ap", "meraki mr", "air-ct",
                      "ip phone", "cisco ip phone")


def short_name(device):
    """SW-ECE-03.campus.local -> SW-ECE-03. Display label only, never the key."""
    return (device or "").split(".")[0].strip()


def identity(device_name, chassis="", chassis_by_name=None):
    """Stable node id.

    Chassis MAC when one is known, because hostnames vary between protocols
    (CDP prints the FQDN, LLDP often the short name) and may repeat across
    buildings. CDP advertises no chassis MAC at all, so a chassis learned for
    the same hostname from any other record is used before falling back to the
    name - otherwise one switch seen over both protocols becomes two nodes and
    a single cable becomes two links.
    """
    name = short_name(device_name).lower()
    return chassis or (chassis_by_name or {}).get(name) or name


def is_infrastructure(capabilities, protocol="lldp", platform=""):
    """Keep switches and routers; drop phones, access points and hosts."""
    if any(platform.lower().startswith(p) for p in ENDPOINT_PLATFORMS):
        return False
    table = CAPS.get(protocol, CAPS["lldp"])
    caps = {c.lower() for c in capabilities}
    if caps & table["endpoint"]:
        return False
    return bool(caps & table["infra"])


def edge_key(a_id, a_port, b_id, b_port):
    """Direction-free key: both ends of one cable produce the same tuple."""
    return tuple(sorted([(a_id, a_port), (b_id, b_port)]))


def build(neighbors, chassis_by_name=None):
    """neighbors: iterable of parser.Neighbor. Returns {"nodes": ..., "links": ...}.

    chassis_by_name maps a short hostname to its chassis MAC (see learn_chassis)
    and is what keeps a device seen over different protocols as one node.
    """
    neighbors = list(neighbors)
    chassis_by_name = chassis_by_name if chassis_by_name is not None else learn_chassis(neighbors)
    nodes, links = {}, {}
    dropped = []

    for n in neighbors:
        if not is_infrastructure(n.capabilities, n.protocol, n.platform):
            dropped.append((n.remote_device, n.capabilities, n.platform))
            continue

        local_id = identity(n.local_device, chassis_by_name=chassis_by_name)
        remote_id = identity(n.remote_device, n.remote_chassis, chassis_by_name)

        nodes.setdefault(local_id, {"id": local_id, "label": short_name(n.local_device)})
        remote = nodes.setdefault(remote_id, {"id": remote_id, "label": short_name(n.remote_device)})
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
        # Confirmed by both ends = high confidence. One end only = investigate:
        # the peer may be unpolled, or the neighbor protocol disabled there.
        link["bidirectional"] = len(link["seen_from"]) == 2
        del link["seen_from"]

    return {
        "nodes": sorted(nodes.values(), key=lambda n: n["label"]),
        "links": sorted(links.values(), key=lambda l: (l["a"]["device"], l["a"]["port"])),
        "dropped_endpoints": dropped,
    }


def summary(graph):
    by_device = defaultdict(int)
    for link in graph["links"]:
        by_device[link["a"]["device"]] += 1
        by_device[link["b"]["device"]] += 1
    return {
        "devices": len(graph["nodes"]),
        "links": len(graph["links"]),
        "endpoints_filtered": len(graph["dropped_endpoints"]),
        "degree": dict(by_device),
    }


def learn_chassis(neighbors):
    """Map short hostname -> chassis MAC, pooled across every record.

    A polled switch never reports its own chassis id, and CDP reports no chassis
    id for anyone. Whichever record did advertise a MAC for a given hostname
    supplies the identity for all the records that did not.
    """
    learned = {}
    for n in neighbors:
        if n.remote_chassis:
            learned.setdefault(short_name(n.remote_device).lower(), n.remote_chassis)
    return learned
