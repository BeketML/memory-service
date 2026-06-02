"""Patch README.md with the latest benchmark results from benchmarks/latest.json.

Usage:
    python scripts/update_readme.py

Creates or replaces the '## Benchmark Results' section at the end of README.md.
Safe to run multiple times — only the section between the sentinel comments is modified.
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).parent.parent
README    = REPO_ROOT / "README.md"
DATA_FILE = REPO_ROOT / "benchmarks" / "latest.json"

SECTION_START = "<!-- BENCHMARK_START -->"
SECTION_END   = "<!-- BENCHMARK_END -->"


def _load_data() -> dict:
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found. Run pytest tests/test_benchmark.py first.")
        sys.exit(1)
    with open(DATA_FILE) as f:
        return json.load(f)


def _build_section(data: dict) -> str:
    run_at   = data["run_at"][:19].replace("T", " ") + " UTC"
    overall  = data["overall"]
    cats     = data["categories"]

    lines = [
        SECTION_START,
        "",
        "## Benchmark Results",
        "",
        f"Last run: **{run_at}** against `http://localhost:8080`",
        "",
        f"### Overall score: **{overall['passed']}/{overall['total']} ({overall['pct']})**",
        "",
        "| # | Category | Passed | Score | Status |",
        "|---|---|---|---|---|",
    ]
    for c in cats:
        lines.append(
            f"| {c['number']:2d} | {c['name']} | {c['passed']}/{c['total']} | {c['pct']} | {c['emoji']} |"
        )

    lines += [
        "",
        "#### Category descriptions (task.md §9)",
        "",
        "| # | Category | What is measured |",
        "|---|---|---|",
        "| 1 | Recall Quality | Primary signal: does `/recall` surface facts a follow-up question depends on? |",
        "| 2 | Fact Evolution | Contradictions detected; old fact superseded; history preserved; current fact in `/recall` |",
        "| 3 | Multi-hop Recall | Questions connecting two separate memories (e.g. pet name + city) |",
        "| 4 | Noise Resistance | Off-topic queries return empty context — no hallucination |",
        "| 5 | Extraction Quality | `/memories` shows structured typed rows (not raw message chunks) |",
        "| 6 | Persistence | Facts written before restart are recallable after `docker compose restart` |",
        "| 7 | Cross-session Scoping | Same-user cross-session sharing works; different users are isolated |",
        "| 8 | Robustness | Malformed input → 4xx; service stays up; unicode accepted |",
        "| 9 | Correctness (sync) | After POST /turns returns 201, data immediately in `/recall` and `/memories` |",
        "| 10 | Contract Compliance | All 7 endpoints return correct status codes and response shapes |",
        "",
        "> Run yourself: `pytest tests/test_benchmark.py -v -s`",
        "",
        SECTION_END,
    ]
    return "\n".join(lines)


def _patch_readme(section: str) -> None:
    text = README.read_text(encoding="utf-8") if README.exists() else ""

    if SECTION_START in text and SECTION_END in text:
        # Replace existing section
        before = text[: text.index(SECTION_START)]
        after  = text[text.index(SECTION_END) + len(SECTION_END):]
        new_text = before + section + after
    else:
        # Append at end
        new_text = text.rstrip("\n") + "\n\n" + section + "\n"

    README.write_text(new_text, encoding="utf-8")
    print(f"✓ README.md updated ({len(new_text):,} chars)")


def main() -> None:
    data    = _load_data()
    section = _build_section(data)
    _patch_readme(section)

    overall = data["overall"]
    print(f"  Overall: {overall['passed']}/{overall['total']} ({overall['pct']})")


if __name__ == "__main__":
    main()
