"""Regenerate the golden parse output from the saved review pages.

Run this ONLY after reviewing a real parser change, and read the resulting diff
line by line — the whole value of the golden file is that it fails when parsing
changes, so regenerating it without reading the diff silently discards the test.

    uv run python tests/generate_golden.py
"""

import json

from conftest import GOLDEN, review_pages

from coffee.parser import parse_html


def main() -> None:
    parsed = {
        path.name: parse_html(path.read_text(encoding="utf-8"))
        for path in review_pages()
    }
    GOLDEN.write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(parsed)} parsed reviews to {GOLDEN}")


if __name__ == "__main__":
    main()
