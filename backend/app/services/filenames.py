import re
from datetime import datetime

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


def build_snapshot_filename(hostname: str, *, collected_at: datetime, ext: str, include_timestamp: bool) -> str:
    """Filename for a downloaded config snapshot: the device's name by
    default (sanitized against characters unsafe in a filename), with an
    optional collection timestamp appended - shared between single-snapshot
    and bulk (zip) downloads so both name files the same way."""
    base = _UNSAFE_FILENAME_CHARS.sub("_", hostname).strip("_") or "device"
    if include_timestamp:
        base += f"_{collected_at:%Y%m%dT%H%M%S}"
    return f"{base}.{ext}"
