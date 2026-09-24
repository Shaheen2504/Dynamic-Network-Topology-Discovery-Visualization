"""Cisco IOS / IOS-XE: CDP and LLDP, in both summary and detail form.

All Cisco-specific knowledge lives here: capability letters, interface
abbreviations, platform naming, and the IOS prompt convention.

Two output shapes exist and they share nothing:

  summary  `show cdp neighbors` / `show lldp neighbors`
           Fixed-width table. No chassis MAC. Long device names are wrapped
           onto their own line (CDP) or truncated to the column width (LLDP).
           VALIDATED against a real C9500-class core switch capture.

  detail   `show cdp neighbors detail` / `show lldp neighbors detail`
           `Label: value` blocks, one per neighbour, including a chassis MAC.
           NOT validated - no real detail output available yet.
"""

import re

from . import base
from .base import Neighbor

VENDOR = "cisco"

# Interface abbreviations as IOS prints them: CDP uses a space ("Ten 2/0/16"),
# LLDP does not ("Te2/0/16"). Both must reduce to the same canonical name or
# the two protocols' view of one cable will not deduplicate.
ABBREV = {
    "fa": "FastEthernet", "fas": "FastEthernet", "fastethernet": "FastEthernet",
    "gi": "GigabitEthernet", "gig": "GigabitEthernet",
    "gigabitethernet": "GigabitEthernet",
    "te": "TenGigabitEthernet", "ten": "TenGigabitEthernet",
    "tengig": "TenGigabitEthernet", "tengige": "TenGigabitEthernet",
    "tengigabitethernet": "TenGigabitEthernet",
    "twe": "TwentyFiveGigE", "twentyfivegige": "TwentyFiveGigE",
    "fo": "FortyGigabitEthernet", "forty": "FortyGigabitEthernet",
    "fortygigabitethernet": "FortyGigabitEthernet",
    # "Fif" is FiftyGigE on C9500/C9600-class hardware. Inferred from the
    # platform numbering, not confirmed by a capture; both protocols abbreviate
    # it identically, so deduplication is unaffected either way.
    "fif": "FiftyGigE", "fifty": "FiftyGigE", "fiftygige": "FiftyGigE",
    "hu": "HundredGigE", "hun": "HundredGigE", "hundredgige": "HundredGigE",
    "po": "Port-channel", "portchannel": "Port-channel",
    "eth": "Ethernet", "ethernet": "Ethernet",
    "vl": "Vlan", "vlan": "Vlan",
}

# CDP capability codes, per the legend IOS prints above the table:
#   R Router, T Trans Bridge, B Source Route Bridge, S Switch, H Host,
#   I IGMP, r Repeater, P Phone, D Remote, C CVTA, M Two-port Mac Relay
# Detail output spells the same capabilities as words, so both forms map here.
CDP_CAPABILITIES = {
    "r": base.ROUTER, "router": base.ROUTER,
    "s": base.SWITCH, "switch": base.SWITCH,
    "b": base.BRIDGE, "source route bridge": base.BRIDGE,
    "t": base.BRIDGE, "trans-bridge": base.BRIDGE, "trans bridge": base.BRIDGE,
    "h": base.STATION, "host": base.STATION,
    "p": base.TELEPHONE, "phone": base.TELEPHONE,
    "r-repeater": base.REPEATER,
    # I (IGMP), D (Remote), C (CVTA) and M (Two-port Mac Relay) say nothing
    # about what kind of device this is, so they map to nothing.
}

# LLDP capability codes, per the legend IOS prints above the table:
#   R Router, B Bridge, T Telephone, C DOCSIS, W WLAN AP, P Repeater,
#   S Station, O Other
# Note S: Station here, Switch in CDP. This is why capability interpretation
# belongs to the parser and not to the topology engine.
LLDP_CAPABILITIES = {
    "r": base.ROUTER, "router": base.ROUTER,
    "b": base.BRIDGE, "bridge": base.BRIDGE,
    "t": base.TELEPHONE, "telephone": base.TELEPHONE,
    "c": base.DOCSIS, "docsis": base.DOCSIS,
    "w": base.WLAN_AP, "wlan access point": base.WLAN_AP,
    "p": base.REPEATER, "repeater": base.REPEATER,
    "s": base.STATION, "station": base.STATION,
    "o": base.OTHER, "other": base.OTHER,
}

# Platform-derived capability, needed because CDP capability codes cannot
# distinguish wireless kit from switches: Meraki access points advertise
# "R S" (Router, Switch) and Aironet access points advertise "T B I"
# (Trans Bridge, Source Route Bridge, IGMP). The platform string is the only
# discriminator CDP offers. Applied in addition to the capability codes.
PLATFORM_CAPABILITIES = (
    ("air-cap", base.WLAN_AP),
    ("air-lap", base.WLAN_AP),
    ("air-ap", base.WLAN_AP),
    ("meraki mr", base.WLAN_AP),
    ("air-ct", base.WLAN_CONTROLLER),
    ("cisco ip phone", base.TELEPHONE),
)


def _platform_capabilities(platform):
    p = (platform or "").lower()
    return frozenset(cap for prefix, cap in PLATFORM_CAPABILITIES
                     if p.startswith(prefix))


def _port(value):
    return base.expand_port(value, ABBREV)


def _is_noise(line):
    """Blank lines, the table footer, and echoed CLI prompts."""
    return (not line.strip()
            or line.lstrip().startswith("Total ")
            or bool(re.match(r"^\S+[#>]", line)))


# --------------------------------------------------------------- CDP summary

_CDP_COLUMNS = ["Device ID", "Local Intrfce", "Holdtme", "Capability",
                "Platform", "Port ID"]


def parse_cdp_summary(text, local_device):
    """`show cdp neighbors`. VALIDATED against real output."""
    neighbors, cols, pending = [], None, None

    for line in text.splitlines():
        if line.lstrip().startswith("Device ID") and "Local Intrfce" in line:
            cols = base.columns(line, _CDP_COLUMNS)
            continue
        if cols is None:
            continue
        if _is_noise(line):
            pending = None
            continue

        # A Device ID longer than its column is printed alone on its own line,
        # with the rest of the record indented on the line below. The name
        # overflows past the column boundary, so the name cell cannot be used
        # to detect this; a data row always carries a numeric holdtime and a
        # name-only line never does.
        if not line[cols[2]].strip().isdigit():
            pending = line.strip()
            continue

        device = line[cols[0]].strip() or pending
        pending = None
        local_port, remote_port = line[cols[1]].strip(), line[cols[5]].strip()
        if not device or not local_port or not remote_port:
            continue

        raw_capability = line[cols[3]].strip()
        platform = line[cols[4]].strip()
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=_port(local_port),
            remote_device=device,
            remote_port=_port(remote_port),
            platform=platform,
            vendor=VENDOR,
            protocol="cdp",
            capabilities=(base.canonical(raw_capability.split(), CDP_CAPABILITIES)
                          | _platform_capabilities(platform)),
            raw_capability=raw_capability,
        ))
    return neighbors


# -------------------------------------------------------------- LLDP summary

_LLDP_COLUMNS = ["Device ID", "Local Intf", "Hold-time", "Capability", "Port ID"]


def parse_lldp_summary(text, local_device):
    """`show lldp neighbors`. VALIDATED against real output.

    The device name is truncated to the column width with no trailing space
    before the interface - a 20-character name runs straight into it, as in
    "SOME-LONG-HOSTNAME-XTe1/0/1" - so only column slicing recovers either
    field.
    """
    neighbors, cols = [], None

    for line in text.splitlines():
        if line.lstrip().startswith("Device ID") and "Local Intf" in line:
            cols = base.columns(line, _LLDP_COLUMNS)
            continue
        if cols is None or _is_noise(line):
            continue

        device = line[cols[0]].strip()
        local_port, remote_port = line[cols[1]].strip(), line[cols[4]].strip()
        if not device or not local_port or not remote_port:
            continue

        raw_capability = line[cols[3]].strip()
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=_port(local_port),
            remote_device=device,
            remote_port=_port(remote_port),
            vendor=VENDOR,
            protocol="lldp",
            capabilities=base.canonical(raw_capability.split(","),
                                        LLDP_CAPABILITIES),
            raw_capability=raw_capability,
        ))
    return neighbors


# ------------------------------------------------ detail formats (UNVALIDATED)

def parse_cdp_detail(text, local_device):
    """`show cdp neighbors detail`. NOT validated against real output."""
    neighbors = []
    for block in re.split(r"^-{10,}\s*$", text, flags=re.MULTILINE):
        m = re.search(r"^Interface:\s*(.+?),\s*Port ID \(outgoing port\):\s*(.+?)\s*$",
                      block, re.MULTILINE)
        if not m:
            continue
        plat = re.search(r"^Platform:\s*(.+?),\s*Capabilities:\s*(.+?)\s*$",
                         block, re.MULTILINE)
        platform = plat.group(1) if plat else ""
        raw_capability = plat.group(2) if plat else ""
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=_port(m.group(1)),
            remote_device=base.key_value(block, "Device ID"),
            remote_port=_port(m.group(2)),
            platform=platform,
            vendor=VENDOR,
            protocol="cdp",
            capabilities=(base.canonical(raw_capability.split(), CDP_CAPABILITIES)
                          | _platform_capabilities(platform)),
            raw_capability=raw_capability,
        ))
    return neighbors


def parse_lldp_detail(text, local_device):
    """`show lldp neighbors detail`. NOT validated against real output."""
    neighbors = []
    for block in re.split(r"^-{10,}\s*$", text, flags=re.MULTILINE):
        local_port = base.key_value(block, "Local Intf")
        remote_port = base.key_value(block, "Port id")
        if not local_port or not remote_port:
            continue
        raw_capability = base.key_value(block, "Enabled Capabilities")
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=_port(local_port),
            remote_device=base.key_value(block, "System Name"),
            remote_port=_port(remote_port),
            vendor=VENDOR,
            protocol="lldp",
            capabilities=base.canonical(raw_capability.split(","),
                                        LLDP_CAPABILITIES),
            raw_capability=raw_capability,
            remote_chassis=base.normalize_chassis(base.key_value(block, "Chassis id")),
        ))
    return neighbors


DIALECTS = {
    "cisco_cdp_summary": parse_cdp_summary,
    "cisco_lldp_summary": parse_lldp_summary,
    "cisco_cdp_detail": parse_cdp_detail,
    "cisco_lldp_detail": parse_lldp_detail,
}


# ------------------------------------------------------- terminal transcripts

# IOS prompt: hostname followed by "#" (enable) or ">" (user exec).
_PROMPT = re.compile(r"^(?P<host>[A-Za-z0-9][\w.\-]*)[#>]\s*(?P<cmd>sh\S*\s+.*\S)\s*$")


def _dialect_for(command):
    command = command.lower()
    if "neigh" not in command:
        return None                       # e.g. "show cdp" global settings
    protocol = "cdp" if "cdp" in command else "lldp" if "lldp" in command else None
    if not protocol:
        return None
    shape = "detail" if "det" in command else "summary"
    return f"cisco_{protocol}_{shape}"


def split_session(text):
    """Split a terminal transcript into (dialect, hostname, output) blocks.

    An abbreviated command that produced no output ("sh cdp ne") yields an
    empty block and simply parses to nothing.
    """
    blocks, current = [], None
    for line in text.splitlines():
        m = _PROMPT.match(line)
        if m:
            dialect = _dialect_for(m.group("cmd"))
            current = [dialect, m.group("host"), []] if dialect else None
            if current:
                blocks.append(current)
            continue
        if current:
            current[2].append(line)
    return [(d, h, "\n".join(lines)) for d, h, lines in blocks]
