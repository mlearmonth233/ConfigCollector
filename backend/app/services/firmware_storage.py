"""Where uploaded firmware images live on disk, and how they get there.

Firmware images routinely run several hundred MB to well over a gigabyte,
so uploads are streamed to disk in fixed-size chunks - never buffered whole
in memory - while an MD5 is computed incrementally over the same bytes as
they're written, so FirmwareImage.md5 is always exactly what ends up on
disk, not a separately-computed value that could drift from it.
"""

import hashlib
import os
import uuid
from dataclasses import dataclass

from fastapi import UploadFile

from app.config import get_settings

settings = get_settings()

_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    storage_path: str  # relative to Settings.firmware_storage_path - what's persisted on FirmwareImage
    size_bytes: int
    md5: str


def _org_directory(org_id) -> str:
    path = os.path.join(settings.firmware_storage_path, str(org_id))
    os.makedirs(path, exist_ok=True)
    return path


async def save_upload(org_id, upload: UploadFile) -> StoredFile:
    directory = _org_directory(org_id)
    original_name = os.path.basename(upload.filename or "firmware.bin")
    stored_name = f"{uuid.uuid4().hex}_{original_name}"
    destination = os.path.join(directory, stored_name)

    digest = hashlib.md5()
    size = 0
    with open(destination, "wb") as out:
        while True:
            chunk = await upload.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            out.write(chunk)

    return StoredFile(
        storage_path=os.path.join(str(org_id), stored_name),
        size_bytes=size,
        md5=digest.hexdigest(),
    )


def absolute_path(storage_path: str) -> str:
    return os.path.join(settings.firmware_storage_path, storage_path)


def delete_file(storage_path: str) -> None:
    try:
        os.remove(absolute_path(storage_path))
    except FileNotFoundError:
        pass
