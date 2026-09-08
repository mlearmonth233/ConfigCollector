from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base_


class Organization(Base_):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    devices: Mapped[list["Device"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    credentials: Mapped[list["Credential"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["CollectionJob"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
