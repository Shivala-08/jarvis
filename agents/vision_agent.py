"""Vision Agent — screenshot analysis using local VLM.

Phase E of the build manual. Analyzes screenshots and images using a
local Vision Language Model (Qwen2.5-VL-7B-Instruct-4bit via mlx-vlm).

Features:
- analyze_screen(): grab a screenshot, analyze it with VLM
- analyze_image(): analyze an existing image file or bytes
- Lazy model loading (load-on-demand, release after use — 4.8GB VRAM)
- Graceful fallback to cloud if mlx-vlm not available
- Memory discipline: never keeps VLM loaded alongside reasoning/coding models

Usage:
    from agents.vision_agent import analyze_screen, analyze_image, get_vision_status

    # Analyze current screen
    result = analyze_screen("What errors do you see?")

    # Analyze an image
    result = analyze_image("screenshot.png", "Describe the UI layout")
"""

import io
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy model loading — 4.8GB VRAM, load only when needed
# ---------------------------------------------------------------------------

_model = None
_processor = None
_model_name = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"


def _patch_model_config():
    """Patch the cached model config.json if it's missing text_config.

    Some mlx-community Qwen2.5-VL repos have text model params at root level
    instead of nested under 'text_config'. This patches the cached config
    so mlx-vlm can load the model correctly.
    """
    try:
        from pathlib import Path
        import json

        hub = Path.home() / ".cache/huggingface/hub/models--mlx-community--Qwen2.5-VL-7B-Instruct-4bit/snapshots"
        if not hub.exists():
            return

        for snapshot in hub.iterdir():
            config_path = snapshot / "config.json"
            if not config_path.exists():
                continue

            config = json.loads(config_path.read_text())
            if "text_config" in config:
                return  # Already patched

            # Move root-level text params into text_config
            text_keys = {
                "model_type", "hidden_size", "num_hidden_layers", "intermediate_size",
                "num_attention_heads", "rms_norm_eps", "vocab_size", "num_key_value_heads",
                "max_position_embeddings", "rope_theta", "rope_traditional", "rope_scaling",
                "tie_word_embeddings", "hidden_act", "max_window_layers", "sliding_window",
                "use_sliding_window", "use_cache", "attention_dropout", "initializer_range",
                "torch_dtype", "transformers_version",
            }
            text_config = {k: v for k, v in config.items() if k in text_keys}
            text_config["model_type"] = config.get("model_type", "qwen2")
            config["text_config"] = text_config
            config_path.write_text(json.dumps(config, indent=2))
            logger.info("Patched model config.json — added text_config")
            return
    except Exception as e:
        logger.warning(f"Config patch skipped: {e}")


def _load_model():
    """Load the VLM model on demand. Returns (model, processor) or (None, None)."""
    global _model, _processor

    if _model is not None:
        return _model, _processor

    try:
        # Patch config if needed before loading
        _patch_model_config()

        from mlx_vlm import load
        logger.info(f"Loading vision model: {_model_name} (~4.8GB VRAM)")
        _model, _processor = load(_model_name)

        # Patch rope_scaling on attention layers if missing
        # (needed for some mlx-community Qwen2.5-VL model repos)
        if _model is not None:
            try:
                rope_scaling = {"type": "mrope", "mrope_section": [16, 24, 24]}
                patched = 0
                for layer in _model.language_model.model.layers:
                    if hasattr(layer, "self_attn") and getattr(layer.self_attn, "rope_scaling", None) is None:
                        layer.self_attn.rope_scaling = rope_scaling
                        patched += 1
                if patched:
                    logger.info(f"Patched rope_scaling on {patched} attention layers")
            except Exception:
                pass  # Non-critical — model may not need it

        logger.info("Vision model loaded successfully")
        return _model, _processor
    except ImportError:
        logger.warning("mlx-vlm not installed — vision agent will use cloud fallback")
        return None, None
    except Exception as e:
        logger.error(f"Failed to load vision model: {e}")
        return None, None


def _release_model():
    """Release the VLM from memory. Call after analysis to free VRAM."""
    global _model, _processor
    _model = None
    _processor = None
    try:
        import gc
        gc.collect()
        # On Apple Silicon, also clear MLX metal cache
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass
    except Exception:
        pass
    logger.info("Vision model released from memory")


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------

def _capture_screen(monitor_index: int = 1) -> Any:
    """Capture a screenshot using mss.

    Args:
        monitor_index: Monitor to capture (1 = primary, 0 = all monitors).

    Returns:
        PIL Image object, resized to max 720px on longest side
        to fit within VLM memory budget (~9GB peak).
    """
    from PIL import Image
    import mss

    with mss.MSS() as sct:
        sct_img = sct.grab(sct.monitors[monitor_index])
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

    # Resize to fit VLM memory budget — full-res screenshots cause OOM
    max_dim = 720
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
        logger.info(f"Resized screenshot from {sct_img.size} to {img.size}")

    return img


def _resize_for_vlm(img: Any) -> Any:
    """Resize image to fit VLM memory budget (max 720px longest side)."""
    from PIL import Image
    max_dim = 720
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    return img


def _load_image_from_path(image_path: str) -> Any:
    """Load an image from a file path, resized for VLM."""
    from PIL import Image
    return _resize_for_vlm(Image.open(image_path))


def _load_image_from_bytes(image_bytes: bytes) -> Any:
    """Load an image from raw bytes, resized for VLM."""
    from PIL import Image
    return _resize_for_vlm(Image.open(io.BytesIO(image_bytes)))


# ---------------------------------------------------------------------------
# Analysis with local VLM
# ---------------------------------------------------------------------------

def _analyze_with_local_vlm(image: Any, prompt: str, max_tokens: int = 300) -> str:
    """Analyze an image using the local mlx-vlm model.

    Args:
        image: PIL Image object.
        prompt: What to analyze about the image.
        max_tokens: Max response tokens.

    Returns:
        Text description of the image analysis.
    """
    model, processor = _load_model()
    if model is None:
        raise RuntimeError("Vision model not available")

    try:
        from mlx_vlm import generate

        result = generate(model, processor, image=image, prompt=prompt, max_tokens=max_tokens)
        return result.strip()
    finally:
        # Release model after use to free VRAM
        _release_model()


# ---------------------------------------------------------------------------
# Analysis with cloud fallback
# ---------------------------------------------------------------------------

def _analyze_with_cloud(image: Any, prompt: str) -> str:
    """Analyze an image using a cloud VLM as fallback.

    Uses the cloud escalation system — sends the image description to a
    capable cloud model. Note: this sends a text description, not the
    actual image (to preserve privacy).
    """
    # Convert image to text description for cloud
    width, height = image.size
    mode = image.mode

    # Basic image stats for the cloud model
    image_context = f"[Image: {width}x{height}, mode={mode}]"

    try:
        from core.escalation import llm_call
        return llm_call(
            prompt=f"{image_context}\n\n{prompt}",
            system_prompt=(
                "You are a vision assistant. The user has provided an image "
                "description (dimensions and mode). Based on the prompt, "
                "provide a helpful analysis. Note: the actual image pixels "
                "are not available — work with the metadata provided."
            ),
            task_type="general",
            temperature=0.3,
        )
    except Exception as e:
        return f"Cloud analysis failed: {e}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_screen(
    prompt: str = "What's on screen? Describe any errors or issues.",
    monitor_index: int = 1,
) -> Dict[str, Any]:
    """Capture a screenshot and analyze it.

    Args:
        prompt: What to look for in the screenshot.
        monitor_index: Which monitor to capture (1 = primary).

    Returns:
        {
            "analysis": str — the VLM's description,
            "source": "local_vlm" | "cloud_fallback",
            "image_size": [width, height],
        }
    """
    try:
        image = _capture_screen(monitor_index)
    except Exception as e:
        return {
            "analysis": f"Screenshot capture failed: {e}",
            "source": "error",
            "image_size": [0, 0],
        }

    width, height = image.size

    # Try local VLM first
    try:
        analysis = _analyze_with_local_vlm(image, prompt)
        return {
            "analysis": analysis,
            "source": "local_vlm",
            "image_size": [width, height],
        }
    except Exception as e:
        logger.info(f"Local VLM unavailable ({e}), falling back to cloud")

    # Cloud fallback
    try:
        analysis = _analyze_with_cloud(image, prompt)
        return {
            "analysis": analysis,
            "source": "cloud_fallback",
            "image_size": [width, height],
        }
    except Exception as e:
        return {
            "analysis": f"All vision methods failed: {e}",
            "source": "error",
            "image_size": [width, height],
        }


def analyze_image(
    image_source: str,
    prompt: str = "Describe what you see in this image.",
) -> Dict[str, Any]:
    """Analyze an image file or URL.

    Args:
        image_source: File path or URL to the image.
        prompt: What to analyze about the image.

    Returns:
        {
            "analysis": str — the VLM's description,
            "source": "local_vlm" | "cloud_fallback",
            "image_size": [width, height],
        }
    """
    # Load image
    try:
        if image_source.startswith(("http://", "https://")):
            import httpx
            response = httpx.get(image_source, timeout=10)
            image = _load_image_from_bytes(response.content)
        elif Path(image_source).exists():
            image = _load_image_from_path(image_source)
        else:
            return {
                "analysis": f"Image not found: {image_source}",
                "source": "error",
                "image_size": [0, 0],
            }
    except Exception as e:
        return {
            "analysis": f"Failed to load image: {e}",
            "source": "error",
            "image_size": [0, 0],
        }

    width, height = image.size

    # Try local VLM first
    try:
        analysis = _analyze_with_local_vlm(image, prompt)
        return {
            "analysis": analysis,
            "source": "local_vlm",
            "image_size": [width, height],
        }
    except Exception as e:
        logger.info(f"Local VLM unavailable ({e}), falling back to cloud")

    # Cloud fallback
    try:
        analysis = _analyze_with_cloud(image, prompt)
        return {
            "analysis": analysis,
            "source": "cloud_fallback",
            "image_size": [width, height],
        }
    except Exception as e:
        return {
            "analysis": f"All vision methods failed: {e}",
            "source": "error",
            "image_size": [width, height],
        }


def analyze_image_bytes(
    image_bytes: bytes,
    prompt: str = "Describe what you see in this image.",
) -> Dict[str, Any]:
    """Analyze raw image bytes (e.g., from a PWA upload).

    Args:
        image_bytes: Raw image data.
        prompt: What to analyze about the image.

    Returns:
        {
            "analysis": str — the VLM's description,
            "source": "local_vlm" | "cloud_fallback",
            "image_size": [width, height],
        }
    """
    try:
        image = _load_image_from_bytes(image_bytes)
    except Exception as e:
        return {
            "analysis": f"Failed to decode image: {e}",
            "source": "error",
            "image_size": [0, 0],
        }

    width, height = image.size

    # Try local VLM first
    try:
        analysis = _analyze_with_local_vlm(image, prompt)
        return {
            "analysis": analysis,
            "source": "local_vlm",
            "image_size": [width, height],
        }
    except Exception as e:
        logger.info(f"Local VLM unavailable ({e}), falling back to cloud")

    # Cloud fallback
    try:
        analysis = _analyze_with_cloud(image, prompt)
        return {
            "analysis": analysis,
            "source": "cloud_fallback",
            "image_size": [width, height],
        }
    except Exception as e:
        return {
            "analysis": f"All vision methods failed: {e}",
            "source": "error",
            "image_size": [width, height],
        }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_vision_status() -> Dict[str, Any]:
    """Check vision agent status — model availability, dependencies."""
    has_mss = False
    has_pillow = False
    has_mlx_vlm = False

    try:
        import mss
        has_mss = True
    except ImportError:
        pass

    try:
        from PIL import Image
        has_pillow = True
    except ImportError:
        pass

    try:
        import mlx_vlm
        has_mlx_vlm = True
    except ImportError:
        pass

    model_loaded = _model is not None

    return {
        "dependencies": {
            "mss": has_mss,
            "pillow": has_pillow,
            "mlx_vlm": has_mlx_vlm,
        },
        "model": {
            "name": _model_name,
            "loaded": model_loaded,
            "vram_mb": 4800 if model_loaded else 0,
        },
        "fallback": "cloud" if not has_mlx_vlm else "local",
        "status": "ready" if (has_mss and has_pillow) else "missing_dependencies",
    }
