from datetime import datetime
from typing import Optional, Dict
from pydantic import BaseModel, ConfigDict

class DocumentBase(BaseModel):
    source_category: str
    title: str
    source_url: str
    pdf_url: str
    published_date: Optional[datetime] = None

class DocumentListItem(DocumentBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class DocumentRead(DocumentListItem):
    content: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class StatsResponse(BaseModel):
    total_documents: int
    documents_by_category: Dict[str, int]
    last_sync_time: Optional[datetime] = None
    system_status: str

class SyncResponse(BaseModel):
    status: str
    message: str
    processed_count: int
    errors: list[str]
