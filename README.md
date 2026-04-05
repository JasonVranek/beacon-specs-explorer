# Beacon Chain Spec Explorer

An interactive reference for the [Ethereum consensus specs](https://github.com/ethereum/consensus-specs). Browse every type, function, and constant across all hardforks with cross-references, diff views, and SSZ test fixture examples.

The spec repo uses a diff-based structure where each hardfork only contains changes from the prior fork. This tool composes those diffs into a navigable index where you can look up any definition at any fork, see what changed between forks, and follow references between types and functions.

## Build

```bash
python3 build.py                                               # fetch from GitHub
python3 build.py --repo-dir ~/consensus-specs                  # from a local clone
python3 build.py --repo-dir ~/consensus-specs --skip-examples  # skip test fixtures (faster)
```

Open `index.html` in a browser. Python 3.8+, no dependencies.

## How it works

Four scripts run in sequence. `build.py` orchestrates them.

**extract.py** parses every spec markdown file across all hardforks and produces a JSON index of every type, function, constant, and type alias. Each entry includes the full code, source file, line number, and a GitHub permalink. Fork ordering is auto-detected from `configs/mainnet.yaml`.

**enrich.py** layers on structural annotations: container fields with types and fork comments, function signatures with params and return types, docstrings, a cross-reference graph, and an EIP index extracted from inline `[New in Fork:EIPNNNN]` comments.

**fetch_examples.py** pulls SSZ test fixture YAML from [consensus-spec-tests](https://github.com/ethereum/consensus-spec-tests) for every type at every fork. These appear as expandable examples in the UI.

**build.py** runs all three and embeds the results into a self-contained `index.html`.

## Call graph

Every function shows a call graph rooted at `on_block` — the main entry point when the consensus layer receives a new block. This covers block processing (`state_transition`), slot/epoch transitions (`process_slots`, `process_epoch`), and fork choice updates.

The current function is highlighted in blue. The path from the root down to it is expanded and shown in white. Everything else is collapsed in gray but expandable. This lets you see exactly where any function fits in the overall execution flow and what runs before and after it.

The sidebar has Entry Points shortcuts for the main system roots: Block Processing, Epoch Processing, Fork Choice, Beacon State, and Block Body.

## EIP filtering

The sidebar shows all EIPs referenced in the spec via `[New in Fork:EIPNNNN]` and `[Modified in Fork:EIPNNNN]` annotations. Click an EIP pill to filter the item list to only definitions affected by that EIP. Multiple EIPs can be selected (AND logic). Each item's detail view shows which EIPs contributed to it with links to eips.ethereum.org.

## Prose documentation

Text between a heading and its code block is captured as inline documentation. This includes notes, warnings, and context from the spec authors. Prose appears as a cyan-bordered info box above the code definition, with clickable cross-references to types and constants.

## Fuzzy search

Press `/` to focus the search bar. Type any subsequence of characters to filter — `procblk` finds `process_block`, `gbr` finds `get_base_reward`, `sttr` finds `state_transition`. Matched characters are highlighted in blue. Results are ranked by match quality: exact substrings first, then consecutive runs, word boundary matches, and shorter names.

Press `Esc` to leave the search bar and arrow through results. Press `Esc` again to clear the search. Works across items, constants, and type aliases.

## What you can do

- Look up any type or function and see its definition at each fork
- Switch between forks to see how a definition evolved, with a diff toggle
- See where any function fits in the full execution flow via the call graph
- Filter by EIP to see everything a specific proposal touches
- Fuzzy search across all items — type abbreviations like `procblk` or `gbr`
- Click any type in a fields table, params table, or code block to navigate to it
- Click constants to see their values across forks
- Click type aliases (Slot, Root, Gwei) to see their SSZ type and an example value
- View SSZ test fixture examples matched to the selected fork
- Read spec prose documentation inline with the code
- Filter by domain (beacon state, block processing, fork choice, etc.), kind, or fork
- Navigate with keyboard: arrows or `j`/`k` through items, `h` to go back, `/` to search, `Esc` to defocus/clear
- Share links to specific items via URL hash (e.g. `#/BeaconState/electra`)

## Custom forks

Clone the consensus-specs repo, add your fork as a new directory under `specs/`, and rebuild.

```bash
git clone https://github.com/ethereum/consensus-specs.git
cd consensus-specs
mkdir -p specs/myfork
```

Write `specs/myfork/beacon-chain.md` following the existing diff pattern. The spec format uses markdown headings with backtick-quoted names and Python code blocks. The key sections are:

**Constants** go in a "Constants" > "Misc" section as markdown tables with Name and Value columns.

**New containers** go under "Containers" > "New containers", each with a heading like `#### MyNewType` and a Python code block defining the class.

**Modified containers** go under "Modified containers" with a heading like `#### BeaconState`. The code block must include ALL fields from the prior fork plus any new fields marked with `# [New in MyFork]` comments. The explorer takes the latest definition per fork and does not merge diffs.

**Functions** go under the appropriate section ("Helpers", "Beacon chain state transition function", etc.) with a heading like `#### my_function` and a Python code block defining the function.

See any existing fork directory (e.g. `specs/electra/beacon-chain.md`) for the exact format.

Register a fork version in `configs/mainnet.yaml` to control ordering (the hex value determines position relative to other forks):

```yaml
MYFORK_FORK_VERSION: 0x09000000
MYFORK_FORK_EPOCH: 18446744073709551615
```

Build and open:

```bash
python3 /path/to/beacon-specs/build.py --repo-dir . --skip-examples --out-dir ./explorer
open explorer/index.html
```

Your fork appears as a new tab. New types, modified containers, new functions, and constants all show up with cross-references, field tables, call graphs, and diff views against the prior fork.

## Programmatic access

The JSON is structured for agent and script access:

```python
data["items"]["BeaconState"]["forks"]["electra"]["fields"]       # fields at a fork
data["items"]["process_attestation"]["forks"]["altair"]["params"] # function signature
data["_references"]["BeaconState"]                                # what depends on this
data["_field_index"]["BeaconState.slot"]["forks"]                 # field type across forks
data["constants"]["MAX_EFFECTIVE_BALANCE"]                        # constant values
data["_eip_index"]["7251"]                                       # items affected by EIP-7251
```

## Files

| File | Description |
|------|-------------|
| `index.html` | Self-contained UI with embedded data |
| `beacon_specs_index.json` | Extracted and enriched spec data |
| `examples.json` | SSZ test fixtures per type per fork |
| `examples.js` | Same data as JS for offline loading |
| `extract.py` | Spec markdown parser |
| `enrich.py` | Structural annotation enrichment |
| `fetch_examples.py` | Test fixture fetcher |
| `build.py` | Pipeline orchestrator |
