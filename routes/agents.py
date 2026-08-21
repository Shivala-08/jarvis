"""Agent routes — coding assistant, web task, vision.

Handles:
- POST /api/code — coding assistant
- POST /api/code/apply — apply code changes
- POST /api/web-task — web task agent
- GET /api/web-task/search — quick web search
- GET /api/web-task/scrape — quick web scrape
- GET /api/vision/status — vision agent status
- POST /api/vision/analyze-screen — capture & analyze screen
- POST /api/vision/analyze-image — analyze image from URL
- POST /api/vision/analyze-upload — analyze uploaded image
- POST /api/study — decompose study topic
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends

from core.auth import require_token
from core.error_models import CodeRequest, VisionRequest, WebTaskRequest
from core.event_bus import EventType, publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["agents"])


# ---------------------------------------------------------------------------
# Study
# ---------------------------------------------------------------------------


@router.post("/study")
async def api_study(topic: str, conversation_id: Optional[str] = None):
    """Decompose study topic with latency tracking."""
    from agents.study_agent import decompose_topic
    from core.eval_metrics import get_latency_tracker

    latency_tracker = get_latency_tracker()
    with latency_tracker.track("study"):
        plan = await asyncio.to_thread(decompose_topic, topic)

    publish(
        EventType.STUDY_PLAN_GENERATED,
        {"topic": topic, "units": len(plan.get("units", []))},
        source="api",
    )

    return plan


# ---------------------------------------------------------------------------
# Coding Assistant
# ---------------------------------------------------------------------------


@router.post("/code", dependencies=[Depends(require_token)])
def api_code(req: CodeRequest):
    """Coding assistant — fix bugs, add features, explain code."""
    from agents.coding_agent import CodeAssistant
    from core.eval_metrics import get_latency_tracker

    latency_tracker = get_latency_tracker()
    assistant = CodeAssistant()

    with latency_tracker.track("code"):
        if req.action == "fix":
            result = assistant.fix_bug(req.instruction, req.file_path)
        elif req.action == "add":
            result = assistant.add_feature(req.instruction, req.file_path)
        elif req.action == "explain":
            result = assistant.explain(req.instruction, req.file_path)
        elif req.action == "refactor":
            result = assistant.refactor(req.instruction, req.file_path)
        elif req.action == "review":
            result = assistant.review(req.file_path or "main.py")
        else:
            # Auto-detect intent
            instruction_lower = req.instruction.lower()
            if any(w in instruction_lower for w in ["fix", "bug", "error"]):
                result = assistant.fix_bug(req.instruction, req.file_path)
            elif any(w in instruction_lower for w in ["add", "create", "new", "feature"]):
                result = assistant.add_feature(req.instruction, req.file_path)
            elif any(w in instruction_lower for w in ["explain", "what", "how", "why"]):
                result = assistant.explain(req.instruction, req.file_path)
            elif any(w in instruction_lower for w in ["refactor", "improve", "clean"]):
                result = assistant.refactor(req.instruction, req.file_path)
            elif any(w in instruction_lower for w in ["review", "check", "audit"]):
                result = assistant.review(req.file_path or "main.py")
            else:
                result = assistant.explain(req.instruction, req.file_path)

    publish(
        EventType.TASK_COMPLETED,
        {"task_text": req.instruction, "action": result.get("action", "unknown")},
        source="api",
    )

    return result


@router.post("/code/apply", dependencies=[Depends(require_token)])
def api_code_apply(result: dict, dry_run: bool = True):
    """Apply code changes from a coding assistant result."""
    from agents.coding_agent import CodeAssistant
    assistant = CodeAssistant()
    return assistant.apply_changes(result, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Web Task
# ---------------------------------------------------------------------------


@router.post("/web-task", dependencies=[Depends(require_token)])
def api_web_task(req: WebTaskRequest):
    """Web task agent — search, scrape, or complete web tasks."""
    from agents.web_task_agent import WebTaskAgent
    from core.eval_metrics import get_latency_tracker

    latency_tracker = get_latency_tracker()
    agent = WebTaskAgent()

    with latency_tracker.track("web_task"):
        if req.action == "search":
            result = agent.search(req.task)
        elif req.action == "scrape" and req.url:
            result = agent.scrape(req.url, req.selector)
        else:
            result = agent.execute(req.task)

    publish(
        EventType.TASK_COMPLETED,
        {"task_text": req.task, "action": req.action},
        source="api",
    )

    return result


@router.get("/web-task/search")
def api_web_search(q: str):
    """Quick web search endpoint."""
    from agents.web_task_agent import WebTaskAgent
    agent = WebTaskAgent()
    return agent.search(q)


@router.get("/web-task/scrape")
def api_web_scrape(url: str, selector: Optional[str] = None):
    """Quick web scrape endpoint."""
    from agents.web_task_agent import WebTaskAgent
    agent = WebTaskAgent()
    return agent.scrape(url, selector)


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------


@router.get("/vision/status", dependencies=[Depends(require_token)])
def api_vision_status():
    """Check vision agent status — model availability, dependencies."""
    from agents.vision_agent import get_vision_status
    return get_vision_status()


@router.post("/vision/analyze-screen", dependencies=[Depends(require_token)])
def api_vision_screen(prompt: str = "What's on screen?"):
    """Capture and analyze a screenshot."""
    from agents.vision_agent import analyze_screen
    return analyze_screen(prompt)


@router.post("/vision/analyze-image", dependencies=[Depends(require_token)])
def api_vision_image(request: VisionRequest):
    """Analyze an image from URL or path."""
    from agents.vision_agent import analyze_image
    if not request.image_url:
        return {"analysis": "No image_url provided", "source": "error", "image_size": [0, 0]}
    return analyze_image(request.image_url, request.prompt)


@router.post("/vision/analyze-upload", dependencies=[Depends(require_token)])
def api_vision_upload(file: bytes, prompt: str = "What do you see?"):
    """Analyze an uploaded image file."""
    from agents.vision_agent import analyze_image_bytes
    return analyze_image_bytes(file, prompt)
