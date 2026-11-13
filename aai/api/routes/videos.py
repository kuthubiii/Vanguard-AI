"""Video upload and metadata management endpoints."""

import os
import uuid
from datetime import timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from aai.core.config import settings
from aai.db.models import Video
from aai.db.session import get_db
from aai.schemas.videos import VideoListRead, VideoRead
from aai.services.video_storage import VideoUploadError, safe_storage_path, store_video_upload
from aai.services.activity_logger import record_activity

router = APIRouter(prefix="/videos", tags=["videos"])


def _video_read(video: Video) -> VideoRead:
    created_at = video.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return VideoRead(
        id=video.id,
        original_filename=video.original_filename,
        file_size=video.file_size,
        content_type=video.content_type,
        upload_status=video.upload_status,
        created_at=created_at,
    )


@router.post("", response_model=VideoRead, status_code=status.HTTP_201_CREATED)
def upload_video(
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Upload-Idempotency-Key", max_length=36)] = None,
) -> VideoRead:
    stored = None
    committed = False
    upload_attempt_id = idempotency_key or str(uuid.uuid4())
    try:
        if idempotency_key is not None:
            try:
                idempotency_key = str(uuid.UUID(idempotency_key))
            except ValueError as error:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "invalid_idempotency_key", "message": "Upload retry key must be a UUID."},
                ) from error
            existing = db.scalar(select(Video).where(Video.upload_idempotency_key == idempotency_key))
            if existing is not None:
                return _return_idempotent_upload(existing, file, response)

        record_activity(category="VIDEO", event_type="video.upload.started",
                        title="Video upload started", description="A multipart video upload entered server-side validation and storage.",
                        severity="INFO", status="RUNNING", source="video_upload",
                        metadata={"declared_content_type": (file.content_type or "")[:64]},
                        idempotency_key=f"video-upload:{upload_attempt_id}:started", bind=db.get_bind())

        stored = store_video_upload(
            file.file,
            file.filename,
            file.content_type,
            settings.video_storage_dir,
        )
        record = Video(
            id=stored.video_id,
            original_filename=file.filename or "",
            stored_filename=stored.stored_filename,
            storage_key=stored.stored_filename,
            upload_idempotency_key=idempotency_key,
            file_size=stored.file_size,
            content_type=stored.content_type,
            upload_status="uploaded",
        )
        db.add(record)
        db.commit()
        committed = True
        response.headers["Location"] = f"/api/videos/{record.id}"
        return _video_read(record)
    except IntegrityError as error:
        db.rollback()
        if idempotency_key is not None:
            existing = db.scalar(select(Video).where(Video.upload_idempotency_key == idempotency_key))
            if existing is not None:
                return _return_idempotent_upload(existing, file, response)
        record_activity(category="VIDEO", event_type="video.upload.failed",
                        title="Video upload failed", description="Video metadata could not be saved after storage validation.",
                        severity="ERROR", status="FAILED", source="video_upload", error_type="IntegrityError",
                        idempotency_key=f"video-upload:{upload_attempt_id}:failed", bind=db.get_bind())
        raise HTTPException(
            status_code=500,
            detail={"code": "metadata_save_failed", "message": "Video metadata could not be saved."},
        ) from error
    except VideoUploadError as error:
        record_activity(category="VIDEO", event_type="video.upload.failed",
                        title="Video upload rejected", description=error.message,
                        severity="WARNING", status="FAILED", source="video_upload", error_type=error.code,
                        idempotency_key=f"video-upload:{upload_attempt_id}:failed", bind=db.get_bind())
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    except SQLAlchemyError as error:
        db.rollback()
        record_activity(category="VIDEO", event_type="video.upload.failed",
                        title="Video upload failed", description="Video metadata could not be saved.",
                        severity="ERROR", status="FAILED", source="video_upload", error_type=type(error).__name__,
                        idempotency_key=f"video-upload:{upload_attempt_id}:failed", bind=db.get_bind())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "metadata_save_failed", "message": "Video metadata could not be saved."},
        ) from error
    except OSError as error:
        db.rollback()
        record_activity(category="VIDEO", event_type="video.upload.failed",
                        title="Video upload failed", description="The uploaded file could not be stored.",
                        severity="ERROR", status="FAILED", source="video_upload", error_type=type(error).__name__,
                        idempotency_key=f"video-upload:{upload_attempt_id}:failed", bind=db.get_bind())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "video_storage_failed", "message": "The video could not be stored."},
        ) from error
    finally:
        if stored is not None and not committed:
            stored.path.unlink(missing_ok=True)
        file.file.close()


def _return_idempotent_upload(record: Video, file: UploadFile, response: Response) -> VideoRead:
    file_size = getattr(file, "size", None)
    if record.original_filename != (file.filename or "") or (file_size is not None and record.file_size != file_size):
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_key_conflict", "message": "This retry key was already used for a different video."},
        )
    response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/videos/{record.id}"
    return _video_read(record)


@router.get("", response_model=VideoListRead)
def list_videos(
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> VideoListRead:
    try:
        total = db.scalar(select(func.count()).select_from(Video)) or 0
        records = db.scalars(
            select(Video).order_by(Video.created_at.desc(), Video.id).offset(offset).limit(limit)
        ).all()
        return VideoListRead(items=[_video_read(video) for video in records], total=total, limit=limit, offset=offset)
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "video_list_failed", "message": "Uploaded video metadata could not be loaded."},
        ) from error


@router.get("/{video_id}", response_model=VideoRead)
def get_video(video_id: str, db: Session = Depends(get_db)) -> VideoRead:
    try:
        record = db.get(Video, video_id)
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "video_lookup_failed", "message": "Video metadata could not be loaded."},
        ) from error
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "video_not_found", "message": "Video not found."})
    return _video_read(record)


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(video_id: str, db: Session = Depends(get_db)) -> Response:
    try:
        record = db.get(Video, video_id)
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "video_lookup_failed", "message": "Video metadata could not be loaded."},
        ) from error
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "video_not_found", "message": "Video not found."})

    storage_dir = settings.video_storage_dir
    try:
        path = safe_storage_path(storage_dir, record.storage_key)
    except ValueError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "invalid_storage_reference", "message": "The stored video reference is invalid."},
        ) from error

    tombstone = storage_dir / f".{uuid.uuid4().hex}.deleting"
    moved_to_tombstone = False
    if path.exists():
        try:
            os.replace(path, tombstone)
            moved_to_tombstone = True
        except OSError as error:
            raise HTTPException(
                status_code=500,
                detail={"code": "video_delete_failed", "message": "The stored video could not be removed."},
            ) from error

    try:
        db.delete(record)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        if moved_to_tombstone:
            os.replace(tombstone, path)
        raise HTTPException(
            status_code=500,
            detail={"code": "metadata_delete_failed", "message": "Video metadata could not be deleted."},
        ) from error

    if moved_to_tombstone:
        try:
            tombstone.unlink()
        except OSError as error:
            # Restore both resources where possible instead of reporting a
            # successful delete while leaving the uploaded file behind.
            os.replace(tombstone, path)
            db.add(record)
            db.commit()
            raise HTTPException(
                status_code=500,
                detail={"code": "video_delete_failed", "message": "The stored video could not be removed."},
            ) from error
    record_activity(category="VIDEO", event_type="video.deleted",
                    title="Video deleted", description="A video record and its stored upload were deleted.",
                    severity="WARNING", status="COMPLETED", source="video_delete", video_id=video_id,
                    idempotency_key=f"video-delete:{video_id}:{uuid.uuid4()}", bind=db.get_bind())
    return Response(status_code=status.HTTP_204_NO_CONTENT)
