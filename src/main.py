"""Run the pipeline over sample_data/ and print the topology as JSON.

    python3 src/main.py [sample_data_dir]

Filenames encode where the output came from: <DEVICE>__<dialect>.txt
e.g. SW-CORE-01__cisco_lldp.txt
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import parsers
import topology


def collect(directory):
    neighbors = []
    for path in sorted(Path(directory).glob("*__*.txt")):
        device, _, dialect = path.stem.partition("__")
        text = path.read_text()
        # A "session" file is a whole terminal capture: several commands, page
        # headers and prompt echoes, dispatched per command.
        if dialect.endswith("session"):
            neighbors.extend(parsers.parse_capture(text, device))
        else:
            neighbors.extend(parsers.parse(dialect, text, device))
    return neighbors


def main():
    directory = sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent / "sample_data"
    neighbors = collect(directory)
    graph = topology.build(neighbors, topology.learn_chassis(neighbors))

    print(f"parsed {len(neighbors)} neighbor records from {directory}")
    print(json.dumps(topology.summary(graph), indent=2))
    print()
    for link in graph["links"]:
        both = "confirmed both ends" if link["bidirectional"] else "one end only"
        print(f"  {link['a']['device']} {link['a']['port']}"
              f"  <-->  {link['b']['device']} {link['b']['port']}"
              f"   [{'/'.join(link['protocols'])}, {both}]")
    print()
    for name, caps in graph["dropped_endpoints"]:
        print(f"  filtered endpoint: {name} ({','.join(caps)})")

    out = Path(__file__).parent.parent / "topology.json"
    graph.pop("dropped_endpoints")
    out.write_text(json.dumps(graph, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
