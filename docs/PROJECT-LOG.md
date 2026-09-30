# Project log — Network Topology Discovery

A running record of the CDP/LLDP topology-discovery work: what was decided, what
was corrected, what was built, and what is still unverified.

This is a synthesised engineering log rather than a verbatim transcript. It keeps
the decisions and their reasons, which is what is worth re-reading; the exact
wording of the conversation is not reproduced.

Period covered: 2026-09-23 to 2026-09-30.
Repository: `Shaheen2504/Dynamic-Network-Topology-Discovery-Visualization` (private).

---

## 1. Where this came from

A meeting with the professor and the campus network team set the requirement: the
Cybersecurity Operations group needs to know the actual topology of the IITH
campus network, and the only existing documentation covers a single building and
is years out of date.

What was asked for:

- A **dynamic** map — switches get added, moved and removed, and a static diagram
  goes stale immediately.
- Coverage of the **whole campus**: core, roughly 22 distribution switches, and
  roughly 400 access switches.
- Built from **CDP and LLDP neighbour data** pulled off the switches themselves.
- **Switch-to-switch only.** End devices, phones and access points are explicitly
  out of scope.
- A **dashboard** showing connectivity and reachability, with unreachable devices
  marked.
- Scheduled polling plus an **on-demand refresh** ("if I want it today, now").
- Later: alerts routed to the technician responsible for that building.

Hardware in scope, as described by the network team:

| Role | Platform | Protocol |
|---|---|---|
| Core | Cisco C9500/C9600 class | CDP + LLDP |
| Distribution (~22) | Cisco C9500-16X / 40X | CDP + LLDP |
| Access (~400) | Cisco 9300 | CDP + LLDP |
| Access (~50) | HP 5130 (Comware) | LLDP only |
| Access | Aruba 6300 / 8300 (AOS-CX) | LLDP only |

---

## 2. Decisions and corrections

Recorded with reasons, because several reversed an initial assumption.

**Mininet will not work.** It was suggested as the lab environment. Open vSwitch
does not speak CDP and has no switch CLI to parse, so it cannot exercise the part
of the problem that matters. GNS3 or EVE-NG with real images, or Docker
containers running `lldpd`, are the workable options.

**Cisco images are not free.** An early plan assumed IOSvL2 in GNS3; that needs a
Cisco CML licence. Corrected: the whole pipeline can be built on LLDP alone using
`lldpd` containers, with CDP handled from saved text. The lab turned out not to be
needed at all once real captures arrived.

**Most of the parser did not need writing from scratch.** `ntc-templates` already
ships TextFSM templates for Cisco. The custom work is the vendors it does not
cover well. The project ended up with hand-written parsers anyway, to stay
dependency-free, but `ntc-templates` remains the better production choice for the
Cisco dialects.

**Real switches are not required to build this.** Parsing is a pure function of
text. Everything above the collector — parse, normalise, identity, dedup, graph,
map — is testable from saved output. This is why roughly 85% of the project was
finished before any credential request was made, and it is deliberately the
reverse of asking for access to 400 production switches on day one.

**The dashboard was moved ahead of the database.** A visual map of the real core
is what makes the case to the professor; persistence demos as nothing.

**Capability semantics belong in the parser, not the topology engine.** See §4.

---

## 3. Architecture

```
switch ──SSH──> raw transcript ──> parse ──> normalise ──> resolve identity
                                                                  │
                                                                  ▼
        map / dashboard <── topology graph <── deduplicate links <┘
```

```
src/parsers/base.py       Neighbor schema, canonical capabilities, shared helpers
src/parsers/cisco.py      CDP + LLDP, summary + detail, IOS prompts, platform rules
src/parsers/comware.py    HPE / H3C
src/parsers/aruba_cx.py   Aruba AOS-CX
src/parsers/__init__.py   dialect registry, parse(), parse_capture()
src/topology.py           identity, filtering, deduplication, graph (vendor-neutral)
src/mapview.py            core-centric text tree + self-contained SVG map
src/collector.py          one-switch Cisco SSH collection
src/main.py               runs the pipeline over a directory of captures
```

**The common schema.** Every parser emits the same record and nothing downstream
knows which one produced it:

```
local_device  local_port  remote_device  remote_port
platform      vendor      protocol
capabilities      # frozenset of canonical terms
raw_capability    # verbatim, kept as evidence
remote_chassis    # "" when the dialect does not advertise one
```

**Canonical capability vocabulary:** `switch` `bridge` `router` `wlan-ap`
`wlan-controller` `telephone` `station` `repeater` `docsis` `other`.

**Adding a vendor:** new module with an abbreviation table, a capability map and
one function per dialect; export `DIALECTS`; add a fixture; add tests. Nothing
downstream changes. `tests/test_architecture.py` enforces this by failing if a
vendor name, platform string or protocol code appears in `topology.py`.

---

## 4. Technical findings

The substance of the work. Most were found by running code against real output,
not by planning.

### The first parser could not read the real data at all

The initial parsers targeted `show cdp neighbors detail` and
`show lldp neighbors detail`. The capture that arrived was the **summary** form.
Both parsers returned **zero records**. Ten concrete mismatches followed:

- Summary output is a fixed-width table, not `Key: value` lines.
- LLDP truncates the device name to exactly 20 characters and runs it flush
  against the interface with no separator, so whitespace splitting is impossible
  and columns must be sliced at header-derived offsets.
- CDP wraps a long Device ID onto its own line with the data indented below.
- Summary output carries **no chassis MAC**, which weakens identity resolution.
- Field values contain spaces (`Meraki MR`, `Ten 2/0/16`), defeating tokenisation.

### `S` means Switch in CDP and Station in LLDP

The single most damaging bug found. A shared capability table would have
classified every CDP-discovered switch as an end station and deleted the entire
core from the map. Capability interpretation is now done per protocol, inside the
parser, and the topology engine never sees a protocol code.

### Access points advertise themselves as switches

Meraki APs advertise `R S` (Router, Switch). Cisco Aironet APs advertise `T B I`
(Trans Bridge, Source Route Bridge, IGMP). Neither is distinguishable from a real
switch by capability alone. The **platform string is the only discriminator CDP
offers**, so `cisco.py` maps platform prefixes (`AIR-CAP`, `AIR-CT`, `Meraki MR`)
onto the canonical `wlan-ap` / `wlan-controller` terms. This lives with the vendor
that needs it; the engine only ever sees canonical capabilities.

### One switch became two nodes

The first run over the real capture produced 4 devices and 3 links where the
answer was 3 and 2. CDP advertises no chassis MAC, so a switch seen over CDP was
keyed on hostname while the same switch seen over LLDP was keyed on MAC — and its
uplink appeared as two separate cables. Fixed by pooling chassis MACs by hostname
across all records. On a 400-switch map this would have corrupted the topology
silently.

### Prefix matching corrupted real port names

`port32` on a FortiGate became `Port-channel32`; `Port 0` on a Meraki became
`Port-channel0`. Replaced with an explicit abbreviation table per vendor. Anything
not in the table is returned untouched, because `1/1/1`, `28`, `LAN` and `port32`
are real names, not abbreviations.

### Deduplication makes the graph global, not relative

A meeting discussion asked whether the map would be relative to whichever switch
you started from. It is not. Each cable's key is the sorted pair of its two
endpoints, so both ends' reports collapse to one edge and no reference switch is
ever chosen.

### Port numbers on the map links (2026-09-29)

Requested by Raqueeb after reviewing the map. Each spoke now carries the core-side
port near the hub and the peer-side port near the peer; redundant uplinks list
every port on the one spoke. Full Cisco names (`TenGigabitEthernet1/0/16`) made
labels overlap near the hub, so the map shows a display-only short form
(`Te1/0/16`) by cutting long alphabetic prefixes to two letters; short real names
(`port32`, `LAN`, `1/1/1`) are untouched, and the tooltip and table keep full
names. Known cosmetic mismatch: `FiftyGigE` renders as `Fi`, where Cisco's CLI
prints `Fif`.

### Multi-switch map (2026-09-30) - synthetic second switch

The map was core-only: one polled switch at the centre, and with two polled
switches it silently mapped the first and ignored the rest. It is now a radial
tree tiered by hop distance from the core (core, distribution, access), with
each node's sector sized by the number of devices below it.

No real second switch has been captured, so `tests/synthetic/LH-00-DIS__cisco_session.txt`
was hand-written for LH-00-DIS. Its two core uplinks (Te1/0/16, Te2/0/16) mirror
exactly what the real CORE-SW-1 capture reports, and a test enforces that the
fake never contradicts the real core. Everything below LH-00-DIS - four access
switches, one LLDP-only HPE 5130, a Meraki AP, and the core's platform string
`C9606R` - is invented. The column layout is copied from the real output so the
validated summary parsers read it unchanged.

**The topology engine needed no change.** Merging the two captures produced 53
links: the real 48 plus 5 new LH-to-access cables. The two core-to-LH cables
were each reported from both ends and merged into one confirmed link each, as
`edge_key` was designed to do. A mutation check (unsorted `edge_key`) fails four
of the new tests.

Choices made:
- The core is the polled device with the most links; only a polled device can
  be the centre.
- Tier names come from hop distance, not platform, so the FortiGate and server
  racks one hop from the core are labelled `distribution`. Correct by topology,
  loose by role.
- Confirmed links are solid, single-ended ones dashed. On real data today every
  link is dashed, which is accurate: only one switch has been polled.
- Any capture containing `SYNTHETIC TEST DATA` puts a warning banner on the map,
  so invented devices cannot be shown as the real network by mistake.

### Netmiko strips exactly what the parser needs

`send_command()` removes the prompt and the echoed command, but `parse_capture()`
splits a transcript on prompt lines. The collector therefore reassembles the
transcript from `find_prompt()`. That prompt is also where the hostname comes
from, so no device name is configured anywhere.

---

## 5. Results against the real capture

Source: complete `CORE-SW-1` transcript — two runs of `show cdp neighbors`
(51 entries each) and one `show lldp neighbors` (39 entries).

```
records parsed      141   (CDP 102, LLDP 39)
records failing       0
endpoints filtered   48   (24 distinct: Meraki APs, Aironet APs, one WLC)
unique links         48   (18 confirmed over both CDP and LLDP)
devices              36   (the core plus 35 directly attached)
```

No record fails to parse. The count is asserted against the switch's own footer
text rather than a hardcoded number.

**What the map surfaced:** 11 peers have redundant uplinks. `FortiGate-1800F` has
four; ten distribution switches have dual uplinks landing on `Fif1/…` and `Fif2/…`,
which is the StackWise-Virtual pair split. Every link is currently marked as seen
from one end only, which is correct — exactly one switch has been polled.

---

## 6. Status

| Component | Status |
|---|---|
| Cisco `show cdp neighbors` | **Validated against real production output** |
| Cisco `show lldp neighbors` | **Validated against real production output** |
| Cisco `... detail` (both protocols) | Implemented, synthetic fixture only |
| HPE Comware LLDP | Implemented, synthetic fixture only |
| Aruba AOS-CX LLDP | Implemented, synthetic fixture only |
| Topology engine | Validated; vendor-neutrality enforced by test |
| Core-centric map | Validated against the real capture; port labels on links |
| Multi-switch tiered map | Implemented; **tested on a synthetic second switch only** |
| SSH collector | Implemented, tested against a mocked transport; **never run against hardware** |
| Persistence, scheduling, reachability, alerts, auth | Not implemented |

"Synthetic fixture only" means the format was written by hand from published
documentation and has never been checked against a real device. Those tests prove
the architecture extends; they say nothing about correctness.

---

## 7. Commit history

| Commit | Summary |
|---|---|
| `6aecb05` | Project architecture and documentation |
| `82c7008` | Multi-vendor switch output samples |
| `42bb235` | LLDP/CDP parser for Cisco and Comware |
| `3e7caee` | Deduplicated topology graph |
| `e17b310` | Pipeline tests |
| `8b25494` | Parse real Cisco core switch CDP/LLDP summary output |
| `c8111a0` | Restructure parsing into a vendor-neutral plugin layer |
| `18f78df` | Add basic core-distribution topology map |
| `17539d2` | Add SSH collector for Cisco CDP/LLDP |
| `a431662` | Port numbers on map links; this project log |
| `fe18b83` | Tiered multi-switch map; synthetic LH-00-DIS capture |

Test suite: 6 → 19 → 29 → 40 → 62 → 64 → **76 passing**.

`18f78df`, `17539d2`, `a431662` and the multi-switch commit are not yet pushed.

---

## 8. Security and data handling

**The repository is private, deliberately.** `tests/fixtures/` holds a real
capture of the production core: 36 hostnames and the internal naming scheme, the
core-to-distribution port map, platform inventory, a perimeter firewall with its
port assignments, and AP MAC addresses. Publishing that would be a reconnaissance
map of a live campus network, and it is the professor's data, not ours, to
publish. Sanitising was considered and rejected in favour of keeping the data
usable for genuine validation.

Commit authorship was corrected to the GitHub noreply address after an earlier
commit used a personal address from the session environment.

**Credentials.** The collector reads `NET_SSH_USERNAME`, `NET_SSH_PASSWORD`,
`NET_SSH_ENABLE` or `NET_SSH_KEY_FILE`, or prompts. There is deliberately no
`--password` flag: arguments appear in `ps` output and shell history. The password
and enable secret are excluded from the target's repr so they cannot surface in a
traceback, and driver error text is scrubbed before printing. Tests assert all of
this. `captures/` is gitignored.

**Pushing.** `git push` from the agent session is blocked by the sandbox, which
flags the topology data as exfiltration. Pushes are done manually.

---

## 9. Open questions and blockers

1. **Real `detail` output** for both protocols from the core switch. This
   validates the two unverified Cisco parsers and restores chassis-MAC identity,
   fixing the truncation weakness below. Highest-value ask.
2. **Name truncation.** Two devices in the capture are exactly 20 characters and
   are almost certainly truncated, with no CDP counterpart to reconstruct from.
   Any hostname of 20 or more characters without a dot before the cut will fail to
   merge across protocols.
3. **Real Comware and Aruba captures.** Until these arrive, neither parser can be
   described as supported.
4. **Switch access for the collector test**: management IP, username, auth method,
   whether enable is required, which commands are permitted, and whether this host
   can reach the management VLAN or needs a jump host.
5. **`Fif` → `FiftyGigE`** is inferred from platform numbering, not confirmed.
   Harmless for deduplication since both protocols abbreviate it identically.
6. **Link aggregation and stacking** are deferred by agreement, and will need
   handling eventually.
7. **Polling interval.** The meeting suggested 5 minutes; 400 SSH sessions will
   not reliably finish in that window. Proposal: 15 minutes plus on-demand refresh.

---

## 10. Next steps

1. Test the collector against one authorised switch.
   Then replace `tests/synthetic/LH-00-DIS__cisco_session.txt` with a real
   distribution-switch capture and re-run `tests/test_multiswitch.py`.
2. Validate the `detail` parsers once real output is available.
3. Collect from the distribution switches, which turns every link from
   single-ended to confirmed at both ends.
4. Persistence: SQLite with `first_seen` / `last_seen`, and change detection.
5. Reachability probing, measured separately from topology.
6. Scheduled and on-demand polling.
7. Dashboard proper; then alerts, technician routing, authentication.

---

## Appendix — commands

```bash
# parse a directory of captures and print the topology
python3 src/main.py tests/fixtures

# core-centric tree plus a self-contained SVG map
python3 src/mapview.py tests/fixtures map.html

# collect from one switch (credentials from the environment, never arguments)
export NET_SSH_USERNAME=<user>
read -rsp "password: " NET_SSH_PASSWORD && echo && export NET_SSH_PASSWORD
python3 src/collector.py <host> -o captures
unset NET_SSH_PASSWORD

# tests
python3 -m pytest tests/ -q
```

Exit codes from the collector: `2` unreachable, `3` authentication,
`4` missing credentials.
