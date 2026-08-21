"""Standardized API response models for consistent error handling.

All endpoints return responses matching these schemas, making
debugging and client integration predictable.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str
    detail: str
    status_code: int = 500


class SuccessResponse(BaseModel):
    """Standard success response wrapper."""
    status: str = "ok"
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class PaginatedResponse(BaseModel):
    """Paginated list response."""
    items: List[Dict[str, Any]] = []
    count: int = 0
    offset: int = 0
    limit: int = 20


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    service: str
    version: str
    components: Dict[str, str] = {}


class TaskStartRequest(BaseModel):
    """Request to start tracking a task."""
    task_text: str
    estimated_minutes: Optional[int] = None


class TaskCompleteRequest(BaseModel):
    """Request to record task completion."""
    task_text: str
    actual_minutes: float
    estimated_minutes: Optional[int] = None


class BrainDumpRequest(BaseModel):
    """Brain dump input."""
    text: str
    conversation_id: Optional[str] = None


class StudyRequest(BaseModel):
    """Study topic decomposition input."""
    topic: str
    conversation_id: Optional[str] = None


class SearchRequest(BaseModel):
    """Memory search input."""
    query: str


class RebalanceRequest(BaseModel):
    """Schedule rebalance input."""
    missed_block_id: Optional[int] = None


class SprintRequest(BaseModel):
    """Micro-sprint generation input."""
    task: str


class ScheduledTaskRequest(BaseModel):
    """Cron-scheduled task creation input."""
    task_id: str
    prompt: str
    cron_expression: str
    agent_type: str = "braindump"


class ConversationRequest(BaseModel):
    """Multi-turn conversation input."""
    message: str
    conversation_id: Optional[str] = None


class CodeRequest(BaseModel):
    """Coding assistant input."""
    instruction: str
    file_path: Optional[str] = None
    action: str = "auto"


class WebTaskRequest(BaseModel):
    """Web task agent input."""
    task: str
    url: Optional[str] = None
    selector: Optional[str] = None
    action: str = "auto"


class VisionRequest(BaseModel):
    """Vision agent input."""
    prompt: str = "What's on screen? Describe any errors or issues."
    image_url: Optional[str] = None
