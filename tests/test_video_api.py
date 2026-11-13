"""Backend tests for validated video upload and metadata management."""

import io
import os
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Response, UploadFile
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from aai.api.routes.videos import delete_video, get_video, list_videos, upload_video
from aai.db.base import Base
from aai.db.models import Video
from aai.services import video_storage


MP4_FIXTURE = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"


def make_upload(filename: str = "warehouse.mp4", body: bytes = MP4_FIXTURE, mime: str = "video/mp4") -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(body),
        headers=Headers({"content-type": mime}),
    )


class VideoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_dir = Path(self.temp_dir.name) / "videos"
        self.env_patch = patch.dict(os.environ, {"VANGUARD_VIDEO_STORAGE_DIR": str(self.storage_dir)})
        self.env_patch.start()
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_upload_persists_metadata_and_safe_storage_name(self) -> None:
        response = Response()
        record = upload_video(response, make_upload(filename="shift-one.mp4"), self.db)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Location"], f"/api/videos/{record.id}")
        self.assertEqual(record.original_filename, "shift-one.mp4")
        self.assertEqual(record.file_size, len(MP4_FIXTURE))
        self.assertEqual(record.content_type, "video/mp4")
        self.assertEqual(record.upload_status, "uploaded")
        self.assertFalse(hasattr(record, "storage_key"))
        self.assertFalse(hasattr(record, "stored_filename"))
        stored_files = list(self.storage_dir.iterdir())
        self.assertEqual(len(stored_files), 1)
        self.assertRegex(stored_files[0].name, r"^[0-9a-f]{32}\.mp4$")
        self.assertEqual(stored_files[0].read_bytes(), MP4_FIXTURE)

    def test_upload_retry_with_same_key_returns_existing_record(self) -> None:
        key = str(uuid.uuid4())
        first_response = Response()
        first = upload_video(first_response, make_upload(), self.db, key)
        retry_response = Response()
        retry = upload_video(retry_response, make_upload(), self.db, key)
        self.assertEqual(first.id, retry.id)
        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(list(self.storage_dir.glob("*.mp4")), [self.storage_dir / self.db.get(Video, first.id).stored_filename])
        self.assertEqual(list_videos(self.db, limit=100, offset=0).total, 1)

    def test_reusing_upload_key_for_a_different_file_is_rejected(self) -> None:
        key = str(uuid.uuid4())
        upload_video(Response(), make_upload(), self.db, key)
        with self.assertRaises(HTTPException) as error:
            upload_video(Response(), make_upload(filename="different.mp4"), self.db, key)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(list_videos(self.db, limit=100, offset=0).total, 1)
        self.assertEqual(len(list(self.storage_dir.glob("*.mp4"))), 1)

    def test_large_chunked_upload_crosses_multiple_storage_chunks(self) -> None:
        body = MP4_FIXTURE + (b"x" * (3 * 1024 * 1024))
        with patch.object(video_storage, "UPLOAD_CHUNK_SIZE", 128 * 1024):
            record = upload_video(Response(), make_upload(body=body), self.db)
        stored = next(self.storage_dir.glob("*.mp4"))
        self.assertEqual(record.file_size, len(body))
        self.assertEqual(stored.stat().st_size, len(body))
        self.assertEqual(stored.read_bytes()[: len(MP4_FIXTURE)], MP4_FIXTURE)

    def test_list_and_get_return_persisted_video_metadata(self) -> None:
        response = upload_video(Response(), make_upload(), self.db)

        listed = list_videos(self.db, limit=100, offset=0)
        detail = get_video(response.id, self.db)
        self.assertEqual(listed.total, 1)
        self.assertEqual(listed.items[0].id, response.id)
        self.assertEqual(detail.original_filename, "warehouse.mp4")
        with self.assertRaises(HTTPException) as missing:
            get_video("missing-video-id", self.db)
        self.assertEqual(missing.exception.status_code, 404)

    def test_accepts_webm_and_mov_signatures(self) -> None:
        webm_header = bytes.fromhex("1A45DFA3") + b"\x93\x42\x82\x84webm"
        mov_header = b"\x00\x00\x00\x18ftypqt  \x00\x00\x02\x00qt  "
        webm = upload_video(Response(), make_upload("camera.webm", webm_header, "video/webm"), self.db)
        mov = upload_video(Response(), make_upload("camera.mov", mov_header, "video/quicktime"), self.db)
        self.assertEqual(webm.content_type, "video/webm")
        self.assertEqual(mov.content_type, "video/quicktime")
        self.assertEqual(list_videos(self.db, limit=100, offset=0).total, 2)

    def test_delete_removes_file_and_metadata(self) -> None:
        record = upload_video(Response(), make_upload(), self.db)
        delete_video(record.id, self.db)

        self.assertEqual(list_videos(self.db, limit=100, offset=0).total, 0)
        self.assertEqual(list(self.storage_dir.iterdir()), [])
        with self.assertRaises(HTTPException) as missing:
            delete_video(record.id, self.db)
        self.assertEqual(missing.exception.status_code, 404)

    def test_delete_cleans_metadata_when_file_is_already_missing(self) -> None:
        record = upload_video(Response(), make_upload(), self.db)
        next(self.storage_dir.glob("*.mp4")).unlink()
        delete_video(record.id, self.db)
        self.assertEqual(list_videos(self.db, limit=100, offset=0).total, 0)

    def test_rejects_bad_extension_path_filename_and_content_signature(self) -> None:
        cases = [
            (make_upload(filename="payload.exe", mime="application/octet-stream"), 415),
            (make_upload(filename="../unsafe.mp4"), 400),
            (make_upload(body=b"not a video"), 415),
        ]
        for upload, expected_status in cases:
            with self.subTest(filename=upload.filename):
                with self.assertRaises(HTTPException) as error:
                    upload_video(Response(), upload, self.db)
                self.assertEqual(error.exception.status_code, expected_status)
                self.assertEqual(list(self.storage_dir.iterdir()) if self.storage_dir.exists() else [], [])

    def test_rejects_oversized_stream_and_removes_partial_file(self) -> None:
        with patch.object(video_storage, "MAX_VIDEO_SIZE_BYTES", 8), patch.object(video_storage, "UPLOAD_CHUNK_SIZE", 4):
            with self.assertRaises(HTTPException) as error:
                upload_video(Response(), make_upload(body=MP4_FIXTURE), self.db)
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(list(self.storage_dir.iterdir()), [])

    def test_database_failure_removes_finalized_video(self) -> None:
        class BrokenCommitSession(Session):
            def commit(self) -> None:
                raise SQLAlchemyError("simulated database failure")

        broken_db = BrokenCommitSession(self.engine, expire_on_commit=False)
        try:
            with self.assertRaises(HTTPException) as error:
                upload_video(Response(), make_upload(), broken_db)
            self.assertEqual(error.exception.status_code, 500)
            self.assertEqual(list(self.storage_dir.iterdir()), [])
        finally:
            broken_db.close()

    def test_interrupted_upload_removes_partial_file(self) -> None:
        class InterruptedReader(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                if self.tell() >= 8:
                    raise OSError("simulated interrupted stream")
                return super().read(8)

        upload = UploadFile(
            filename="interrupted.mp4",
            file=InterruptedReader(MP4_FIXTURE),
            headers=Headers({"content-type": "video/mp4"}),
        )
        with self.assertRaises(HTTPException) as error:
            upload_video(Response(), upload, self.db)
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(list(self.storage_dir.iterdir()), [])

    def test_delete_database_failure_restores_the_file(self) -> None:
        record = upload_video(Response(), make_upload(), self.db)

        class BrokenCommitSession(Session):
            def commit(self) -> None:
                raise SQLAlchemyError("simulated database failure")

        broken_db = BrokenCommitSession(self.engine, expire_on_commit=False)
        try:
            with self.assertRaises(HTTPException) as error:
                delete_video(record.id, broken_db)
            self.assertEqual(error.exception.status_code, 500)
            self.assertEqual(list_videos(self.db, limit=100, offset=0).total, 1)
            self.assertEqual(len(list(self.storage_dir.glob("*.mp4"))), 1)
            self.assertEqual(list(self.storage_dir.glob(".*.deleting")), [])
        finally:
            broken_db.close()

    def test_storage_lookup_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            video_storage.safe_storage_path(self.storage_dir, "..\\outside.mp4")


if __name__ == "__main__":
    unittest.main()
