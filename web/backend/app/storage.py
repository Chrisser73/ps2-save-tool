from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import time
import uuid


_STORAGE = Path(os.environ.get("APP_STORAGE", "/tmp/ps2-save-web"))
_STORAGE.mkdir(parents=True, exist_ok=True)
_TTL_SECONDS = int(os.environ.get("APP_FILE_TTL", "3600"))
_MAX_BYTES = int(os.environ.get("APP_MAX_UPLOAD_BYTES", str(128 * 1024 * 1024)))


@dataclass(slots=True)
class StoredFile:
    file_id: str
    path: Path
    original_name: str


def _safe_name(name: str) -> str:
    name = Path(name or "upload.bin").name
    name = re.sub(r"[^A-Za-z0-9._()\- ]+", "_", name).strip(" .")
    return name[:160] or "upload.bin"


def cleanup_expired() -> None:
    now = time.time()
    for child in _STORAGE.iterdir():
        try:
            if child.is_dir() and now - child.stat().st_mtime > _TTL_SECONDS:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass


def create_slot(original_name: str) -> StoredFile:
    cleanup_expired()
    file_id = uuid.uuid4().hex
    folder = _STORAGE / file_id
    folder.mkdir(parents=True, exist_ok=False)
    name = _safe_name(original_name)
    return StoredFile(file_id=file_id, path=folder / ("upload-" + name), original_name=name)


def get_file(file_id: str) -> StoredFile:
    if not re.fullmatch(r"[0-9a-f]{32}", file_id):
        raise FileNotFoundError("Invalid upload id")
    folder = _STORAGE / file_id
    if not folder.is_dir():
        raise FileNotFoundError("Upload expired or not found")
    files = [p for p in folder.iterdir() if p.is_file() and p.name.startswith("upload-")]
    if not files:
        raise FileNotFoundError("Uploaded file not found")
    folder.touch()
    path = files[0]
    return StoredFile(file_id=file_id, path=path, original_name=path.name.removeprefix("upload-"))


def result_path(file_id: str, name: str) -> Path:
    folder = _STORAGE / file_id
    if not folder.is_dir():
        raise FileNotFoundError("Upload expired or not found")
    return folder / ("result-" + _safe_name(name))


def max_upload_bytes() -> int:
    return _MAX_BYTES
