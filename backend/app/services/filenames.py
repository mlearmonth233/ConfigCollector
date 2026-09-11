import re
from datetime import datetime

from app.services.device_types import get_device_type_spec

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


def folder_for_device_type(device_type: str | None) -> str:
    """Which subfolder a device's config goes into inside a job's bulk zip
    download - PDUs get their own "PDUs" folder, everything else (switches,
    WLCs, firewalls) shares "Switches" as the catch-all. `device_type` is
    None for an item whose device has since been deleted (see
    devices.py's delete_device) or, defensively, an unresolvable one -
    both fall back to "Switches" rather than erroring the whole download."""
    if device_type is not None:
        try:
            if get_device_type_spec(device_type).category == "pdu":
                return "PDUs"
        except ValueError:
            pass
    return "Switches"


def build_zip_filename(*, dated_at: datetime) -> str:
    """Default filename for a job's bulk config download - the date it was
    collected, so a folder of these sorts and reads naturally (e.g. as a
    nightly backup archive) without needing the job's id."""
    return f"{dated_at:%Y%m%d}.zip"
