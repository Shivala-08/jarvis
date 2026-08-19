import os
import httpx
import toml
from pathlib import Path

def run_model_diagnostics() -> dict:
    """Run diagnostics on Ollama and configured models.

    Returns:
        Dict containing diagnostic status, configured model, Ollama status,
        existence of local models, and fallback suggestions.
    """
    report = {
        "ollama_reachable": False,
        "configured_model": "qwen3.5:9b",
        "model_exists_locally": False,
        "available_local_models": [],
        "fallback_model": None,
        "verdict": "healthy",
        "warnings": []
    }
    
    # 1. Load config
    config_path = Path("config/config.toml")
    config = {}
    if config_path.exists():
        try:
            config = toml.load(str(config_path))
        except Exception as e:
            report["warnings"].append(f"Failed to load config.toml: {e}")
            
    # Get configured model from config or environment variable override
    model_override = os.environ.get("JARVIS_MODEL")
    configured_model = model_override or config.get("engine", {}).get("ollama", {}).get("default_model", "qwen3.5:9b")
    report["configured_model"] = configured_model
    
    # 2. Check Ollama reachability
    ollama_url = config.get("engine", {}).get("ollama", {}).get("base_url", "http://localhost:11434")
    
    try:
        # Check /api/tags endpoint of Ollama
        res = httpx.get(f"{ollama_url}/api/tags", timeout=3.0)
        if res.status_code == 200:
            report["ollama_reachable"] = True
            tags_data = res.json()
            models_list = [m["name"] for m in tags_data.get("models", [])]
            report["available_local_models"] = models_list
            
            # Check if configured model exists locally
            # Match both with/without tag (e.g. qwen3.5:9b or qwen3.5:9b:latest)
            clean_model = configured_model.split(":")[0] if ":" in configured_model else configured_model
            
            exists = False
            for m in models_list:
                m_clean = m.split(":")[0] if ":" in m else m
                if m == configured_model or m_clean == clean_model:
                    exists = True
                    # Update report with exact matched tag
                    report["configured_model"] = m
                    break
                    
            report["model_exists_locally"] = exists
            
            if not exists:
                report["verdict"] = "warning"
                report["warnings"].append(
                    f"Configured model '{configured_model}' is not pulled on Ollama."
                )
                
                # Pick a fallback from what is actually installed
                fallback_options = ["llama3.1:latest", "llama3.1:8b", "llama3.1", "qwen3.5:9b", "qwen:latest", "mistral"]
                for option in fallback_options:
                    opt_clean = option.split(":")[0] if ":" in option else option
                    for m in models_list:
                        m_clean = m.split(":")[0] if ":" in m else m
                        if m == option or m_clean == opt_clean:
                            report["fallback_model"] = m
                            break
                    if report["fallback_model"]:
                        break
                        
                if not report["fallback_model"] and models_list:
                    # Just fallback to the first available model
                    report["fallback_model"] = models_list[0]
                    
                if report["fallback_model"]:
                    report["warnings"].append(
                        f"Will automatically fallback to local model '{report['fallback_model']}' for this session."
                    )
                else:
                    report["verdict"] = "critical"
                    report["warnings"].append(
                        "Ollama has NO local models installed. Please run 'ollama pull qwen3.5:9b' in your terminal."
                    )
        else:
            report["verdict"] = "critical"
            report["warnings"].append(f"Ollama returned unexpected HTTP status: {res.status_code}")
            
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        report["verdict"] = "critical"
        report["warnings"].append(f"Could not connect to Ollama at {ollama_url}. Is Ollama running?")
        
    return report

def print_diagnostics_report():
    """Run and print model diagnostics to console during startup."""
    report = run_model_diagnostics()
    
    print("=" * 60)
    print("🩺 LOCAL AI CONFIGURATION DIAGNOSTICS")
    print("=" * 60)
    
    status_symbol = "🟢" if report["verdict"] == "healthy" else "🟡" if report["verdict"] == "warning" else "🔴"
    print(f"  Status Check: {status_symbol} {report['verdict'].upper()}")
    print(f"  Ollama Reachable: {'✅ Yes' if report['ollama_reachable'] else '❌ No'}")
    print(f"  Configured Model: '{report['configured_model']}'")
    print(f"  Model Pulled Locally: {'✅ Yes' if report['model_exists_locally'] else '❌ No'}")
    
    if report["available_local_models"]:
        print(f"  Available Local Models: {', '.join(report['available_local_models'])}")
        
    if report["fallback_model"] and not report["model_exists_locally"]:
        print(f"  Fallback Strategy: ⚠️ Using local fallback '{report['fallback_model']}'")
        
    if report["warnings"]:
        print("\n  Warnings/Errors:")
        for warning in report["warnings"]:
            print(f"    - {warning}")
            
    print("=" * 60)
    print()
    return report
