
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.facility import FacilityCreate, FacilityResponse
from app.services.facility_service import create_facility


router = APIRouter(
    prefix="/facilities",
    tags=["Facilities"],
)


@router.post(
    "",
    response_model=FacilityResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_facility(
    facility_data: FacilityCreate,
    db: Session = Depends(get_db),
):
    try:
        return create_facility(db, facility_data)

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND
            if "does not exist" in str(exc)
            else status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Facility could not be created because of a database conflict.",
        ) from exc
