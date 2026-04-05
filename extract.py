#!/usr/bin/env python3
"""
Deterministic extraction of all types, functions, constants, and custom types
from the ethereum/consensus-specs repository.

Outputs structured JSON that can drive a UI or be used for research lookups.

Usage:
    python3 extract.py [--repo-dir PATH] [--output PATH] [--branch BRANCH]

If --repo-dir is not given, fetches raw files from GitHub API.
Output defaults to ./beacon_specs_index.json
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────

GITHUB_RAW = "https://raw.githubusercontent.com/ethereum/consensus-specs/{branch}/specs"
GITHUB_WEB = "https://github.com/ethereum/consensus-specs/blob/{branch}/specs"

# Fallback fork ordering (used when auto-detection isn't possible)
DEFAULT_FORK_ORDER = [
    "phase0", "altair", "bellatrix", "capella",
    "deneb", "electra", "fulu", "gloas", "heze",
]

DEFAULT_FEATURE_PREFIXES = ["eip6914", "eip7928", "eip8025"]


def detect_forks_local(repo_dir: str) -> tuple:
    """Auto-detect fork order and features from a local repo clone.

    Reads configs/mainnet.yaml for fork version ordering, and
    lists specs/_features/ for feature prefixes. Any spec dir
    not in the config is appended at the end (for custom forks).

    Returns (fork_order: list[str], features: list[str])
    """
    specs_dir = Path(repo_dir) / "specs"
    config_path = Path(repo_dir) / "configs" / "mainnet.yaml"

    # Parse fork ordering from config
    fork_versions = {}  # fork_name -> version_hex
    if config_path.exists():
        for line in config_path.read_text().splitlines():
            # Match: ALTAIR_FORK_VERSION: 0x01000000
            m = re.match(r"([A-Z_]+)_FORK_VERSION:\s*(0x[0-9a-fA-F]+)", line.strip())
            if m:
                name = m.group(1).lower()
                version = int(m.group(2), 16)
                fork_versions[name] = version

    # Sort by version number
    ordered_from_config = sorted(fork_versions.keys(), key=lambda k: fork_versions[k])

    # Detect features
    features_dir = specs_dir / "_features"
    features = []
    if features_dir.is_dir():
        features = sorted([d.name for d in features_dir.iterdir() if d.is_dir()])

    # Detect all spec dirs (exclude _features and hidden dirs)
    all_spec_dirs = set()
    if specs_dir.is_dir():
        for d in specs_dir.iterdir():
            if d.is_dir() and not d.name.startswith(("_", ".")):
                all_spec_dirs.add(d.name)

    # Build fork order: config-ordered forks first, then phase0 prepended if missing,
    # then any dirs not in config appended at end (custom forks)
    fork_order = []
    # phase0 doesn't have a FORK_VERSION in config — it's the genesis state
    if "phase0" in all_spec_dirs:
        fork_order.append("phase0")

    for fork in ordered_from_config:
        if fork in all_spec_dirs and fork not in fork_order:
            fork_order.append(fork)

    # Append any remaining dirs not in config (custom/experimental forks)
    for d in sorted(all_spec_dirs):
        if d not in fork_order:
            fork_order.append(d)

    return fork_order, features


def detect_forks_github(branch: str = "master") -> tuple:
    """Auto-detect fork order and features from GitHub API.

    Returns (fork_order: list[str], features: list[str])
    """
    import urllib.request
    import urllib.error

    # Fetch config
    fork_versions = {}
    try:
        config_url = f"https://raw.githubusercontent.com/ethereum/consensus-specs/{branch}/configs/mainnet.yaml"
        req = urllib.request.Request(config_url, headers={"User-Agent": "beacon-spec-extractor/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            for line in resp.read().decode().splitlines():
                m = re.match(r"([A-Z_]+)_FORK_VERSION:\s*(0x[0-9a-fA-F]+)", line.strip())
                if m:
                    fork_versions[m.group(1).lower()] = int(m.group(2), 16)
    except Exception:
        pass

    ordered_from_config = sorted(fork_versions.keys(), key=lambda k: fork_versions[k])

    # Fetch specs dir listing
    all_spec_dirs = set()
    features = []
    try:
        api_url = f"https://api.github.com/repos/ethereum/consensus-specs/contents/specs?ref={branch}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "beacon-spec-extractor/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            items = json.loads(resp.read().decode())
            for item in items:
                if item["type"] == "dir":
                    if item["name"] == "_features":
                        # Fetch feature subdirs
                        feat_url = f"https://api.github.com/repos/ethereum/consensus-specs/contents/specs/_features?ref={branch}"
                        freq = urllib.request.Request(feat_url, headers={"User-Agent": "beacon-spec-extractor/1.0"})
                        with urllib.request.urlopen(freq, timeout=30) as fresp:
                            fitems = json.loads(fresp.read().decode())
                            features = sorted([f["name"] for f in fitems if f["type"] == "dir"])
                    elif not item["name"].startswith(("_", ".")):
                        all_spec_dirs.add(item["name"])
    except Exception:
        pass

    # Build fork order
    fork_order = []
    if "phase0" in all_spec_dirs:
        fork_order.append("phase0")
    for fork in ordered_from_config:
        if fork in all_spec_dirs and fork not in fork_order:
            fork_order.append(fork)
    for d in sorted(all_spec_dirs):
        if d not in fork_order:
            fork_order.append(d)

    return fork_order, features

# Spec files we care about in each fork dir (and light-client subdir)
SPEC_FILES = [
    "beacon-chain.md",
    "fork-choice.md",
    "validator.md",
    "p2p-interface.md",
    "fork.md",
    "deposit-contract.md",
    "weak-subjectivity.md",
    "bls.md",
    "polynomial-commitments.md",
    "das-core.md",
    "polynomial-commitments-sampling.md",
    "builder.md",
    "inclusion-list.md",
    # light-client sub-files
    "light-client/sync-protocol.md",
    "light-client/light-client.md",
    "light-client/full-node.md",
    "light-client/p2p-interface.md",
    "light-client/fork.md",
]

# Domain classification rules: map (section_context, file) -> domain
# We classify based on the ## or ### heading the item appears under
DOMAIN_RULES = {
    # By file
    "fork-choice.md": "fork-choice",
    "validator.md": "validator-duties",
    "p2p-interface.md": "networking",
    "fork.md": "fork-transitions",
    "deposit-contract.md": "deposit-contract",
    "weak-subjectivity.md": "weak-subjectivity",
    "bls.md": "cryptography",
    "polynomial-commitments.md": "cryptography",
    "das-core.md": "data-availability",
    "polynomial-commitments-sampling.md": "data-availability",
    "builder.md": "builder-pbs",
    "inclusion-list.md": "builder-pbs",
    "light-client/sync-protocol.md": "light-client",
    "light-client/light-client.md": "light-client",
    "light-client/full-node.md": "light-client",
    "light-client/p2p-interface.md": "light-client",
    "light-client/fork.md": "light-client",
}

# For beacon-chain.md, classify by section heading context
BEACON_CHAIN_SECTION_DOMAINS = {
    "containers": "beacon-state",
    "beacon state": "beacon-state",
    "beacon blocks": "block-processing",
    "beacon operations": "block-processing",
    "signed envelopes": "block-processing",
    "dataclasses": "beacon-state",
    "types": "custom-types",
    "constants": "constants",
    "preset": "constants",
    "configuration": "constants",
    "math": "helpers",
    "crypto": "cryptography",
    "predicates": "helpers",
    "misc": "helpers",
    "beacon state accessors": "helpers",
    "beacon state mutators": "helpers",
    "epoch processing": "epoch-processing",
    "block processing": "block-processing",
    "justification and finalization": "epoch-processing",
    "rewards and penalties": "epoch-processing",
    "slashings": "epoch-processing",
    "sync committee": "epoch-processing",
    "sync aggregate processing": "block-processing",
    "inactivity scores": "epoch-processing",
    "genesis": "genesis",
    "beacon chain state transition function": "block-processing",
    "execution engine": "block-processing",
    "execution proof": "block-processing",
    "execution payload processing": "block-processing",
    "execution": "block-processing",
    "withdrawals": "block-processing",
    "withdrawals processing": "block-processing",
    "pending deposits processing": "block-processing",
    "modified containers": "beacon-state",
    "new containers": "beacon-state",
    "modified dataclasses": "beacon-state",
    "new dataclasses": "beacon-state",
}


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class Definition:
    """A single definition of a type/function/constant at a specific fork."""
    fork: str
    file: str                        # e.g. "beacon-chain.md"
    line_number: int                 # 1-indexed line in the source file
    kind: str                        # "class", "def", "type_alias", "constant", "dataclass"
    is_new: bool                     # True if introduced in this fork
    is_modified: bool                # True if modified from prior fork
    code: str                        # Full code block content
    inline_comments: list = field(default_factory=list)  # [New in X], [Modified in X] comments
    section_path: list = field(default_factory=list)      # Heading hierarchy above this item
    prose: str = ""                                       # Explanatory text between heading and code block
    github_url: str = ""

    def to_dict(self):
        d = asdict(self)
        if not d.get("prose"):
            d.pop("prose", None)
        return d


@dataclass
class SpecItem:
    """An item (type, function, constant) tracked across forks."""
    name: str
    kind: str                        # "class", "def", "type_alias", "constant"
    domain: str                      # functional domain
    introduced: str                  # fork name
    forks: dict = field(default_factory=dict)  # fork -> Definition

    def modified_in(self) -> list:
        return [f for f, d in self.forks.items() if d.is_modified and f != self.introduced]

    def to_dict(self):
        d = {
            "name": self.name,
            "kind": self.kind,
            "domain": self.domain,
            "introduced": self.introduced,
            "modified_in": self.modified_in(),
            "forks": {f: defn.to_dict() for f, defn in self.forks.items()},
        }
        return d


@dataclass
class ConstantEntry:
    """A constant/preset/config value at a specific fork."""
    name: str
    value: str
    description: str
    section: str          # e.g. "Misc", "Gwei values"
    category: str         # "constant", "preset", "configuration"
    fork: str
    file: str
    line_number: int
    github_url: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class TypeAlias:
    """A custom type alias (from the Types table)."""
    name: str
    ssz_equivalent: str
    description: str
    fork: str
    file: str
    line_number: int
    github_url: str = ""

    def to_dict(self):
        return asdict(self)


# ── File fetching ──────────────────────────────────────────────────────────

def fetch_file_github(fork: str, filepath: str, branch: str = "master") -> Optional[str]:
    """Fetch a raw spec file from GitHub."""
    url = f"{GITHUB_RAW.format(branch=branch)}/{fork}/{filepath}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "beacon-spec-extractor/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except urllib.error.URLError:
        return None


def fetch_file_local(repo_dir: str, fork: str, filepath: str) -> Optional[str]:
    """Read a spec file from a local clone."""
    path = Path(repo_dir) / "specs" / fork / filepath
    if path.exists():
        return path.read_text()
    return None


def make_github_url(fork: str, filepath: str, anchor: str, branch: str = "master") -> str:
    """Generate a GitHub permalink to a specific heading."""
    base = GITHUB_WEB.format(branch=branch)
    return f"{base}/{fork}/{filepath}#{anchor}"


def name_to_anchor(name: str) -> str:
    """Convert a spec item name to a GitHub markdown anchor.

    GitHub's anchor generation:
    - Strips backticks
    - Lowercases
    - Replaces spaces with hyphens
    - Strips non-alphanumeric except hyphens and underscores

    For headings like `#### \`BeaconState\`` -> #beaconstate
    For headings like `#### Modified \`process_attestation\`` -> #modified-process_attestation
    """
    anchor = name.lower()
    anchor = anchor.replace("`", "")
    anchor = anchor.replace(" ", "-")
    # Keep alphanumeric, hyphens, underscores
    anchor = re.sub(r"[^a-z0-9_\-]", "", anchor)
    return anchor


# ── Parsing ────────────────────────────────────────────────────────────────

def classify_domain(file: str, section_path: list) -> str:
    """Determine functional domain from file and section context."""
    # Check file-level rules first
    basename = file
    if basename in DOMAIN_RULES:
        return DOMAIN_RULES[basename]

    # For beacon-chain.md, use section hierarchy
    if "beacon-chain" in basename:
        # Walk section path from most specific to most general
        for heading in reversed(section_path):
            heading_lower = heading.lower().strip()
            # Strip "modified" / "new" prefixes for matching
            cleaned = re.sub(r"^(modified|new)\s+", "", heading_lower)
            if cleaned in BEACON_CHAIN_SECTION_DOMAINS:
                return BEACON_CHAIN_SECTION_DOMAINS[cleaned]
            if heading_lower in BEACON_CHAIN_SECTION_DOMAINS:
                return BEACON_CHAIN_SECTION_DOMAINS[heading_lower]

    # Fallback: check the current heading text itself (for ## level headings
    # where the function is directly under the heading)
    for heading in section_path:
        if "state transition" in heading.lower():
            return "block-processing"
        if "genesis" in heading.lower():
            return "genesis"

    return "other"


def detect_modification_status(heading_text: str, section_path: list, code: str, fork: str) -> tuple:
    """Detect if an item is new or modified based on heading and inline comments.

    Returns (is_new, is_modified).
    """
    # Check heading for "Modified" or "New" prefix
    heading_lower = heading_text.lower()
    has_modified_heading = "modified" in heading_lower

    # Check parent section headings
    for s in section_path:
        sl = s.lower()
        if "new containers" in sl or "new dataclasses" in sl:
            return (True, False)
        if "modified containers" in sl or "modified dataclasses" in sl:
            return (False, True)

    # Check inline comments in code
    new_pattern = re.compile(r"\[New in (\w+)\]", re.IGNORECASE)
    mod_pattern = re.compile(r"\[Modified in (\w+)\]", re.IGNORECASE)

    has_new = bool(new_pattern.search(code))
    has_modified = bool(mod_pattern.search(code))

    if has_modified_heading:
        return (False, True)

    # For the first fork (phase0), everything is "new"
    if fork == "phase0":
        return (True, False)

    # If neither label found, it's a new addition in this fork
    if not has_new and not has_modified:
        return (True, False)

    return (has_new, has_modified)


def extract_inline_comments(code: str) -> list:
    """Extract [New in X] and [Modified in X] comments from code."""
    pattern = re.compile(r"#\s*\[(New|Modified) in (\w+)\]")
    return [m.group(0) for m in pattern.finditer(code)]


def parse_spec_file(content: str, fork: str, filepath: str, branch: str = "master"):
    """Parse a single spec markdown file and extract all definitions.

    Yields (name, Definition) tuples.
    Also yields ("__constant__", ConstantEntry) for table-based constants.
    Also yields ("__type_alias__", TypeAlias) for custom type aliases.
    """
    lines = content.split("\n")
    total_lines = len(lines)

    # Track heading hierarchy
    section_stack = []  # [(level, heading_text)]

    # Identify #### headings (item-level) and their code blocks
    i = 0
    while i < total_lines:
        line = lines[i]

        # Track heading hierarchy
        heading_match = re.match(r"^(#{2,5})\s+(.+)", line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()

            # Pop headings at same or deeper level
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            section_stack.append((level, heading_text))

            # Check for ##, ###, ####, or ##### headings that may contain definitions
            if level in (2, 3, 4, 5):
                # Extract name from backticks: #### `BeaconState` or #### Modified `process_attestation`
                name_match = re.search(r"`([^`]+)`", heading_text)

                # For headings without backtick names (e.g. "##### Attestations", "#### Block header",
                # "### Block processing"), extract the function/class name from the code block.
                extract_name_from_code = not name_match

                if name_match or extract_name_from_code:
                    item_name = name_match.group(1) if name_match else None
                    heading_line = i + 1  # 1-indexed
                    section_path = [s[1] for s in section_stack]  # include current heading for domain classification

                    # Capture prose text between heading and first code block
                    prose_lines = []
                    j = i + 1
                    while j < total_lines:
                        pline = lines[j].strip()
                        if pline.startswith("```"):
                            break  # hit code block
                        if re.match(r"^#{2,5}\s", lines[j]):
                            break  # hit next heading
                        if pline and not pline.startswith("|"):
                            prose_lines.append(pline)
                        elif not pline and prose_lines:
                            prose_lines.append("")  # preserve paragraph breaks
                        j += 1
                    # Trim trailing blanks and join
                    while prose_lines and not prose_lines[-1]:
                        prose_lines.pop()
                    # Filter out lines that are only EIP markers (e.g. "*[New in Deneb:EIP4844]*")
                    prose_lines = [
                        pl for pl in prose_lines
                        if not re.match(r"^\*?\[(?:New|Modified) in \w+:EIP\d+\]\*?$", pl)
                    ]
                    # Trim again after filtering
                    while prose_lines and not prose_lines[-1]:
                        prose_lines.pop()
                    item_prose = "\n".join(prose_lines) if prose_lines else ""

                    # Look for ALL code blocks under this heading until the next heading
                    # (##### plain-text headings can have multiple code blocks)
                    j = i + 1
                    found_blocks = []  # [(code_block, code_start_line)]
                    # Only collect the FIRST code block directly after this heading.
                    # For ### and #### plain-text headings, we don't want to greedily
                    # consume all code blocks in nested sub-sections.
                    # For backtick-named headings, there's typically exactly one code block.
                    stop_at_level = level  # stop at same or higher level heading
                    while j < total_lines:
                        if lines[j].strip().startswith("```python"):
                            code_start_line = j + 1
                            k = j + 1
                            code_lines = []
                            while k < total_lines and not lines[k].strip().startswith("```"):
                                code_lines.append(lines[k])
                                k += 1
                            found_blocks.append(("\n".join(code_lines), code_start_line))
                            j = k + 1
                            # For plain-text headings extracting from code, collect all
                            # code blocks until next heading at same level or higher.
                            # For backtick-named headings, one block is enough.
                            if name_match:
                                break
                        elif re.match(r"^#{2," + str(stop_at_level) + r"}\s", lines[j]):
                            # Hit a heading at same or higher level - stop
                            break
                        elif re.match(r"^#{" + str(stop_at_level + 1) + r",}\s", lines[j]):
                            # Hit a sub-heading - stop collecting for this heading
                            # (that sub-heading will get its own pass)
                            break
                        else:
                            j += 1

                    # Process found code blocks
                    for code_block, code_start_line in found_blocks:
                        if not code_block.strip():
                            continue

                        # Determine kind and extract name from code if needed
                        code_stripped = code_block.strip()
                        if code_stripped.startswith("class "):
                            kind = "class"
                            if not item_name:
                                cm = re.match(r"class\s+(\w+)", code_stripped)
                                if cm:
                                    item_name = cm.group(1)
                        elif code_stripped.startswith("def "):
                            kind = "def"
                            if not item_name:
                                dm = re.match(r"def\s+(\w+)", code_stripped)
                                if dm:
                                    item_name = dm.group(1)
                        elif code_stripped.startswith("@dataclass"):
                            kind = "dataclass"
                            if not item_name:
                                dm = re.search(r"class\s+(\w+)", code_stripped)
                                if dm:
                                    item_name = dm.group(1)
                        else:
                            kind = "other"

                        if not item_name:
                            continue

                        # For plain-text ##### headings with multiple code blocks,
                        # use the function/class name for each block
                        effective_name = item_name
                        if extract_name_from_code and len(found_blocks) > 1:
                            # Re-extract name from this specific code block
                            if code_stripped.startswith("def "):
                                dm = re.match(r"def\s+(\w+)", code_stripped)
                                if dm:
                                    effective_name = dm.group(1)
                            elif code_stripped.startswith("class "):
                                cm = re.match(r"class\s+(\w+)", code_stripped)
                                if cm:
                                    effective_name = cm.group(1)

                        is_new, is_modified = detect_modification_status(
                            heading_text, section_path, code_block, fork
                        )
                        inline_comments = extract_inline_comments(code_block)
                        domain = classify_domain(filepath, section_path)

                        # Build anchor from the full heading text
                        anchor = name_to_anchor(heading_text)

                        defn = Definition(
                            fork=fork,
                            file=filepath,
                            line_number=heading_line,
                            kind=kind,
                            is_new=is_new,
                            is_modified=is_modified,
                            code=code_stripped,
                            inline_comments=inline_comments,
                            section_path=section_path,
                            prose=item_prose,
                            github_url=make_github_url(fork, filepath, anchor, branch),
                        )
                        yield (effective_name, defn)

                        # Reset item_name for next block if extracting from code
                        if extract_name_from_code:
                            item_name = None

            # Check for ## or ### sections that contain tables (constants, presets, types)
            if level in (2, 3):
                section_lower = heading_text.lower().strip()

                # Check if this is a "Types" section with a table of type aliases
                if section_lower == "types" and "beacon-chain" in filepath:
                    # Parse the table that follows
                    j = i + 1
                    while j < total_lines:
                        tline = lines[j].strip()
                        if tline.startswith("|") and "---" not in tline and "Name" not in tline:
                            cells = [c.strip().strip("`") for c in tline.split("|")[1:-1]]
                            if len(cells) >= 3:
                                alias = TypeAlias(
                                    name=cells[0],
                                    ssz_equivalent=cells[1],
                                    description=cells[2],
                                    fork=fork,
                                    file=filepath,
                                    line_number=j + 1,
                                    github_url=make_github_url(fork, filepath, name_to_anchor(heading_text), branch),
                                )
                                yield ("__type_alias__", alias)
                        elif tline and not tline.startswith("|") and not tline.startswith("*") and not tline == "":
                            if re.match(r"^#{2,4}\s", tline):
                                break
                        j += 1

                # Check for constant/preset/config table sections
                parent_sections = [s[1].lower() for s in section_stack]
                is_constant_section = any(
                    p in ("constants", "preset", "configuration")
                    for p in parent_sections
                )

                if is_constant_section and level == 3:
                    category = "constant"
                    for ps in parent_sections:
                        if ps == "preset":
                            category = "preset"
                            break
                        elif ps == "configuration":
                            category = "configuration"
                            break
                        elif ps == "constants":
                            category = "constant"
                            break

                    # Parse table rows
                    j = i + 1
                    while j < total_lines:
                        tline = lines[j].strip()
                        if tline.startswith("|") and "---" not in tline and "Name" not in tline:
                            cells = [c.strip().strip("`") for c in tline.split("|")[1:-1]]
                            if len(cells) >= 2 and cells[0]:
                                desc = cells[2] if len(cells) >= 3 else ""
                                entry = ConstantEntry(
                                    name=cells[0],
                                    value=cells[1],
                                    description=desc,
                                    section=heading_text,
                                    category=category,
                                    fork=fork,
                                    file=filepath,
                                    line_number=j + 1,
                                    github_url=make_github_url(fork, filepath, name_to_anchor(heading_text), branch),
                                )
                                yield ("__constant__", entry)
                        elif re.match(r"^#{2,3}\s", tline):
                            break
                        j += 1

        i += 1


# ── Main extraction ────────────────────────────────────────────────────────

def extract_all(repo_dir: Optional[str] = None, branch: str = "master"):
    """Extract all definitions from all forks.

    Auto-detects fork ordering and features from the repo.

    Returns:
        items: dict[name, SpecItem] — all types and functions
        constants: dict[name, list[ConstantEntry]] — constants across forks
        type_aliases: dict[name, list[TypeAlias]] — custom type aliases
        files_processed: list[dict] — metadata about files processed
        fork_order: list[str] — detected fork ordering
        features: list[str] — detected feature prefixes
    """
    # Auto-detect fork order and features
    if repo_dir:
        fork_order, features = detect_forks_local(repo_dir)
    else:
        fork_order, features = detect_forks_github(branch)

    # Fall back to defaults if detection failed
    if not fork_order:
        print("  Warning: fork auto-detection failed, using defaults", file=sys.stderr)
        fork_order = list(DEFAULT_FORK_ORDER)
        features = list(DEFAULT_FEATURE_PREFIXES)

    print(f"  Detected forks: {fork_order}", file=sys.stderr)
    if features:
        print(f"  Detected features: {features}", file=sys.stderr)

    items = {}        # name -> SpecItem
    constants = {}    # name -> [ConstantEntry per fork]
    type_aliases = {} # name -> [TypeAlias per fork]
    files_processed = []

    # Process all forks + features
    all_dirs = [(f, f) for f in fork_order] + [
        (f"_features/{feat}", feat) for feat in features
    ]

    for dir_path, fork_label in all_dirs:
        for filepath in SPEC_FILES:
            # Fetch content
            if repo_dir:
                content = fetch_file_local(repo_dir, dir_path, filepath)
            else:
                content = fetch_file_github(dir_path, filepath, branch)

            if content is None:
                continue

            files_processed.append({
                "fork": fork_label,
                "dir": dir_path,
                "file": filepath,
                "lines": content.count("\n") + 1,
            })

            print(f"  Parsing {dir_path}/{filepath} ({content.count(chr(10))+1} lines)", file=sys.stderr)

            for marker, result in parse_spec_file(content, fork_label, filepath, branch):
                if marker == "__constant__":
                    if result.name not in constants:
                        constants[result.name] = []
                    constants[result.name].append(result)

                elif marker == "__type_alias__":
                    if result.name not in type_aliases:
                        type_aliases[result.name] = []
                    type_aliases[result.name].append(result)

                else:
                    name = marker
                    defn = result

                    domain = classify_domain(filepath, defn.section_path)

                    if name not in items:
                        items[name] = SpecItem(
                            name=name,
                            kind=defn.kind,
                            domain=domain,
                            introduced=fork_label,
                            forks={},
                        )

                    item = items[name]
                    item.forks[fork_label] = defn

                    # Update domain if it was "other" and we now have a better one
                    if item.domain == "other" and domain != "other":
                        item.domain = domain

    return items, constants, type_aliases, files_processed, fork_order, features


def build_output(items, constants, type_aliases, files_processed, fork_order, features, branch="master"):
    """Build the final JSON output structure."""

    # Build domain summary
    domain_items = {}
    for name, item in items.items():
        d = item.domain
        if d not in domain_items:
            domain_items[d] = {"classes": [], "functions": [], "other": []}
        bucket = "classes" if item.kind in ("class", "dataclass") else "functions" if item.kind == "def" else "other"
        domain_items[d][bucket].append(name)

    # Build fork summary
    fork_summary = {}
    for fork in fork_order + features:
        new_items = [n for n, it in items.items() if it.introduced == fork]
        modified_items = [n for n, it in items.items()
                         if fork in it.forks and fork != it.introduced]
        new_constants = [n for n, cs in constants.items()
                        if any(c.fork == fork for c in cs)
                        and not any(c.fork in fork_order[:fork_order.index(fork)] if fork in fork_order else False for c in cs)]
        if new_items or modified_items:
            fork_summary[fork] = {
                "new": sorted(new_items),
                "modified": sorted(modified_items),
                "total_definitions": len(new_items) + len(modified_items),
            }

    output = {
        "_meta": {
            "generated_by": "beacon-specs/extract.py",
            "repo": "ethereum/consensus-specs",
            "branch": branch,
            "fork_order": fork_order,
            "features": features,
            "files_processed": files_processed,
            "total_items": len(items),
            "total_constants": len(constants),
            "total_type_aliases": len(type_aliases),
        },
        "domains": {
            domain: {
                "classes": sorted(info["classes"]),
                "functions": sorted(info["functions"]),
                "other": sorted(info["other"]),
            }
            for domain, info in sorted(domain_items.items())
        },
        "fork_summary": fork_summary,
        "items": {
            name: item.to_dict()
            for name, item in sorted(items.items())
        },
        "constants": {
            name: [c.to_dict() for c in entries]
            for name, entries in sorted(constants.items())
        },
        "type_aliases": {
            name: [a.to_dict() for a in entries]
            for name, entries in sorted(type_aliases.items())
        },
    }

    return output


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract beacon chain spec definitions")
    parser.add_argument("--repo-dir", help="Path to local consensus-specs clone (optional)")
    parser.add_argument("--output", default="./beacon_specs_index.json", help="Output JSON path")
    parser.add_argument("--branch", default="master", help="Git branch (default: master)")
    args = parser.parse_args()

    print("Extracting beacon chain spec definitions...", file=sys.stderr)
    print(f"Source: {'local ' + args.repo_dir if args.repo_dir else 'GitHub raw (' + args.branch + ')'}", file=sys.stderr)

    items, constants, type_aliases, files_processed, fork_order, features = extract_all(
        repo_dir=args.repo_dir,
        branch=args.branch,
    )

    print(f"\nExtracted:", file=sys.stderr)
    print(f"  {len(items)} types/functions", file=sys.stderr)
    print(f"  {len(constants)} constants/presets/config values", file=sys.stderr)
    print(f"  {len(type_aliases)} custom type aliases", file=sys.stderr)
    print(f"  from {len(files_processed)} files", file=sys.stderr)

    output = build_output(items, constants, type_aliases, files_processed, fork_order, features, args.branch)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nWritten to {args.output}", file=sys.stderr)

    # Print domain summary
    print("\n── Domain summary ──", file=sys.stderr)
    for domain, info in sorted(output["domains"].items()):
        total = len(info["classes"]) + len(info["functions"]) + len(info["other"])
        print(f"  {domain}: {len(info['classes'])} classes, {len(info['functions'])} functions ({total} total)", file=sys.stderr)

    # Print fork summary
    print("\n── Fork summary ──", file=sys.stderr)
    for fork in fork_order + features:
        if fork in output["fork_summary"]:
            fs = output["fork_summary"][fork]
            print(f"  {fork}: {len(fs['new'])} new, {len(fs['modified'])} modified", file=sys.stderr)


if __name__ == "__main__":
    main()
