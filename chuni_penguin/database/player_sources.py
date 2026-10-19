"""Source snapshots preserve provider identity; canonical scores remain in A's PB table."""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UInt64Integer


class PlayerSource(Base):
    __tablename__ = "v2_player_sources"
    discord_id: Mapped[int] = mapped_column(UInt64Integer(), primary_key=True)
    source: Mapped[str] = mapped_column(primary_key=True)
    credential: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default="linked")
