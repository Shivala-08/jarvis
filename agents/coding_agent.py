"""Coding Agent — wraps Aider for local coding assistance via Ollama.

Features:
- Natural language → code changes via Aider + local Ollama
- File-aware: reads project context before suggesting changes
- Voice-friendly: accepts spoken commands, returns diffs + summaries
- All inference local — zero cloud calls

Usage:
    from agents.coding_agent import CodeAssistant
    assistant = CodeAssistant()
    result = assistant.fix_bug("main.py has an off-by-one error in the login loop")
    result = assistant.add_feature("Add a dark mode toggle to the settings page")
    result = assistant.explain("What does the scheduler_agent.py rebalance function do?")
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

try:
    import toml
    CONFIG = toml.load("config/config.toml")
except (FileNotFoundError, Exception):
    CONFIG = {}

from core.config import get_coding_model
DEFAULT_MODEL = get_coding_model()

# Project root for file context
PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Code analysis helpers
# ---------------------------------------------------------------------------

def _safe_resolve(file_path: str) -> Path:
    """Resolve the path safely, preventing traversal and symlink escapes outside PROJECT_ROOT."""
    path_obj = Path(file_path)
    if path_obj.is_absolute():
        raise ValueError(f"Absolute paths not allowed: {file_path}")
    
    # Check for direct traversal patterns in string to fail fast
    normalized_str = str(path_obj)
    if ".." in normalized_str.split(os.sep):
        raise ValueError(f"Directory traversal patterns not allowed: {file_path}")
        
    combined = PROJECT_ROOT / path_obj
    resolved_path = combined.resolve()
    
    if not resolved_path.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError(f"Unsafe path traversal detected: {file_path}")
        
    return resolved_path


def _read_file(path: str, max_lines: int = 500) -> str:
    """Read a file and return its content, truncated to max_lines."""
    try:
        file_path = _safe_resolve(path)
        if not file_path.exists():
            return f"[File not found: {path}]"
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n... [{len(lines) - max_lines} more lines]"
        return "\n".join(lines)
    except Exception as e:
        return f"[Error reading {path}: {e}]"


def _list_project_files(ext: str = ".py", max_files: int = 50) -> List[str]:
    """List Python files in the project."""
    files = []
    for f in PROJECT_ROOT.rglob(f"*{ext}"):
        # Skip hidden dirs, __pycache__, .venv
        rel = f.relative_to(PROJECT_ROOT)
        parts = rel.parts
        if any(p.startswith(".") or p == "__pycache__" or p == ".venv" for p in parts):
            continue
        files.append(str(rel))
        if len(files) >= max_files:
            break
    return sorted(files)


def _get_project_context(file_path: Optional[str] = None, extra_files: Optional[List[str]] = None) -> str:
    """Build project context for the LLM."""
    context_parts = []

    # List project structure
    files = _list_project_files()
    context_parts.append("Project files:\n" + "\n".join(f"  {f}" for f in files))

    # Read the target file if specified
    if file_path:
        content = _read_file(file_path)
        context_parts.append(f"\n--- {file_path} ---\n{content}")

    # Read extra context files
    for extra in (extra_files or []):
        content = _read_file(extra)
        context_parts.append(f"\n--- {extra} ---\n{content}")

    return "\n".join(context_parts)


# ---------------------------------------------------------------------------
# Coding Assistant
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a coding assistant for a Python project (ADHD Co-Processor).
You have access to the project's file structure and can read files.
Given a coding request, produce a clear, actionable response.

Rules:
- Return ONLY valid JSON matching the schema below.
- Be specific: reference exact file paths, function names, line numbers.
- For code changes, provide the complete diff or replacement code.
- Keep explanations concise (≤ 3 sentences per change).
- Never hallucinate file contents — work only with what you can see.

Output schema:
{
  "action": "fix | add_feature | explain | refactor | test",
  "summary": "<1-2 sentence summary of what you're doing>",
  "files_changed": [
    {
      "path": "<relative file path>",
      "changes": "<description of changes>",
      "old_code": "<exact old code to replace, if applicable>",
      "new_code": "<replacement code, if applicable>"
    }
  ],
  "explanation": "<detailed explanation of the changes>",
  "confidence": "high | medium | low",
  "warnings": ["<any warnings or caveats>"]
}

Return ONLY the JSON object, no markdown fences.
"""


class CodeAssistant:
    """Local coding assistant powered by Aider + Ollama.

    Provides high-level methods for common coding tasks:
    - fix_bug: diagnose and fix a specific bug
    - add_feature: implement a new feature
    - explain: explain how code works
    - refactor: improve code structure
    - review: review code for issues
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._aider_available = self._check_aider()

    def _check_aider(self) -> bool:
        """Check if aider-chat is installed."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "aider", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _query_llm(self, prompt: str, context: str = "") -> Dict[str, Any]:
        """Query the local LLM for a coding response."""
        if not HAS_OLLAMA:
            return {
                "action": "explain",
                "summary": "Ollama not available — cannot process coding request.",
                "files_changed": [],
                "explanation": "Please install and start Ollama to use the coding assistant.",
                "confidence": "low",
                "warnings": ["Ollama not installed or not running"],
            }

        full_prompt = f"{context}\n\nRequest: {prompt}" if context else prompt

        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": full_prompt},
                ],
                options={"temperature": 0.2},
            )
            content = response["message"]["content"].strip()

            # Strip markdown fences
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                content = content.rsplit("```", 1)[0].strip()

            return json.loads(content)

        except json.JSONDecodeError:
            return {
                "action": "explain",
                "summary": "LLM returned non-JSON response.",
                "files_changed": [],
                "explanation": content if 'content' in dir() else "No response",
                "confidence": "low",
                "warnings": ["Failed to parse LLM response as JSON"],
            }
        except Exception as e:
            return {
                "action": "explain",
                "summary": f"LLM query failed: {e}",
                "files_changed": [],
                "explanation": str(e),
                "confidence": "low",
                "warnings": [str(e)],
            }

    def fix_bug(self, description: str, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Diagnose and fix a bug.

        Args:
            description: Natural language description of the bug
            file_path: Optional specific file to look at

        Returns:
            Dict with action, summary, files_changed, explanation
        """
        context = _get_project_context(file_path)
        prompt = f"Fix this bug: {description}"
        if file_path:
            prompt += f"\nFocus on file: {file_path}"

        result = self._query_llm(prompt, context)
        result["action"] = "fix"
        return result

    def add_feature(self, description: str, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Add a new feature.

        Args:
            description: Natural language description of the feature
            file_path: Optional specific file to modify

        Returns:
            Dict with action, summary, files_changed, explanation
        """
        context = _get_project_context(file_path)
        prompt = f"Add this feature: {description}"
        if file_path:
            prompt += f"\nImplement in file: {file_path}"

        result = self._query_llm(prompt, context)
        result["action"] = "add_feature"
        return result

    def explain(self, query: str, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Explain how code works.

        Args:
            query: What to explain
            file_path: Optional specific file to explain

        Returns:
            Dict with action, summary, explanation
        """
        context = _get_project_context(file_path)
        prompt = f"Explain: {query}"
        if file_path:
            prompt += f"\nFocus on file: {file_path}"

        result = self._query_llm(prompt, context)
        result["action"] = "explain"
        return result

    def refactor(self, description: str, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Refactor code.

        Args:
            description: What to refactor and why
            file_path: Optional specific file to refactor

        Returns:
            Dict with action, summary, files_changed, explanation
        """
        context = _get_project_context(file_path)
        prompt = f"Refactor: {description}"
        if file_path:
            prompt += f"\nRefactor file: {file_path}"

        result = self._query_llm(prompt, context)
        result["action"] = "refactor"
        return result

    def review(self, file_path: str) -> Dict[str, Any]:
        """Review code for issues.

        Args:
            file_path: File to review

        Returns:
            Dict with action, summary, issues found, suggestions
        """
        context = _get_project_context(file_path)
        prompt = f"Review the code in {file_path} for bugs, style issues, and improvements."

        result = self._query_llm(prompt, context)
        result["action"] = "review"
        return result

    def apply_changes(self, result: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
        """Apply code changes from a coding assistant result.

        Args:
            result: Result from fix_bug/add_feature/refactor
            dry_run: If True, only show what would change (don't write files)

        Returns:
            Dict with application status
        """
        changes = result.get("files_changed", [])
        if not changes:
            return {"applied": 0, "message": "No changes to apply"}

        applied = []
        errors = []
        for change in changes:
            file_path = change.get("path", "")
            old_code = change.get("old_code", "")
            new_code = change.get("new_code", "")

            if not file_path or not new_code:
                continue

            try:
                full_path = _safe_resolve(file_path)
            except ValueError as e:
                errors.append({
                    "file": file_path,
                    "error": str(e),
                })
                continue

            if dry_run:
                applied.append({
                    "file": file_path,
                    "status": "dry_run",
                    "description": change.get("changes", ""),
                })
                continue

            try:
                if old_code and full_path.exists():
                    # Replace specific code block
                    content = full_path.read_text(encoding="utf-8")
                    if old_code in content:
                        content = content.replace(old_code, new_code, 1)
                        full_path.write_text(content, encoding="utf-8")
                        applied.append({"file": file_path, "status": "applied"})
                    else:
                        errors.append({
                            "file": file_path,
                            "error": "Old code not found in file",
                        })
                else:
                    # Write new file or append
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(new_code, encoding="utf-8")
                    applied.append({"file": file_path, "status": "applied"})

            except Exception as e:
                errors.append({"file": file_path, "error": str(e)})

        return {
            "applied": len(applied),
            "errors": len(errors),
            "details": applied,
            "error_details": errors,
        }

    def with_aider(self, instructions: str, files: Optional[List[str]] = None) -> Dict[str, Any]:
        """Use Aider directly for code changes (if available).

        Args:
            instructions: Natural language instructions for Aider
            files: Optional list of files for Aider to work on

        Returns:
            Dict with aider output
        """
        if not self._aider_available:
            return {
                "success": False,
                "error": "Aider not installed. Install with: pip install aider-chat",
                "fallback": "Use fix_bug() or add_feature() for LLM-based suggestions.",
            }

        cmd = [
            sys.executable, "-m", "aider",
            "--model", f"ollama/{self.model}",
            "--no-git",
            "--message", instructions,
        ]

        if files:
            cmd.extend(files)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=120,
                cwd=str(PROJECT_ROOT),
            )
            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "errors": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Aider timed out after 120 seconds",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    assistant = CodeAssistant()

    if len(sys.argv) > 1:
        command = " ".join(sys.argv[1:])

        # Detect intent
        cmd_lower = command.lower()
        if "fix" in cmd_lower or "bug" in cmd_lower:
            result = assistant.fix_bug(command)
        elif "add" in cmd_lower or "feature" in cmd_lower or "create" in cmd_lower:
            result = assistant.add_feature(command)
        elif "explain" in cmd_lower or "what" in cmd_lower or "how" in cmd_lower:
            result = assistant.explain(command)
        elif "refactor" in cmd_lower:
            result = assistant.refactor(command)
        elif "review" in cmd_lower:
            # Find a file path in the command
            words = command.split()
            file_path = None
            for w in words:
                if ".py" in w:
                    file_path = w
                    break
            result = assistant.review(file_path or "main.py")
        else:
            result = assistant.explain(command)

        print(json.dumps(result, indent=2))
    else:
        print("🧠 Coding Assistant — CLI Mode")
        print("  Usage: python -m agents.coding_agent <instruction>")
        print("  Examples:")
        print('    python -m agents.coding_agent "fix the off-by-one in login.py"')
        print('    python -m agents.coding_agent "add dark mode to settings"')
        print('    python -m agents.coding_agent "explain the scheduler rebalance"')
        print('    python -m agents.coding_agent "review main.py"')
