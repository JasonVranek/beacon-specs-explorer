#!/usr/bin/env python3
"""
Enrich beacon_specs_index.json with structured annotations extracted
deterministically from the code. No LLM needed — pure parsing.

Adds to each item's fork definitions:
  - fields: [{name, type, comment}]  (for Container/dataclass types)
  - params: [{name, type}]           (for functions)
  - return_type: str                 (for functions)
  - docstring: str                   (for functions with docstrings)
  - references: [str]                (type/function names referenced in the code)

Adds top-level:
  - _references: {name: [referencing_item_names]}  (reverse index)
  - _field_index: {type.field: {type, defined_at_fork}}  (field lookup)

Usage:
    python3 enrich.py [--input PATH] [--output PATH]
"""

import argparse
import json
import re
import sys
from collections import defaultdict


# ── Field extraction (Container classes) ──────────────────────────────────

def extract_fields(code: str) -> list:
    """Extract fields from a Container/dataclass class definition.

    Returns list of {name, type, comment} dicts.
    Handles both inline comments (field: Type  # comment) and
    preceding-line comments (# [New in X] on the line above a field).
    """
    fields = []
    lines = code.split("\n")

    # Skip the class line
    in_body = False
    pending_comment = ""
    for line in lines:
        stripped = line.strip()

        if stripped.startswith("class ") or stripped.startswith("@"):
            in_body = True
            continue

        if not in_body:
            continue

        # Skip docstrings
        if stripped.startswith('"""') or stripped.startswith("'''"):
            continue

        # Blank line resets pending comment
        if not stripped:
            pending_comment = ""
            continue

        # Pure comment line — accumulate as pending for next field
        if stripped.startswith("#"):
            comment_text = stripped[1:].strip()
            if pending_comment:
                pending_comment += "; " + comment_text
            else:
                pending_comment = comment_text
            continue

        # Match field: type  # optional inline comment
        field_match = re.match(
            r"(\w+)\s*:\s*(.+?)(?:\s*#\s*(.*))?$", stripped
        )
        if field_match:
            fname = field_match.group(1)
            ftype = field_match.group(2).strip()
            fcomment = (field_match.group(3) or "").strip()

            # Clean up trailing comment from type if regex was greedy
            if "  #" in ftype:
                parts = ftype.split("  #", 1)
                ftype = parts[0].strip()
                if not fcomment:
                    fcomment = parts[1].strip()

            # Merge pending comment (from preceding line) with inline comment
            if pending_comment:
                if fcomment:
                    fcomment = pending_comment + "; " + fcomment
                else:
                    fcomment = pending_comment
                pending_comment = ""

            fields.append({
                "name": fname,
                "type": ftype,
                "comment": fcomment,
            })
        elif stripped.startswith("def "):
            # Hit a method definition — stop field extraction
            break
        else:
            # Unknown line, reset pending
            pending_comment = ""

    return fields


# ── Function signature extraction ─────────────────────────────────────────

def extract_signature(code: str) -> dict:
    """Extract function parameters, return type, and docstring.

    Returns {params: [{name, type}], return_type: str, docstring: str}
    """
    result = {"params": [], "return_type": "", "docstring": ""}

    # Extract the full def line (may span multiple lines with line continuations)
    # Collect everything from 'def ' to the closing ')' and optional '-> type:'
    lines = code.split("\n")
    def_text = ""
    paren_depth = 0
    found_def = False

    for line in lines:
        if line.strip().startswith("def "):
            found_def = True
        if found_def:
            def_text += " " + line.strip()
            paren_depth += line.count("(") - line.count(")")
            if paren_depth <= 0 and ":" in line:
                break

    if not found_def:
        return result

    # Extract return type
    ret_match = re.search(r"\)\s*->\s*([^:]+):", def_text)
    if ret_match:
        result["return_type"] = ret_match.group(1).strip()

    # Extract parameters
    param_match = re.search(r"\((.*)\)", def_text, re.DOTALL)
    if param_match:
        param_str = param_match.group(1).strip()
        if param_str:
            # Split on commas, but respect brackets
            params = split_params(param_str)
            for p in params:
                p = p.strip()
                if not p or p == "self":
                    continue
                # Handle: name: Type = default
                type_match = re.match(r"(\w+)\s*:\s*([^=]+?)(?:\s*=\s*(.+))?$", p)
                if type_match:
                    result["params"].append({
                        "name": type_match.group(1),
                        "type": type_match.group(2).strip(),
                        "default": (type_match.group(3) or "").strip() or None,
                    })
                else:
                    # No type annotation
                    name_match = re.match(r"(\w+)", p)
                    if name_match:
                        result["params"].append({
                            "name": name_match.group(1),
                            "type": "",
                            "default": None,
                        })

    # Extract docstring
    in_body = False
    doc_lines = []
    doc_started = False
    doc_delim = None

    for line in lines:
        if line.strip().startswith("def "):
            in_body = True
            continue
        if not in_body:
            continue

        stripped = line.strip()

        if not doc_started:
            if stripped.startswith('"""'):
                doc_delim = '"""'
                doc_started = True
                # Single-line docstring
                if stripped.count('"""') >= 2:
                    content = stripped[3:]
                    content = content[:content.index('"""')]
                    result["docstring"] = content.strip()
                    break
                else:
                    doc_lines.append(stripped[3:])
            elif stripped.startswith("'''"):
                doc_delim = "'''"
                doc_started = True
                if stripped.count("'''") >= 2:
                    content = stripped[3:]
                    content = content[:content.index("'''")]
                    result["docstring"] = content.strip()
                    break
                else:
                    doc_lines.append(stripped[3:])
            else:
                break  # No docstring
        else:
            if doc_delim in stripped:
                doc_lines.append(stripped[:stripped.index(doc_delim)])
                break
            else:
                doc_lines.append(stripped)

    if doc_lines and not result["docstring"]:
        result["docstring"] = "\n".join(doc_lines).strip()

    # Remove None defaults for cleaner output
    for p in result["params"]:
        if p["default"] is None:
            del p["default"]

    return result


def split_params(s: str) -> list:
    """Split parameter string on commas, respecting brackets."""
    parts = []
    depth = 0
    current = ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


# ── Reference extraction ──────────────────────────────────────────────────

def extract_references(code: str, all_names: set) -> list:
    """Extract names of types/functions referenced in the code.

    Only returns names that exist in the spec (from all_names set).
    """
    # Find all PascalCase words (types) and snake_case words (functions)
    words = set(re.findall(r"\b([A-Z]\w+|[a-z_]\w+)\b", code))

    # Filter to only names that exist in our spec
    refs = sorted(words & all_names)

    # Remove self-reference (the item's own name)
    return refs


# ── Composed type resolution ──────────────────────────────────────────────

def compose_fields_at_fork(item: dict, target_fork: str, fork_order: list) -> list:
    """Compose the full field list for a Container at a given fork.

    Walks from the earliest fork to target_fork, applying field changes.
    For the beacon chain spec, each fork redefines the ENTIRE Container
    (not just the changed fields), so we just return the fields from the
    latest fork at or before target_fork.
    """
    # Find the latest fork definition at or before target
    target_idx = fork_order.index(target_fork) if target_fork in fork_order else 999

    latest_fields = None
    for fork in fork_order:
        if fork_order.index(fork) > target_idx:
            break
        if fork in item.get("forks", {}):
            defn = item["forks"][fork]
            if "fields" in defn and defn["fields"]:
                latest_fields = defn["fields"]

    # Also check feature forks
    for fork, defn in item.get("forks", {}).items():
        if fork not in fork_order and "fields" in defn and defn["fields"]:
            latest_fields = defn["fields"]

    return latest_fields or []


# ── Main enrichment ───────────────────────────────────────────────────────

def enrich(data: dict) -> dict:
    """Add structural annotations to the spec index."""

    all_names = set(data["items"].keys())
    fork_order = data["_meta"]["fork_order"]

    # Per-item enrichment
    for name, item in data["items"].items():
        is_container = item["kind"] in ("class", "dataclass")
        is_function = item["kind"] == "def"

        for fork, defn in item["forks"].items():
            code = defn["code"]

            # Extract fields for containers
            if is_container:
                defn["fields"] = extract_fields(code)

            # Extract signature for functions
            if is_function:
                sig = extract_signature(code)
                defn["params"] = sig["params"]
                defn["return_type"] = sig["return_type"]
                if sig["docstring"]:
                    defn["docstring"] = sig["docstring"]

            # Extract references (for all items)
            refs = extract_references(code, all_names)
            # Remove self
            refs = [r for r in refs if r != name]
            if refs:
                defn["references"] = refs

    # ── EIP tag extraction ──────────────────────────────────────────────────
    eip_pattern = re.compile(r"\[(New|Modified) in (\w+):EIP(\d+)\]")
    eip_index = defaultdict(list)  # eip_number -> [{item, fork, change}]

    for name, item in data["items"].items():
        for fork, defn in item["forks"].items():
            code = defn["code"]
            eips_found = set()
            for m in eip_pattern.finditer(code):
                change_type = m.group(1).lower()  # "new" or "modified"
                eip_num = m.group(3)
                eips_found.add(eip_num)
                eip_index[eip_num].append({
                    "item": name,
                    "fork": fork,
                    "change": change_type,
                })
            if eips_found:
                defn["eips"] = sorted(eips_found, key=int)

    # Build _eip_index: sort by EIP number, deduplicate, count unique items
    data["_eip_index"] = {}
    for eip_num in sorted(eip_index.keys(), key=int):
        entries = eip_index[eip_num]
        # Sort entries alphabetically by item name, then fork
        entries.sort(key=lambda e: (e["item"], e["fork"]))
        unique_items = len(set(e["item"] for e in entries))
        data["_eip_index"][eip_num] = {
            "items": entries,
            "count": unique_items,
        }

    # Build reverse reference index
    ref_index = defaultdict(set)
    for name, item in data["items"].items():
        for fork, defn in item["forks"].items():
            for ref in defn.get("references", []):
                ref_index[ref].add(name)

    data["_references"] = {
        k: sorted(v) for k, v in sorted(ref_index.items())
    }

    # Build field index: "BeaconState.slot" -> {type: "Slot", forks: [...]}
    field_index = {}
    for name, item in data["items"].items():
        if item["kind"] not in ("class", "dataclass"):
            continue
        for fork, defn in item["forks"].items():
            for field in defn.get("fields", []):
                key = f"{name}.{field['name']}"
                if key not in field_index:
                    field_index[key] = {
                        "container": name,
                        "field": field["name"],
                        "type": field["type"],
                        "forks": {},
                    }
                field_index[key]["forks"][fork] = {
                    "type": field["type"],
                    "comment": field.get("comment", ""),
                }
                # Update top-level type to latest
                field_index[key]["type"] = field["type"]

    data["_field_index"] = field_index

    # Update meta
    data["_meta"]["enriched"] = True
    data["_meta"]["total_eips"] = len(data["_eip_index"])
    data["_meta"]["total_fields"] = sum(
        len(defn.get("fields", []))
        for item in data["items"].values()
        for defn in item["forks"].values()
    )
    data["_meta"]["total_references"] = sum(
        len(v) for v in data["_references"].values()
    )

    return data


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Enrich beacon spec index with structural annotations")
    parser.add_argument("--input", default="./beacon_specs_index.json", help="Input JSON")
    parser.add_argument("--output", default="./beacon_specs_index.json", help="Output JSON (overwrites by default)")
    args = parser.parse_args()

    print(f"Loading {args.input}...", file=sys.stderr)
    with open(args.input) as f:
        data = json.load(f)

    print(f"Enriching {len(data['items'])} items...", file=sys.stderr)
    data = enrich(data)

    # Stats
    total_fields = data["_meta"]["total_fields"]
    total_refs = data["_meta"]["total_references"]
    items_with_docstrings = sum(
        1 for item in data["items"].values()
        for defn in item["forks"].values()
        if defn.get("docstring")
    )
    items_with_fields = sum(
        1 for item in data["items"].values()
        for defn in item["forks"].values()
        if defn.get("fields")
    )
    items_with_params = sum(
        1 for item in data["items"].values()
        for defn in item["forks"].values()
        if defn.get("params")
    )

    print(f"\nEnrichment results:", file=sys.stderr)
    print(f"  {items_with_fields} definitions with field extraction", file=sys.stderr)
    print(f"  {total_fields} total fields across all forks", file=sys.stderr)
    print(f"  {items_with_params} definitions with param extraction", file=sys.stderr)
    print(f"  {items_with_docstrings} definitions with docstrings", file=sys.stderr)
    print(f"  {len(data['_references'])} items referenced by others", file=sys.stderr)
    print(f"  {total_refs} total reference edges", file=sys.stderr)
    print(f"  {len(data['_field_index'])} unique container fields", file=sys.stderr)
    print(f"  {data['_meta']['total_eips']} unique EIPs referenced", file=sys.stderr)

    with open(args.output, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nWritten to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
