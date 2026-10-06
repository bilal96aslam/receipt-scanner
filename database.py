from sqlmodel import SQLModel, Field, create_engine
from datetime import datetime, timezone
from typing import Optional


def utc_now():
    return datetime.now(timezone.utc)


class Receipt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    vendor: str
    date: str
    total: float
    needs_review: bool = False
    review_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class ScanLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    status: str
    error_message: Optional[str] = None
    duration_seconds: float
    created_at: datetime = Field(default_factory=utc_now)


engine = create_engine("sqlite:///receipts.db")


def init_db():
    SQLModel.metadata.create_all(engine)