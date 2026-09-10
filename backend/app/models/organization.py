from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base_


class Organization(Base_):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # How many days to keep a device's collected config snapshots before
    # tasks.py's purge_expired_snapshots deletes them - NULL means keep
    # forever (the default, so nothing is ever silently lost unless an
    # admin opts in). See api/organizations.py.
    snapshot_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    users: Mapped[list["User"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    devices: Mapped[list["Device"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    credentials: Mapped[list["Credential"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["CollectionJob"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    schedules: Mapped[list["Schedule"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
