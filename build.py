#!/usr/bin/env python3
"""
Build the Beacon Chain Spec Explorer from a consensus-specs repo.

Orchestrates: extract → enrich → (optional) fetch examples → embed into index.html

Usage:
    # From a local repo clone:
    python3 build.py --repo-dir ~/consensus-specs

    # From GitHub (default):
    python3 build.py

    # Skip examples (faster, no network for test fixtures):
    python3 build.py --repo-dir ~/consensus-specs --skip-examples

    # Custom output directory:
    python3 build.py --repo-dir ~/consensus-specs --out-dir ./dist
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def run(cmd, **kwargs):
    """Run a command and return stdout. Exits on failure."""
    print(f"  $ {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"  FAILED: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description="Build the Beacon Chain Spec Explorer")
    parser.add_argument("--repo-dir", help="Path to local consensus-specs clone. If omitted, fetches from GitHub.")
    parser.add_argument("--branch", default="master", help="Git branch (default: master, only used with GitHub fetch)")
    parser.add_argument("--out-dir", default=".", help="Output directory (default: current dir)")
    parser.add_argument("--skip-examples", action="store_true", help="Skip fetching SSZ test fixture examples")
    parser.add_argument("--max-lines", type=int, default=300, help="Max lines per example YAML (default: 300)")
    args = parser.parse_args()

    script_dir = Path(__file__).parent.resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    index_json = out_dir / "beacon_specs_index.json"
    examples_json = out_dir / "examples.json"
    examples_js = out_dir / "examples.js"
    index_html_src = script_dir / "index.html"
    index_html_dst = out_dir / "index.html"

    # ── Step 1: Extract ──
    print("\n═══ Step 1: Extract spec definitions ═══", file=sys.stderr)
    extract_cmd = [sys.executable, str(script_dir / "extract.py"), "--output", str(index_json)]
    if args.repo_dir:
        extract_cmd += ["--repo-dir", args.repo_dir]
    else:
        extract_cmd += ["--branch", args.branch]
    run(extract_cmd)

    # ── Step 2: Enrich ──
    print("\n═══ Step 2: Enrich with structural annotations ═══", file=sys.stderr)
    run([sys.executable, str(script_dir / "enrich.py"),
         "--input", str(index_json), "--output", str(index_json)])

    # ── Step 3: Fetch examples (optional) ──
    if not args.skip_examples:
        print("\n═══ Step 3: Fetch SSZ test fixture examples ═══", file=sys.stderr)
        run([sys.executable, str(script_dir / "fetch_examples.py"),
             "--output", str(examples_json), "--max-lines", str(args.max_lines)])

        # Generate examples.js
        with open(examples_json) as f:
            examples = json.load(f)
        js_content = "window.__EXAMPLES__ = " + json.dumps(examples, separators=(",", ":"), ensure_ascii=True) + ";"
        with open(examples_js, "w") as f:
            f.write(js_content)
        print(f"  Generated {examples_js} ({len(js_content) // 1024}KB)", file=sys.stderr)
    else:
        print("\n═══ Step 3: Skipping examples ═══", file=sys.stderr)

    # ── Step 4: Embed into index.html ──
    print("\n═══ Step 4: Build index.html ═══", file=sys.stderr)

    with open(index_html_src) as f:
        html = f.read()

    # Embed spec data
    with open(index_json) as f:
        spec_data = json.load(f)
    spec_json = json.dumps(spec_data, separators=(",", ":"), ensure_ascii=True)
    spec_json_safe = spec_json.replace("</script>", r"<\/script>")

    html = re.sub(
        r'(<script id="spec-data"[^>]*>)[\s\S]*?(</script>)',
        lambda m: m.group(1) + spec_json_safe + m.group(2),
        html,
        count=1,
    )

    # Embed examples as inline script (if available)
    if not args.skip_examples and examples_js.exists():
        with open(examples_js) as f:
            examples_js_content = f.read()
        # Replace existing examples script or insert before main script
        if "window.__EXAMPLES__" in html:
            html = re.sub(
                r"<script>\nwindow\.__EXAMPLES__.*?\n</script>",
                "<script>\n" + examples_js_content + "\n</script>",
                html,
                count=1,
                flags=re.DOTALL,
            )
        else:
            # Insert before the main app script
            html = html.replace(
                '<script>\n// ── State',
                '<script>\n' + examples_js_content + '\n</script>\n<script>\n// ── State',
            )

    # Embed README.md (if available)
    readme_path = script_dir / "README.md"
    if readme_path.exists():
        with open(readme_path) as f:
            readme_content = f.read()
        readme_json = json.dumps(readme_content)
        readme_line = 'const README_MD = ' + readme_json + ';\n'
        html = re.sub(
            r'const README_MD = .*?;\n',
            lambda m: readme_line,
            html,
            count=1,
            flags=re.DOTALL,
        )
        print(f"  Embedded README.md ({len(readme_content)} chars)", file=sys.stderr)

    if out_dir != script_dir:
        # Copy favicon if it exists
        favicon = script_dir / "favicon.ico"
        if favicon.exists():
            import shutil
            shutil.copy2(favicon, out_dir / "favicon.ico")

    with open(index_html_dst, "w") as f:
        f.write(html)

    # ── Summary ──
    print("\n═══ Build complete ═══", file=sys.stderr)
    print(f"  Output: {out_dir}", file=sys.stderr)
    for f in sorted(out_dir.iterdir()):
        if f.suffix in (".html", ".json", ".js", ".ico", ".py"):
            size = f.stat().st_size
            print(f"    {f.name:30s} {size // 1024:>6d}KB", file=sys.stderr)
    print(f"\n  Open {index_html_dst} in a browser.", file=sys.stderr)


if __name__ == "__main__":
    main()
