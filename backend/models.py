from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_id: str = Field(default="demo-user", min_length=1)
    message: str = Field(..., min_length=1)


class Source(BaseModel):
    document_id: str
    source: str
    chunk_index: int
    snippet: str
    score: float | None = None


class ActionResult(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    user_id: str
    answer: str
    reply: str | None = None
    sources: list[Source] = Field(default_factory=list)
    actions: list[ActionResult] = Field(default_factory=list)
    memory_count: int


class UploadResponse(BaseModel):
    document_id: str
    filename: str
    chunks_indexed: int
    message: str
