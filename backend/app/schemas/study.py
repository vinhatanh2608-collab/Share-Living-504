from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field


class MaterialUpload(BaseModel):
    content_type: Literal["application/pdf", "text/plain"]
    size: int = Field(gt=0, le=20_000_000, strict=True)


class MaterialData(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class MaterialCreate(MaterialData):
    upload_id: UUID


class Note(BaseModel):
    text: str = Field(max_length=20_000)
    expected_version: int = Field(ge=0, strict=True)
