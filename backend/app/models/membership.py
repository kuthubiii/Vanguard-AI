
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Membership(Base):
    __tablename__ = "memberships"

    __table_args__ = (
        # Facility must belong to the membership's organization.
        ForeignKeyConstraint(
            ["facility_id", "organization_id"],
            ["facilities.id", "facilities.organization_id"],
            name="fk_memberships_facility_organization",
            ondelete="RESTRICT",
        ),

        # Role must belong to the same organization as the membership.
        # Platform roles cannot be assigned through memberships.
        ForeignKeyConstraint(
            ["role_id", "organization_id"],
            ["roles.id", "roles.organization_id"],
            name="fk_memberships_role_organization",
            ondelete="RESTRICT",
        ),

        CheckConstraint(
            "ended_at IS NULL OR ended_at >= created_at",
            name="ck_memberships_ended_at",
        ),

        # Only one active organization-level membership per user.
        Index(
            "uq_memberships_active_org",
            "organization_id",
            "user_id",
            unique=True,
            postgresql_where=text(
                "facility_id IS NULL AND is_active IS TRUE"
            ),
        ),

        # Only one active membership per user at each facility.
        Index(
            "uq_memberships_active_facility",
            "organization_id",
            "facility_id",
            "user_id",
            unique=True,
            postgresql_where=text(
                "facility_id IS NOT NULL AND is_active IS TRUE"
            ),
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

    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
