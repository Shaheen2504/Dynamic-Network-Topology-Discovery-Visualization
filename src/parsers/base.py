"""Vendor-neutral parsing contract.

Everything downstream of the parsers - identity resolution, deduplication, the
graph, and later the database and dashboard - sees only `Neighbor`. Nothing in
this module knows about any specific vendor, and nothing outside the parser
package should need to.
"""

import re
from dataclasses import dataclass, field


# Canonical capability vocabulary. Each parser translates its own protocol's
# capability encoding into these terms, so no consumer ever has to know that
# CDP's "S" means Switch while LLDP's "S" means Station.
SWITCH = "switch"
BRIDGE = "bridge"
ROUTER = "router"
WLAN_AP = "wlan-ap"
WLAN_CONTROLLER = "wlan-controller"
TELEPHONE = "telephone"
STATION = "station"
REPEATER = "repeater"
DOCSIS = "docsis"
OTHER = "other"

CAPABILITIES = frozenset({SWITCH, BRIDGE, ROUTER, WLAN_AP, WLAN_CONTROLLER,
                          TELEPHONE, STATION, REPEATER, DOCSIS, OTHER})


@dataclass(frozen=True)
class Neighbor:
    """One neighbour, as reported by one device, in vendor-neutral form."""

    local_device: str
    local_port: str
    remote_device: str
    remote_port: str
    platform: str = ""                      # "" when the protocol omits it
    vendor: str = ""                        # of the parser, not the neighbour
    protocol: str = ""                      # "cdp" | "lldp"
    capabilities: frozenset = frozenset()   # canonical terms, see above
    raw_capability: str = ""                # verbatim, for evidence/debugging
    remote_chassis: str = ""                # "" when not advertised


def canonical(tokens, mapping):
    """Map a dialect's capability tokens onto the canonical vocabulary.

    Unrecognised tokens are dropped rather than guessed: an unknown capability
    should not silently become an endpoint or an infrastructure device.
    """
    out = {mapping[t.strip().lower()] for t in tokens
           if t.strip().lower() in mapping}
    return frozenset(out)


def normalize_chassis(value):
    """00e1.6d2a.1b00, 00e1-6d2a-1b00 and 00:E1:6D:2A:1B:00 are one identity."""
    hex_only = re.sub(r"[^0-9a-f]", "", (value or "").lower())
    if len(hex_only) != 12:
        return ""
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def expand_port(port, abbrev):
    """Expand a vendor's interface abbreviation using that vendor's table.

    A prefix whose abbreviation the vendor does not define is returned
    untouched: many platforms name ports in ways that only look like
    abbreviations ("port32"), use a bare position ("1/1/1", "28") or a plain
    word ("LAN"). Expanding those would invent a port that does not exist.
    """
    port = (port or "").strip()
    m = re.match(r"^([A-Za-z][A-Za-z-]*)\s*([\d/.:]+)$", port)
    if not m:
        return port
    name, number = m.groups()
    full = abbrev.get(name.lower().replace("-", ""))
    return full + number if full else port


def key_value(block, label):
    """Read `Label : value` out of a block of detail-format output."""
    m = re.search(rf"^\s*{label}\s*:\s*(.+?)\s*$", block, re.MULTILINE)
    return m.group(1) if m else ""


def columns(header, labels):
    """Column spans taken from a table header, so widths are never hardcoded.

    Fixed-width tables cannot be split on whitespace: field values contain
    spaces, and a value may run flush against the next column with no gap.
    """
    starts = [header.index(l) for l in labels]
    return [slice(s, e) for s, e in zip(starts, starts[1:] + [len(header) + 4096])]
