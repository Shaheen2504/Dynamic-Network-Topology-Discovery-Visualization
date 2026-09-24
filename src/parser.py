"""Parse Cisco CDP/LLDP and HPE Comware LLDP neighbour output.

Two shapes of Cisco output exist and they are nothing alike:

  summary  `show cdp neighbors` / `show lldp neighbors`
           Fixed-width columns. No chassis MAC. Device names are wrapped (CDP)
           or truncated to the column width (LLDP). Verified against a real
           C9500 core switch capture.

  detail   `show cdp neighbors detail` / `show lldp neighbors detail`
           Key: value blocks, one per neighbour, including a chassis MAC.
           NOT yet verified against real output.

Summary parsing must be done by column offset, not by splitting on whitespace:
LLDP truncates the device name at exactly 20 characters and runs it straight
into the interface with no separator ("M-HOSTEL-00-DIS.iithFif2/2/0/21"), and
both the platform and port fields contain spaces ("Meraki MR", "Ten 2/0/16").

rdx: hand-written, dependency-free so the pipeline runs against saved output
with no lab. ntc-templates is the better production choice for the Cisco
dialects once live collection exists.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Neighbor:
    local_device: str
    local_port: str
    remote_device: str
    remote_port: str
    platform: str = ""
    remote_chassis: str = ""          # summary output does not carry one
    capabilities: tuple = field(default_factory=tuple)
    protocol: str = ""


# Cisco interface abbreviations, as printed by CDP ("Ten 2/0/16", "Gig 0") and
# LLDP ("Te2/0/16"). Explicit table rather than prefix matching, because prefix
# matching turns the FortiGate port "port32" into "Port-channel32".
_PORT_ABBREV = {
    "fa": "FastEthernet", "fas": "FastEthernet", "fastethernet": "FastEthernet",
    "gi": "GigabitEthernet", "gig": "GigabitEthernet",
    "gigabitethernet": "GigabitEthernet",
    "te": "TenGigabitEthernet", "ten": "TenGigabitEthernet",
    "tengig": "TenGigabitEthernet", "tengige": "TenGigabitEthernet",
    "tengigabitethernet": "TenGigabitEthernet",
    "twe": "TwentyFiveGigE", "twentyfivegige": "TwentyFiveGigE",
    "fo": "FortyGigabitEthernet", "forty": "FortyGigabitEthernet",
    "fortygigabitethernet": "FortyGigabitEthernet",
    # "Fif" on a C9600-class chassis is FiftyGigE. Inferred from the platform,
    # not confirmed by the capture; harmless either way, since both protocols
    # abbreviate it identically and so still deduplicate.
    "fif": "FiftyGigE", "fifty": "FiftyGigE", "fiftygige": "FiftyGigE",
    "hu": "HundredGigE", "hun": "HundredGigE", "hundredgige": "HundredGigE",
    "po": "Port-channel", "portchannel": "Port-channel",
    "eth": "Ethernet", "ethernet": "Ethernet",
    "bagg": "Bridge-Aggregation", "bridgeaggregation": "Bridge-Aggregation",
    "vl": "Vlan", "vlan": "Vlan",
}


def normalize_port(port):
    """"Ten 2/0/16", "Te2/0/16" and "Ten-GigabitEthernet2/0/16" are one port.

    Anything whose prefix is not a known abbreviation is returned untouched -
    "port32", "Port 0", "LAN", "1/1/1" and "28" are real port names on
    FortiGate, Meraki, Aruba and HP devices, not abbreviations to expand.
    """
    port = (port or "").strip()
    m = re.match(r"^([A-Za-z][A-Za-z-]*)\s*([\d/.:]+)$", port)
    if not m:
        return port
    name, number = m.groups()
    full = _PORT_ABBREV.get(name.lower().replace("-", ""))
    return full + number if full else port


def normalize_chassis(value):
    """00e1.6d2a.1b00, 00e1-6d2a-1b00 and 00:e1:6d:2a:1b:00 are one identity."""
    hex_only = re.sub(r"[^0-9a-f]", "", (value or "").lower())
    if len(hex_only) != 12:
        return ""
    return ":".join(hex_only[i:i + 2] for i in range(0, 12, 2))


def _slices(header, labels):
    """Column spans derived from the header line, so widths are never hardcoded."""
    starts = [header.index(l) for l in labels]
    return [slice(s, e) for s, e in zip(starts, starts[1:] + [len(header) + 4096])]


def _is_noise(line):
    return (not line.strip()
            or line.lstrip().startswith("Total ")
            or re.match(r"^\S+[#>]", line))


# ---------------------------------------------------------------- CDP summary

_CDP_HEADER = "Local Intrfce"
_CDP_LABELS = ["Device ID", "Local Intrfce", "Holdtme", "Capability",
               "Platform", "Port ID"]


def parse_cisco_cdp_summary(text, local_device):
    """`show cdp neighbors` on Cisco IOS / IOS-XE.

    A Device ID longer than the column sits alone on its own line with the rest
    of the record indented on the line below.
    """
    neighbors, cols, pending = [], None, None

    for line in text.splitlines():
        if _CDP_HEADER in line and line.lstrip().startswith("Device ID"):
            cols = _slices(line, _CDP_LABELS)
            continue
        if cols is None or _is_noise(line):
            if _is_noise(line):
                pending = None
            continue

        # A wrapped name overflows its column, so testing the name cell is not
        # enough ("M-HOSTEL-00-DIS.iith.ac.in" spills past it). A real data row
        # always has a numeric holdtime; a name-only line never does.
        if not line[cols[2]].strip().isdigit():
            pending = line.strip()
            continue

        device = line[cols[0]].strip() or pending
        pending = None
        local_port = line[cols[1]].strip()
        remote_port = line[cols[5]].strip()
        if not device or not local_port or not remote_port:
            continue

        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=normalize_port(local_port),
            remote_device=device,
            remote_port=normalize_port(remote_port),
            platform=line[cols[4]].strip(),
            capabilities=tuple(line[cols[3]].split()),   # CDP: space separated
            protocol="cdp",
        ))
    return neighbors


# --------------------------------------------------------------- LLDP summary

_LLDP_HEADER = "Local Intf"
_LLDP_LABELS = ["Device ID", "Local Intf", "Hold-time", "Capability", "Port ID"]


def parse_cisco_lldp_summary(text, local_device):
    """`show lldp neighbors` on Cisco IOS / IOS-XE.

    The device name is truncated to the column width with no trailing space, so
    it can only be recovered by slicing at the header offset.
    """
    neighbors, cols = [], None

    for line in text.splitlines():
        if _LLDP_HEADER in line and line.lstrip().startswith("Device ID"):
            cols = _slices(line, _LLDP_LABELS)
            continue
        if cols is None or _is_noise(line):
            continue

        device = line[cols[0]].strip()
        local_port = line[cols[1]].strip()
        remote_port = line[cols[4]].strip()
        if not device or not local_port or not remote_port:
            continue

        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=normalize_port(local_port),
            remote_device=device,
            remote_port=normalize_port(remote_port),
            capabilities=tuple(c for c in line[cols[3]].strip().split(",") if c),
            protocol="lldp",
        ))
    return neighbors


# ------------------------------------------- detail formats (NOT yet verified)

def _field(block, label):
    m = re.search(rf"^\s*{label}\s*:\s*(.+?)\s*$", block, re.MULTILINE)
    return m.group(1) if m else ""


def parse_cisco_lldp_detail(text, local_device):
    """`show lldp neighbors detail`. Unverified against real output."""
    neighbors = []
    for block in re.split(r"^-{10,}\s*$", text, flags=re.MULTILINE):
        local_port, remote_port = _field(block, "Local Intf"), _field(block, "Port id")
        if not local_port or not remote_port:
            continue
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=normalize_port(local_port),
            remote_device=_field(block, "System Name"),
            remote_port=normalize_port(remote_port),
            remote_chassis=normalize_chassis(_field(block, "Chassis id")),
            capabilities=tuple(c.strip() for c in
                               _field(block, "Enabled Capabilities").split(",") if c.strip()),
            protocol="lldp",
        ))
    return neighbors


def parse_cisco_cdp_detail(text, local_device):
    """`show cdp neighbors detail`. Unverified against real output."""
    neighbors = []
    for block in re.split(r"^-{10,}\s*$", text, flags=re.MULTILINE):
        m = re.search(r"^Interface:\s*(.+?),\s*Port ID \(outgoing port\):\s*(.+?)\s*$",
                      block, re.MULTILINE)
        if not m:
            continue
        platform = re.search(r"^Platform:\s*(.+?),\s*Capabilities:\s*(.+?)\s*$",
                             block, re.MULTILINE)
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=normalize_port(m.group(1)),
            remote_device=_field(block, "Device ID"),
            remote_port=normalize_port(m.group(2)),
            platform=platform.group(1) if platform else "",
            capabilities=tuple(platform.group(2).split()) if platform else (),
            protocol="cdp",
        ))
    return neighbors


def parse_comware_lldp_detail(text, local_device):
    """`display lldp neighbor-information verbose`. Unverified against real output."""
    neighbors = []
    for block in re.split(r"^LLDP neighbor-information of port ", text, flags=re.MULTILINE)[1:]:
        header = re.match(r"\d+\[(.+?)\]", block)
        remote_port = _field(block, "Port ID")
        if not header or not remote_port:
            continue
        neighbors.append(Neighbor(
            local_device=local_device,
            local_port=normalize_port(header.group(1)),
            remote_device=_field(block, "System name"),
            remote_port=normalize_port(remote_port),
            remote_chassis=normalize_chassis(_field(block, "Chassis ID")),
            capabilities=tuple(c.strip() for c in
                               _field(block, "System capabilities enabled").split(",") if c.strip()),
            protocol="lldp",
        ))
    return neighbors


PARSERS = {
    "cisco_cdp_summary": parse_cisco_cdp_summary,
    "cisco_lldp_summary": parse_cisco_lldp_summary,
    "cisco_cdp_detail": parse_cisco_cdp_detail,
    "cisco_lldp_detail": parse_cisco_lldp_detail,
    "comware_lldp_detail": parse_comware_lldp_detail,
    # kept so existing fixtures keep working
    "cisco_cdp": parse_cisco_cdp_detail,
    "cisco_lldp": parse_cisco_lldp_detail,
    "comware_lldp": parse_comware_lldp_detail,
}


def parse(dialect, text, local_device):
    if dialect not in PARSERS:
        raise ValueError(f"no parser for dialect {dialect!r}; have {sorted(PARSERS)}")
    return PARSERS[dialect](text, local_device)


# --------------------------------------------------------- whole-session input

_PROMPT = re.compile(r"^(?P<host>[A-Za-z0-9][\w.\-]*)[#>]\s*(?P<cmd>sh\S*\s+.*\S)\s*$")


def _dialect_for(cmd):
    cmd = cmd.lower()
    if "neigh" not in cmd:
        return None
    proto = "cdp" if "cdp" in cmd else "lldp" if "lldp" in cmd else None
    if not proto:
        return None
    return f"cisco_{proto}_{'detail' if 'det' in cmd else 'summary'}"


def parse_capture(text, local_device=None):
    """Parse a whole terminal capture: several commands, prompts, page headers.

    The device name is taken from the CLI prompt unless one is passed in. A
    command run more than once yields duplicate records; deduplication happens
    in the topology layer, which is where identity is decided.
    """
    blocks, current = [], None
    for line in text.splitlines():
        m = _PROMPT.match(line)
        if m:
            dialect = _dialect_for(m.group("cmd"))
            current = {"dialect": dialect, "host": m.group("host"), "lines": []} if dialect else None
            if current:
                blocks.append(current)
            continue
        if current:
            current["lines"].append(line)

    neighbors = []
    for b in blocks:
        device = local_device or b["host"]
        neighbors.extend(parse(b["dialect"], "\n".join(b["lines"]), device))
    return neighbors
