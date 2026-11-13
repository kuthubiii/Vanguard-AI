"""Public video metadata response models."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VideoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    file_size: int
    content_type: str
    upload_status: str
    created_at: datetime


class VideoListRead(BaseModel):
    items: list[VideoRead]
    total: int
    limit: int
    offset: int
