# Network Topology Discovery

**Status:** in progress — working parser and topology engine, prototype stage.

Automatically discovers and visualises switch-to-switch network topology by collecting
LLDP and CDP neighbour information from network devices, normalising it across vendors,
reconstructing the links, and serving the result as a live graph.

## The problem

No switch knows the network topology. Each one knows only its immediate neighbours, and
reports them in its vendor's own format. A campus with a core layer, ~22 distribution
switches and ~400 access switches therefore has its topology spread across 400 partial,
inconsistent views — and that topology changes as switches are added, moved and removed.

This project merges those partial views into one graph and keeps it current.

## Pipeline

```
Switches ──SSH──> raw CLI text ──> parse ──> normalise ──> resolve identity
                                                                  │
                                                                  ▼
  dashboard <── REST API <── SQLite (+history) <── dedup links ──> graph
```

| Stage | What it does |
|---|---|
| Collect | SSH to each device, run the vendor's neighbour command, store raw output |
| Parse | Vendor-specific text → common `Neighbor` record |
| Normalise | `Gi1/0/24` ≡ `GigabitEthernet1/0/24`; `00e1.6d2a.1b00` ≡ `00e1-6d2a-1b00` |
| Identity | Decide when two records describe the same physical device |
| Filter | Keep switches and routers, drop phones, APs, printers and hosts |
| Dedup | Collapse both ends' view of one cable into one link |
| Persist | Track devices, links, poll history, first-seen / last-seen |
| Probe | Reachability, measured separately from topology |
| Visualise | Interactive graph, device status, port-level link detail |

## Two problems that make this harder than it looks

**One device, several identities.** CDP prints the FQDN (`SW-ECE-03.campus.local`), LLDP
often the short name, and CDP advertises no chassis MAC at all. Keying the graph on
hostname silently splits one switch into two nodes and one cable into two links. Nodes are
therefore keyed on chassis MAC, pooled across every record that advertised one, with the
hostname kept only as a display label.

**Endpoints that look like switches.** IP phones advertise the `Bridge` capability because
they contain a small built-in switch, so a capability allowlist alone keeps them in the
graph. Endpoint capabilities (`Telephone`, `Station`, `WLAN`) are checked first and win.

Both are covered by tests.

## Example

Two switches report the same cable from opposite ends:

```
SW-CORE-01  →  local Te1/1/1,  remote SW-ECE-03.campus.local Te1/1/4     (LLDP + CDP)
SW-ECE-03   →  local Te1/1/4,  remote SW-CORE-01.campus.local Te1/1/1    (LLDP)
```

Sorting the endpoint pair gives both records an identical key, so they collapse to one
link — no reference switch is chosen, and the graph is global rather than relative to any
one device.

## Running the prototype

```bash
python3 src/main.py          # parses sample_data/, prints the topology, writes topology.json
python3 -m pytest tests/ -q  # 6 tests
```

Current output over the bundled multi-vendor samples:

```
parsed 8 neighbor records
{ "devices": 3, "links": 2, "endpoints_filtered": 3 }

  00:e1:6d:2a:1b:00 GigabitEthernet1/0/24   <-->  38:22:d6:f1:0a:40 GigabitEthernet1/0/49    [lldp, confirmed both ends]
  00:e1:6d:2a:1b:00 TenGigabitEthernet1/1/1 <-->  00:e1:6d:2a:2c:00 TenGigabitEthernet1/1/4  [cdp/lldp, confirmed both ends]

  filtered endpoint: SEP001646221F01 (B,T)
  filtered endpoint: LAB-PC-114 (S)
  filtered endpoint: HOSTEL-PRINTER-02 (Station,only)
```

The collection and topology stages are dependency-free, so the whole pipeline runs against
saved switch output with no lab, no licences and no access to live devices.

## Supported input formats

| Vendor / platform | Command | Protocol | Status |
|---|---|---|---|
| Cisco IOS / IOS-XE | `show cdp neighbors` | CDP | **validated against real output** |
| Cisco IOS / IOS-XE | `show lldp neighbors` | LLDP | **validated against real output** |
| Cisco IOS / IOS-XE | `show cdp neighbors detail` | CDP | synthetic fixture only |
| Cisco IOS / IOS-XE | `show lldp neighbors detail` | LLDP | synthetic fixture only |
| HPE / H3C Comware (5130) | `display lldp neighbor-information verbose` | LLDP | synthetic fixture only |
| Aruba AOS-CX (6300, 8300) | `show lldp neighbor-info detail` | LLDP | synthetic fixture only |

"Validated" means parsed from a capture taken off production hardware.
"Synthetic fixture only" means the format was written by hand from published
documentation and has never been checked against a real device - the tests
prove the architecture extends, not that the parser is correct.

### Adding a vendor

1. Add `src/parsers/<vendor>.py` with an interface-abbreviation table, a map
   from that dialect's capability tokens onto the canonical vocabulary, and one
   function per dialect returning `Neighbor` records.
2. Export `DIALECTS = {"<vendor>_<protocol>_<shape>": fn}` and list the module
   in `src/parsers/__init__.py`.
3. Drop a capture in `tests/fixtures/` or `sample_data/`.
4. Add tests.

The topology engine, identity resolution, deduplication and everything after
them need no change: they consume `Neighbor` and never learn which parser
produced it. `tests/test_architecture.py` enforces this - it fails if a vendor
name, platform string or protocol capability code appears in `topology.py`.

### Canonical capability vocabulary

Parsers translate their own protocol's capability encoding into these terms, so
no consumer has to know that CDP's `S` means Switch while LLDP's `S` means
Station:

`switch` `bridge` `router` `wlan-ap` `wlan-controller` `telephone` `station`
`repeater` `docsis` `other`

## Technology

Collection with Netmiko over SSH; parsing with hand-written templates and `ntc-templates`
where a tested one exists; graph logic in Python; SQLite for topology and history;
FastAPI for the backend; React and Cytoscape.js for the dashboard. Docker containers
running `lldpd` provide a lab that emits real LLDP without any switch hardware.

## Collecting from a switch

```bash
export NET_SSH_USERNAME=netops
read -rs NET_SSH_PASSWORD && export NET_SSH_PASSWORD   # not echoed, not in history
python3 src/collector.py 10.0.0.1 -o captures
python3 src/mapview.py captures map.html
```

The collector SSHes to one Cisco switch, runs `show cdp neighbors` and
`show lldp neighbors`, and writes a transcript named after the device's own
prompt. That file is the same shape as a hand-pasted terminal session, so the
parser consumes it unchanged.

Credentials come from `NET_SSH_USERNAME`, `NET_SSH_PASSWORD`, optionally
`NET_SSH_ENABLE`, or a key via `NET_SSH_KEY_FILE`; with a TTY the collector
prompts instead. **There is deliberately no `--password` flag** - arguments are
visible in `ps` output and land in shell history. Secrets are excluded from the
target's repr and scrubbed from driver error text before anything is printed.

Exit codes: `2` unreachable, `3` authentication, `4` missing credentials.

Keep `captures/` out of version control if it holds production output.

## Viewing the map

A first-phase visualisation: the polled switch at the centre, with everything
directly attached to it.

```bash
python3 src/mapview.py tests/fixtures map.html   # tree to stdout, map to map.html
xdg-open map.html                                # or just open the file
```

The first argument is any directory of captures, the second the output file;
both are optional. The tree prints straight to the terminal:

```
CORE-SW-1
├── AD3-00-DIS  [C9500-16X]  (2 links)
│       FiftyGigE1/2/0/18 -> TenGigabitEthernet1/0/16  [cdp]  *
│       FiftyGigE2/2/0/18 -> TenGigabitEthernet2/0/16  [cdp]  *
└── ...

* seen from this device only; the peer has not been polled yet
```

The HTML file is a radial map with a detail table underneath, listing every
core-side and remote port. Peers reached over more than one cable are grouped
into one node carrying a `×2` badge and a thicker spoke, so redundant uplinks
read as redundancy rather than as duplicate devices. Colour separates switching
devices from routing-only ones, derived from canonical capabilities.

The root is not configured anywhere: it is whichever device was actually
polled, meaning the one that appears as `local_device` on the records. Peers are
whatever the graph says is adjacent to it. No device name, port or expected
count appears in the code.

**The generated file is entirely self-contained** - inline SVG and CSS, no
script tag, no CDN, no fetch. A map built from private capture data references
nothing outside itself and can be opened offline. A test enforces this.

## Milestones

- [x] **Phase 1 — Parsing.** Multi-vendor, multi-protocol parsing into a common record; port and chassis normalisation; test fixtures.
- [x] **Phase 2 — Topology engine.** Identity resolution, endpoint filtering, link deduplication, JSON export.
- [x] **Phase 3 — Collection.** One-switch Cisco SSH collector, mocked-transport tests.
- [ ] **Phase 3b — Collection at scale.** Netmiko SSH collector, Docker `lldpd` lab, credential handling.
- [ ] **Phase 4 — Persistence.** SQLite schema, poll history, first-seen / last-seen, topology change detection.
- [ ] **Phase 5 — Monitoring.** Reachability probing, scheduled polls, on-demand refresh.
- [x] **Phase 5a — Basic topology map.** Core-centric text tree and self-contained SVG map.
- [ ] **Phase 6 — Dashboard.** FastAPI backend, React + Cytoscape.js graph, device status, link detail.
- [ ] **Phase 7 — Scale and real network.** Aruba support, bounded concurrency, testing against production switch output.

## Layout

```
src/parsers/base.py       Neighbor schema, canonical capabilities, shared helpers
src/parsers/cisco.py      CDP + LLDP, summary + detail, IOS prompts
src/parsers/comware.py    HPE / H3C
src/parsers/aruba_cx.py   Aruba AOS-CX
src/parsers/__init__.py   dialect registry and dispatch
src/topology.py           identity, filtering, deduplication, graph (vendor-neutral)
src/main.py               runs the pipeline over a directory of captures
src/mapview.py            core-centric view: text tree + self-contained SVG map
src/collector.py          one-switch Cisco SSH collection over Netmiko
sample_data/              synthetic captures, named <DEVICE>__<dialect>.txt
tests/fixtures/           real captures
```

## Next milestone

Live collection: SSH to a Docker `lldpd` lab, store raw output, and run the existing
pipeline against it end to end.
