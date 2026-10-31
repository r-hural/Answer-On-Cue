from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ContentLanguage(Base):
    """One row per supported language: whether subjects may use it, and its voice."""

    __tablename__ = "content_language"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    voice_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    voice_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
