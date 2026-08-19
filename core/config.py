import os
import toml
from pathlib import Path

# Load config.toml once on module import
CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.toml"
try:
    if CONFIG_PATH.exists():
        CONFIG = toml.load(str(CONFIG_PATH))
    else:
        CONFIG = {}
except Exception:
    CONFIG = {}

def get_config() -> dict:
    return CONFIG

def get_default_model() -> str:
    """Get the configured default Ollama model name, supporting environment variable overrides."""
    # Check env override first
    env_model = os.environ.get("JARVIS_MODEL")
    if env_model:
        return env_model
        
    # Check config
    return CONFIG.get("engine", {}).get("ollama", {}).get("default_model", "qwen3.5:9b")

def get_reasoning_model() -> str:
    """Get the configured reasoning Ollama model name, supporting env overrides."""
    env_model = os.environ.get("JARVIS_REASONING_MODEL") or os.environ.get("JARVIS_MODEL")
    if env_model:
        return env_model
        
    return CONFIG.get("engine", {}).get("ollama", {}).get("models", {}).get("reasoning", "qwen3.5:9b")

def get_coding_model() -> str:
    """Get the configured coding Ollama model name, supporting env overrides."""
    env_model = os.environ.get("JARVIS_CODING_MODEL") or os.environ.get("JARVIS_MODEL")
    if env_model:
        return env_model
        
    return CONFIG.get("engine", {}).get("ollama", {}).get("models", {}).get("coding", "qwen3.5:9b")
