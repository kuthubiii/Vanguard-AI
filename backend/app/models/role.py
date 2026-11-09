
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.organization import Organization


class Role(Base):
    __tablename__ = "roles"

    __table_args__ = (
        # Required for the composite foreign key in memberships.
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_roles_id_organization",
        ),

        # Platform roles have no organization.
        # Organization and facility roles must belong to one.
        CheckConstraint(
            """
            (scope = 'platform' AND organization_id IS NULL)
            OR
            (scope IN ('organization', 'facility')
             AND organization_id IS NOT NULL)
            """,
            name="ck_roles_scope_organization",
        ),

        CheckConstraint(
            "scope IN ('platform', 'organization', 'facility')",
            name="ck_roles_scope",
        ),

        # Platform role codes must be globally unique.
        Index(
            "uq_roles_platform_code",
            "code",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),

        # Organization role codes must be unique within their organization.
        Index(
            "uq_roles_organization_code",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )

    code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    is_system: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
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

    organization: Mapped["Organization | None"] = relationship()
