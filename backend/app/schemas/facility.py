from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FacilityCreate(BaseModel):
    organization_id: UUID

    facility_code: str = Field(
        min_length=2,
        max_length=50,
        pattern=r"^[A-Za-z0-9-]+$",
    )

    name: str = Field(
        min_length=2,
        max_length=200,
    )

    timezone: str = Field(
        min_length=1,
        max_length=100,
    )

    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None

    country_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
    )


class FacilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    facility_code: str
    name: str
    timezone: str
    status: str
    created_at: datetime
    updated_at: datetime

class FacilityListResponse(BaseModel):
    organization_id: UUID
    total_facilities: int
    facilities: list[FacilityResponse]
