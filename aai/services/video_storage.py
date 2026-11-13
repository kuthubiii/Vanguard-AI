"""Validated, chunked storage operations for uploaded video files."""

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


MAX_VIDEO_SIZE_BYTES = 2 * 1024 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024
ALLOWED_EXTENSIONS = {".mp4", ".webm", ".mov"}
CANONICAL_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}
_SAFE_STORAGE_NAME = re.compile(r"^[0-9a-f]{32}\.(?:mp4|webm|mov)$")


class VideoUploadError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StoredUpload:
    video_id: str
    stored_filename: str
    file_size: int
    content_type: str
    path: Path


def validate_upload_metadata(filename: str | None, content_type: str | None) -> tuple[str, str]:
    if not filename or filename in {".", ".."}:
        raise VideoUploadError(400, "invalid_filename", "A video filename is required.")
    if len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise VideoUploadError(400, "invalid_filename", "The filename is invalid or exceeds 255 characters.")
    if "/" in filename or "\\" in filename:
        raise VideoUploadError(400, "invalid_filename", "Path separators are not allowed in filenames.")

    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise VideoUploadError(415, "unsupported_video_type", "Only MP4, WebM, and MOV files are supported.")

    declared_type = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
    compatible_types = {CANONICAL_CONTENT_TYPES[extension], "application/octet-stream", ""}
    if extension == ".mov":
        compatible_types.add("video/mp4")
    if declared_type not in compatible_types:
        raise VideoUploadError(415, "content_type_mismatch", "The declared content type does not match the video extension.")
    return extension, CANONICAL_CONTENT_TYPES[extension]


def _has_valid_signature(extension: str, prefix: bytes) -> bool:
    if extension in {".mp4", ".mov"}:
        return len(prefix) >= 12 and prefix[4:8] == b"ftyp"
    if extension == ".webm":
        return len(prefix) >= 4 and prefix[:4] == bytes.fromhex("1A45DFA3")
    return False


def store_video_upload(
    source: BinaryIO,
    filename: str | None,
    content_type: str | None,
    storage_dir: Path,
) -> StoredUpload:
    extension, canonical_type = validate_upload_metadata(filename, content_type)
    storage_dir.mkdir(parents=True, exist_ok=True)
    video_id = str(uuid.uuid4())
    storage_name = f"{uuid.uuid4().hex}{extension}"
    temp_path = storage_dir / f".{uuid.uuid4().hex}.part"
    final_path = storage_dir / storage_name
    size = 0
    prefix = bytearray()

    try:
        with temp_path.open("xb") as target:
            while True:
                chunk = source.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_VIDEO_SIZE_BYTES:
                    raise VideoUploadError(413, "file_too_large", "The maximum video upload size is 2 GB.")
                if len(prefix) < 16:
                    prefix.extend(chunk[: 16 - len(prefix)])
                target.write(chunk)

        if size == 0:
            raise VideoUploadError(400, "empty_file", "The uploaded video is empty.")
        if not _has_valid_signature(extension, bytes(prefix)):
            raise VideoUploadError(415, "invalid_video_content", "The file contents do not match a supported video container.")

        os.replace(temp_path, final_path)
        return StoredUpload(video_id, storage_name, size, canonical_type, final_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise


def safe_storage_path(storage_dir: Path, storage_key: str) -> Path:
    if not _SAFE_STORAGE_NAME.fullmatch(storage_key):
        raise ValueError("Stored video key is invalid")
    root = storage_dir.resolve()
    path = root / storage_key
    if path.parent.resolve() != root:
        raise ValueError("Stored video path escaped its storage directory")
    return path
