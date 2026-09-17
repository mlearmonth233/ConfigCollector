"""Who made a device, from its MAC address.

The IEEE assigns address blocks to manufacturers in three sizes: the
classic 24-bit OUI (first three bytes), and the smaller 28-bit and 36-bit
blocks sold since 2014. app/data/oui.tsv.gz holds all three registries
(see scripts/update_oui.py); the longest matching block wins.

Phones and laptops increasingly use a random, per-network address. Those
have the "locally administered" bit set and belong to nobody, so they are
reported as such rather than looked up.
"""

from __future__ import annotations

import gzip
import logging
import re
import threading
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("app.oui")

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "oui.tsv.gz"
RANDOMIZED = "Randomised private address"

_lock = threading.Lock()
_table: dict[str, str] | None = None


def _hex_digits(mac: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()


def _load() -> dict[str, str]:
    global _table
    if _table is not None:
        return _table
    with _lock:
        if _table is not None:
            return _table
        table: dict[str, str] = {}
        try:
            with gzip.open(DATA_FILE, "rt", encoding="utf-8") as handle:
                for line in handle:
                    prefix, _, org = line.rstrip("\n").partition("\t")
                    if prefix and org:
                        table[prefix] = org
        except OSError as exc:
            log.warning("MAC manufacturer table %s unavailable (%s); endpoints will have no manufacturer", DATA_FILE, exc)
        _table = table
        return table


def is_randomized(mac: str) -> bool:
    """True for a locally administered (self-assigned) unicast address -
    what iOS, Android, Windows and macOS use for per-network privacy."""
    digits = _hex_digits(mac)
    if len(digits) != 12:
        return False
    first_byte = int(digits[:2], 16)
    return bool(first_byte & 0x02) and not (first_byte & 0x01)


@lru_cache(maxsize=65536)
def manufacturer(mac: str | None) -> str | None:
    """Organisation name for the address's block, RANDOMIZED for a private
    address, None when unknown or malformed."""
    if not mac:
        return None
    digits = _hex_digits(mac)
    if len(digits) != 12:
        return None
    if is_randomized(digits):
        return RANDOMIZED
    table = _load()
    for length in (9, 7, 6):  # MA-S, MA-M, MA-L: most specific first
        org = table.get(digits[:length])
        if org:
            return org
    return None


def table_size() -> int:
    return len(_load())
