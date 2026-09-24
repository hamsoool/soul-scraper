from sqlalchemy import Column, Integer, String, Text, DateTime, func, Index
from app.database import Base

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_category = Column(String(255), nullable=False)
    title = Column(String(1024), nullable=False)
    source_url = Column(String(2048), nullable=False)
    pdf_url = Column(String(2048), unique=True, nullable=False)
    content = Column(Text, nullable=True)
    published_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Explicit indexes for query paths used by the API
    __table_args__ = (
        Index("idx_documents_published_date", "published_date"),
        Index("idx_documents_created_at", "created_at"),
    )
