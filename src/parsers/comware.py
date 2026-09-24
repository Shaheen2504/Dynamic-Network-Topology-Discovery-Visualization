"""HPE / H3C Comware LLDP.

Target platform: HP 5130 and similar Comware 7 switches.

STATUS: SYNTHETIC ONLY. The format below follows published Comware output for
`display lldp neighbor-information verbose`, but no capture from a real device
has been seen. Passing tests here prove the architecture extends, not that the
parser is correct.
"""

import re

from . import base
from .base import Neighbor

VENDOR = "hpe"

ABBREV = {
    "ge": "GigabitEthernet", "gigabitethernet": "GigabitEthernet",
    "xge": "Ten-GigabitEthernet", "tengigabitethernet": "Ten-GigabitEthernet",
    "fge": "FortyGigE", "fortygige": "FortyGigE",
    "hge": "HundredGigE", "hundredgige": "HundredGigE",
    "bagg": "Bridge-Aggregation", "bridgeaggregation": "Bridge-Aggregation",
    "vlan": "Vlan-interface", "vlaninterface": "Vlan-interface",
}

# Comware spells LLDP capabilities as words rather than letters.
CAPABILITIES = {
    "bridge": base.BRIDGE,
    "router": base.ROUTER,
    "telephone": base.TELEPHONE,
    "station": base.STATION, "station only": base.STATION,
    "wlan access point": base.WLAN_AP,
    "repeater": base.REPEATER,
    "docsis cable device": base.DOCSIS,
    "other": base.OTHER,
}


def parse_lldp_detail(text, local_device):
    """`display lldp neighbor-information verbose`. SYNTHETIC fixture only."""
    neighbors = []
    blocks = re.split(r"^LLDP neighbor-information of port ", text, flags=re.MULTILINE)
    for block in blocks[1:]:
        header = re.match(r"\d+\[(.+?)\]", block)
        remote_port = base.key_value(block, "Port ID")
        if not header or not remote_port:
            continue
        raw_capability = base.key_value(block, "System capabilities enabled")
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=base.expand_port(header.group(1), ABBREV),
            remote_device=base.key_value(block, "System name"),
            remote_port=base.expand_port(remote_port, ABBREV),
            vendor=VENDOR,
            protocol="lldp",
            capabilities=base.canonical(raw_capability.split(","), CAPABILITIES),
            raw_capability=raw_capability,
            remote_chassis=base.normalize_chassis(base.key_value(block, "Chassis ID")),
        ))
    return neighbors


DIALECTS = {"comware_lldp_detail": parse_lldp_detail}
