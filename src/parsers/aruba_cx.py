"""Aruba AOS-CX LLDP.

Target platforms: Aruba 6300 and 8300.

STATUS: SYNTHETIC ONLY. The format below follows published AOS-CX output for
`show lldp neighbor-info detail`, but no capture from a real device has been
seen. This module exists to demonstrate that a new vendor plugs in without
touching anything downstream; it is not evidence of Aruba support.
"""

import re

from . import base
from .base import Neighbor

VENDOR = "aruba"

# AOS-CX names ports positionally ("1/1/49") and does not abbreviate them, so
# there is nothing to expand. The table is kept for symmetry and for the
# named LAG interfaces.
ABBREV = {"lag": "lag"}

CAPABILITIES = {
    "bridge": base.BRIDGE,
    "router": base.ROUTER,
    "telephone": base.TELEPHONE,
    "station": base.STATION, "station only": base.STATION,
    "wlan access point": base.WLAN_AP, "wlan-access-point": base.WLAN_AP,
    "repeater": base.REPEATER,
    "docsis cable device": base.DOCSIS,
    "other": base.OTHER,
}


def parse_lldp_detail(text, local_device):
    """`show lldp neighbor-info detail`. SYNTHETIC fixture only."""
    neighbors = []
    blocks = re.split(r"^(?=Port\s*:)", text, flags=re.MULTILINE)
    for block in blocks:
        local_port = base.key_value(block, "Port")
        remote_port = base.key_value(block, "Port-ID")
        if not local_port or not remote_port:
            continue
        raw_capability = base.key_value(block, "Chassis Capabilities Enabled")
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=base.expand_port(local_port, ABBREV),
            remote_device=base.key_value(block, "Chassis-Name"),
            remote_port=base.expand_port(remote_port, ABBREV),
            vendor=VENDOR,
            protocol="lldp",
            capabilities=base.canonical(raw_capability.split(","), CAPABILITIES),
            raw_capability=raw_capability,
            remote_chassis=base.normalize_chassis(base.key_value(block, "Chassis-ID")),
        ))
    return neighbors


DIALECTS = {"aruba_cx_lldp_detail": parse_lldp_detail}
