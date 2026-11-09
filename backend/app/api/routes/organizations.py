
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.facility import FacilityListResponse
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationResponse,
)
from app.services.facility_service import get_organization_facilities
from app.services.organization_service import create_organization


router = APIRouter(
    prefix="/organizations",
    tags=["Organizations"],
)


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_organization(
    organization_data: OrganizationCreate,
    db: Session = Depends(get_db),
):
    try:
        return create_organization(db, organization_data)

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization could not be created because of a database conflict.",
        ) from exc


@router.get(
    "/{organization_id}/facilities",
    response_model=FacilityListResponse,
    status_code=status.HTTP_200_OK,
)
def list_organization_facilities(
    organization_id: UUID,
    db: Session = Depends(get_db),
):
    try:
        return get_organization_facilities(db, organization_id)

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
