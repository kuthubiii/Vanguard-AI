
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.facility import Facility
from app.models.organization import Organization
from app.schemas.facility import (
    FacilityCreate,
    FacilityListResponse,
)


def create_facility(
    db: Session,
    facility_data: FacilityCreate,
) -> Facility:
    """
    Create and persist a facility under an organization.
    """

    # 1. Verify that the organization exists.
    organization = db.scalar(
        select(Organization).where(
            Organization.id == facility_data.organization_id
        )
    )

    if not organization:
        raise ValueError("The specified organization does not exist.")

    # 2. Check facility code within this organization.
    existing_facility = db.scalar(
        select(Facility).where(
            Facility.organization_id == facility_data.organization_id,
            Facility.facility_code == facility_data.facility_code,
        )
    )

    if existing_facility:
        raise ValueError(
            "This facility code already exists in the organization."
        )

    # 3. Create the facility object.
    facility = Facility(
        organization_id=facility_data.organization_id,
        facility_code=facility_data.facility_code,
        name=facility_data.name,
        timezone=facility_data.timezone,
        address_line_1=facility_data.address_line_1,
        address_line_2=facility_data.address_line_2,
        city=facility_data.city,
        region=facility_data.region,
        postal_code=facility_data.postal_code,
        country_code=facility_data.country_code,
    )

    # 4. Save it to PostgreSQL.
    db.add(facility)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise

    # 5. Retrieve database-generated values.
    db.refresh(facility)

    return facility


def get_organization_facilities(
    db: Session,
    organization_id: UUID,
) -> FacilityListResponse:
    """
    Retrieve all facilities belonging to an organization.
    """

    # 1. Verify that the organization exists.
    organization = db.get(Organization, organization_id)

    if not organization:
        raise ValueError("The specified organization does not exist.")

    # 2. Retrieve facilities belonging to this organization.
    facilities = db.scalars(
        select(Facility)
        .where(Facility.organization_id == organization_id)
        .order_by(Facility.created_at.desc())
    ).all()

    # 3. Return structured response data.
    return FacilityListResponse(
        organization_id=organization_id,
        total_facilities=len(facilities),
        facilities=facilities,
    )
