#!/usr/bin/env python3
"""
Fetch SSZ test fixture examples from ethereum/consensus-spec-tests.

For each type in each fork that has ssz_static tests, fetches
case_0/value.yaml. Produces examples.json mapping:
  type_name -> { forks: { fork_name: { yaml, raw_url, total_lines, truncated } } }

Usage:
    python3 fetch_examples.py [--output PATH] [--max-lines N]
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
import time

GITHUB_RAW = "https://raw.githubusercontent.com/ethereum/consensus-spec-tests/refs/heads/master"
GITHUB_API = "https://api.github.com/repos/ethereum/consensus-spec-tests/contents"

FORKS = ["phase0", "altair", "bellatrix", "capella", "deneb", "electra", "fulu"]


def api_get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "beacon-spec-examples/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print(f"  Rate limited, waiting 10s...", file=sys.stderr)
                time.sleep(10)
            elif e.code == 404:
                return None
            else:
                raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    return None


def raw_get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "beacon-spec-examples/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt < retries - 1:
                time.sleep(2)
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    return None


def main():
    parser = argparse.ArgumentParser(description="Fetch SSZ test examples")
    parser.add_argument("--output", default="./examples.json", help="Output path")
    parser.add_argument("--max-lines", type=int, default=80, help="Max YAML lines per example")
    args = parser.parse_args()

    # Structure: { type_name: { forks: { fork: { yaml, raw_url, total_lines, truncated } } } }
    examples = {}

    for fork in FORKS:
        print(f"Checking {fork}...", file=sys.stderr)
        listing = api_get(f"{GITHUB_API}/tests/mainnet/{fork}/ssz_static")
        if not listing:
            print(f"  No ssz_static for {fork}", file=sys.stderr)
            continue

        types_in_fork = [item["name"] for item in listing if item["type"] == "dir"]
        print(f"  {len(types_in_fork)} types", file=sys.stderr)

        for type_name in types_in_fork:
            raw_url = f"{GITHUB_RAW}/tests/mainnet/{fork}/ssz_static/{type_name}/ssz_random/case_0/value.yaml"
            yaml_content = raw_get(raw_url)

            if not yaml_content:
                continue

            lines = yaml_content.split("\n")
            total_lines = len(lines)
            truncated = total_lines > args.max_lines

            yaml_display = "\n".join(lines[:args.max_lines]) if truncated else yaml_content.rstrip()

            if type_name not in examples:
                examples[type_name] = {"forks": {}}

            examples[type_name]["forks"][fork] = {
                "yaml": yaml_display,
                "raw_url": raw_url,
                "total_lines": total_lines,
                "truncated": truncated,
            }

            print(f"    {type_name}: {total_lines} lines{'*' if truncated else ''}", file=sys.stderr)
            time.sleep(0.2)

    with open(args.output, "w") as f:
        json.dump(examples, f, indent=2)

    total_entries = sum(len(v["forks"]) for v in examples.values())
    print(f"\nFetched {total_entries} examples across {len(examples)} types, written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
