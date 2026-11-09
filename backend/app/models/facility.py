
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.organization import Organization


class Facility(Base):
    __tablename__ = "facilities"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "facility_code",
            name="uq_facilities_organization_code",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_facilities_id_organization",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_facilities_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    facility_code: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    timezone: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    address_line_1: Mapped[str | None] = mapped_column(String)
    address_line_2: Mapped[str | None] = mapped_column(String)
    city: Mapped[str | None] = mapped_column(String)
    region: Mapped[str | None] = mapped_column(String)
    postal_code: Mapped[str | None] = mapped_column(String)
    country_code: Mapped[str | None] = mapped_column(String(2))

    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="active",
        server_default="active",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    organization: Mapped["Organization"] = relationship(
        back_populates="facilities",
    )
