# Beacon Chain Spec Explorer — Roadmap

## Navigation & discovery
- [x] URL hash routing (`#/BeaconState/electra`) for shareable links and native browser back/forward
- [x] Keyboard navigation — arrow keys through item list, Enter to select, Escape to go back
- [x] Related items cluster — group by call graph proximity instead of alphabetical listing
- [x] Full on_block call graph with current item highlighted and path expanded
- [x] Entry Points sidebar for main system roots

## Diff view
- [ ] Word-level diff for Container fields (highlight exactly which fields added/removed/changed)
- [ ] Full evolution timeline — show an item's complete changelog across all forks in one scroll

## Search
- [ ] Fuzzy search (typing "procblk" finds `process_block`)
- [ ] Search inside code bodies — "what function mentions `effective_balance`?"
- [ ] Search constants by value, not just name

## Visualization
- [ ] Dependency graph view — interactive node graph of an item's reference tree
- [ ] Fork comparison matrix — pick two forks, see everything that changed between them

## Content
- [x] Surface EIP tags as a filterable dimension (data already exists in `[New in Electra:EIP7251]` comments)
- [x] Pull prose explanations from spec markdown (text between headings) as inline documentation
- [x] Link to EIP pages for each tagged change

## Developer workflow
- [x] Custom fork support with auto-detected ordering from configs/mainnet.yaml
- [ ] "What's new in fork X" summary page — one click to see all additions and modifications
- [ ] Export an item's full history as markdown (for research posts / ethresear.ch)
- [ ] Watch mode in `build.py` — rebuild on file change when editing a custom fork
