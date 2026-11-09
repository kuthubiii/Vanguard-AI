
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.schemas.organization import OrganizationCreate


def create_organization(
    db: Session,
    organization_data: OrganizationCreate,
) -> Organization:
    """
    Create and persist a new organization.
    """

    # 1. Check whether the organization slug already exists.
    existing_organization = db.scalar(
        select(Organization).where(
            Organization.slug == organization_data.slug
        )
    )

    if existing_organization:
        raise ValueError("An organization with this slug already exists.")

    # 2. Create a SQLAlchemy model object.
    organization = Organization(
        name=organization_data.name,
        slug=organization_data.slug,
    )

    # 3. Add the object to the database session.
    db.add(organization)

    # 4. Commit the transaction.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise

    # 5. Retrieve database-generated values.
    db.refresh(organization)

    return organization
