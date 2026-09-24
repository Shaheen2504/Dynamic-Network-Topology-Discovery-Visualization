"""Parser registry.

Adding a vendor means adding a module beside this one and listing it in
`_MODULES`. Nothing downstream changes: the topology engine, and later the
database, API and dashboard, consume `Neighbor` and never learn which parser
produced it.
"""

from . import aruba_cx, base, cisco, comware
from .base import Neighbor, normalize_chassis

_MODULES = (cisco, comware, aruba_cx)

DIALECTS = {name: fn for module in _MODULES for name, fn in module.DIALECTS.items()}

VENDORS = {name: module.VENDOR for module in _MODULES for name in module.DIALECTS}

# Vendors whose transcripts can be split into individual commands. Only
# platforms with a known prompt convention appear here.
_SESSION_SPLITTERS = (cisco.split_session,)


def parse(dialect, text, local_device):
    """Parse one command's output in a named dialect."""
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; known: {sorted(DIALECTS)}")
    return DIALECTS[dialect](text, local_device)


def parse_capture(text, local_device=None):
    """Parse a whole terminal transcript: several commands, prompts, paging.

    The device name comes from the prompt unless one is given. A command run
    more than once yields duplicate records; collapsing them is the topology
    layer's job, since that is where identity is decided.
    """
    neighbors = []
    for split in _SESSION_SPLITTERS:
        for dialect, host, output in split(text):
            neighbors.extend(parse(dialect, output, local_device or host))
        if neighbors:
            break
    return neighbors
