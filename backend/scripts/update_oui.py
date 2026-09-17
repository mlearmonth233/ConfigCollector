"""Refreshes the bundled MAC address manufacturer table (app/data/oui.tsv.gz).

The inventory names the maker of every endpoint MAC from the IEEE
registries: MA-L (24-bit OUIs), MA-M (28-bit) and MA-S (36-bit). The IEEE
publishes them as CSV; this script downloads the three files and writes
one compressed tab-separated table of "hex prefix<TAB>organisation" that
app/services/oui.py reads. Run it every few months and commit the result:

    python backend/scripts/update_oui.py
    python backend/scripts/update_oui.py --from-dir downloads/   # already-downloaded oui.csv, mam.csv, oui36.csv
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import sys
import urllib.request
from pathlib import Path

SOURCES = {
    "oui.csv": "https://standards-oui.ieee.org/oui/oui.csv",
    "mam.csv": "https://standards-oui.ieee.org/oui28/mam.csv",
    "oui36.csv": "https://standards-oui.ieee.org/oui36/oui36.csv",
}
TARGET = Path(__file__).resolve().parents[1] / "app" / "data" / "oui.tsv.gz"


def _read(name: str, from_dir: Path | None) -> str:
    if from_dir is not None:
        return (from_dir / name).read_text(encoding="utf-8", errors="replace")
    with urllib.request.urlopen(SOURCES[name], timeout=120) as response:  # noqa: S310 - fixed IEEE URLs
        return response.read().decode("utf-8", errors="replace")


def _tidy(org: str) -> str:
    return " ".join(org.split())


def build(from_dir: Path | None) -> dict[str, str]:
    table: dict[str, str] = {}
    for name in SOURCES:
        text = _read(name, from_dir)
        rows = csv.DictReader(io.StringIO(text))
        count = 0
        for row in rows:
            prefix = (row.get("Assignment") or "").strip().upper()
            org = _tidy(row.get("Organization Name") or "")
            if not prefix or not org or len(prefix) not in (6, 7, 9):
                continue
            table[prefix] = org
            count += 1
        print(f"{name}: {count} assignments", file=sys.stderr)
    return table


def write(table: dict[str, str], target: Path = TARGET) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(target, "wt", encoding="utf-8", compresslevel=9) as handle:
        for prefix in sorted(table):
            handle.write(f"{prefix}\t{table[prefix]}\n")
    print(f"wrote {target} ({target.stat().st_size // 1024} KB, {len(table)} prefixes)", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--from-dir", type=Path, help="folder holding oui.csv, mam.csv and oui36.csv instead of downloading")
    parser.add_argument("--target", type=Path, default=TARGET)
    args = parser.parse_args()
    write(build(args.from_dir), args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
