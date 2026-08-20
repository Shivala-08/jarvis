"""Web Task Agent — drives real web interactions using Scrapling.

Features:
- Adaptive web scraping that survives website changes
- Stealthy fetcher bypasses Cloudflare and anti-bot systems
- Session management for multi-step web tasks
- Local LLM-powered task planning and result synthesis
- All web interactions logged for review

Usage:
    from agents.web_task_agent import WebTaskAgent
    agent = WebTaskAgent()
    result = agent.execute("Find the top 3 Python web frameworks by GitHub stars")
    result = agent.scrape("https://example.com", selector=".product-title")
    result = agent.search("latest AI research papers on arxiv")
"""
import json
import logging
import time
from datetime import datetime, timezone
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

from core.config import get_default_model
from core.escalation import llm_call

logger = logging.getLogger(__name__)
DEFAULT_MODEL = get_default_model()

# Log directory for web task results
LOG_DIR = Path("data/web_tasks")
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Scrapling wrappers
# ---------------------------------------------------------------------------

def _get_fetcher(stealthy: bool = True):
    """Get the appropriate Scrapling fetcher."""
    try:
        if stealthy:
            from scrapling.fetchers import StealthyFetcher
            return StealthyFetcher
        else:
            from scrapling.fetchers import Fetcher
            return Fetcher
    except ImportError:
        return None


def _fetch_page(url: str, stealthy: bool = True, headless: bool = True) -> Any:
    """Fetch a page using Scrapling."""
    fetcher = _get_fetcher(stealthy)
    if fetcher is None:
        raise ImportError("Scrapling not installed. Install with: pip install scrapling")

    if stealthy:
        return fetcher.fetch(url, headless=headless, network_idle=True)
    else:
        return fetcher.get(url)


def _extract_text(page, selector: Optional[str] = None) -> str:
    """Extract text content from a page."""
    if selector:
        elements = page.css(selector)
        return "\n".join(el.text for el in elements if el.text)
    return page.text or ""


def _extract_links(page, selector: Optional[str] = None) -> List[Dict[str, str]]:
    """Extract links from a page."""
    if selector:
        elements = page.css(selector)
    else:
        elements = page.css("a")

    links = []
    for el in elements:
        href = el.attrib.get("href", "")
        text = el.text or ""
        if href and text:
            links.append({"url": href, "text": text.strip()})
    return links


def _extract_structured(page, schema: Dict[str, str]) -> List[Dict[str, str]]:
    """Extract structured data from a page using CSS selectors.

    schema: {"field_name": "css_selector", ...}
    Returns list of dicts, one per matching element.
    """
    results = []

    # Find the common parent or iterate per field
    first_selector = next(iter(schema.values()), None)
    if not first_selector:
        return results

    elements = page.css(first_selector)
    for el in elements:
        row = {}
        for field, sel in schema.items():
            # Try child selector first, then the element itself
            child = el.css(sel)
            if child:
                row[field] = child[0].text.strip() if child[0].text else ""
            else:
                row[field] = el.text.strip() if el.text else ""
        results.append(row)

    return results


# ---------------------------------------------------------------------------
# Task planning with LLM
# ---------------------------------------------------------------------------

TASK_PLANNER_PROMPT = """\
You are a web task planner. Given a natural language task, break it into
concrete web actions that can be executed with a web scraper.

Output ONLY valid JSON matching this schema:
{
  "task_summary": "<one-line summary>",
  "steps": [
    {
      "step_id": 1,
      "action": "fetch | search | extract | click | scroll",
      "target": "<URL or search query>",
      "selector": "<CSS selector for extraction, if applicable>",
      "description": "<what this step does>"
    }
  ],
  "output_format": "text | structured | links",
  "extraction_schema": {"field": "selector"} or null
}

Rules:
- Keep steps minimal (≤ 5 per task).
- Prefer direct URLs over searches when possible.
- Use simple CSS selectors.
- For search tasks, use the search engine URL pattern.
- Return ONLY JSON, no markdown fences.
"""


def _plan_task(task_description: str, model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """Use LLM to plan a web task."""
    if not HAS_OLLAMA:
        return {
            "task_summary": task_description,
            "steps": [
                {
                    "step_id": 1,
                    "action": "fetch",
                    "target": task_description,
                    "selector": None,
                    "description": "Fetch the URL",
                }
            ],
            "output_format": "text",
            "extraction_schema": None,
        }

    try:
        content = llm_call(
            prompt=task_description,
            system_prompt=TASK_PLANNER_PROMPT,
            task_type="web_task",
            model=model,
            temperature=0.2,
        )

        # Strip markdown fences
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            content = content.rsplit("```", 1)[0].strip()

        return json.loads(content)

    except Exception as e:
        logger.warning(f"Task planning LLM call failed: {e}")
        # Fallback plan
        return {
            "task_summary": task_description,
            "steps": [
                {
                    "step_id": 1,
                    "action": "fetch",
                    "target": task_description,
                    "selector": None,
                    "description": f"Fallback: {e}",
                }
            ],
            "output_format": "text",
            "extraction_schema": None,
        }


# ---------------------------------------------------------------------------
# Result synthesis with LLM
# ---------------------------------------------------------------------------

SYNTHESIS_PROMPT = """\
You are a web research assistant. Given raw web scraping results,
synthesize a clear, concise answer to the user's original question.

Rules:
- Be factual and cite sources (URLs) when available.
- Keep the answer under 200 words.
- Use bullet points for lists.
- If the data is insufficient, say so clearly.
- Return plain text, no JSON needed.
"""


def _synthesize_results(task: str, raw_results: List[Dict[str, Any]], model: str = DEFAULT_MODEL) -> str:
    """Use LLM to synthesize web results into a coherent answer."""
    if not HAS_OLLAMA:
        parts = []
        for r in raw_results:
            if r.get("text"):
                parts.append(r["text"][:500])
        return "\n\n".join(parts) or "No results found."

    # Build context from results
    context_parts = []
    for i, r in enumerate(raw_results[:10]):  # Limit to 10 results
        url = r.get("url", "unknown")
        text = r.get("text", "")[:1000]
        context_parts.append(f"[{i+1}] {url}\n{text}")

    context = "\n\n".join(context_parts)

    try:
        return llm_call(
            prompt=f"Task: {task}\n\nResults:\n{context}",
            system_prompt=SYNTHESIS_PROMPT,
            task_type="web_task",
            model=model,
            temperature=0.3,
        )

    except Exception as e:
        logger.warning(f"Result synthesis LLM call failed: {e}")
        return f"Synthesis failed: {e}\n\nRaw results:\n{context[:2000]}"


# ---------------------------------------------------------------------------
# Web Task Agent
# ---------------------------------------------------------------------------

class WebTaskAgent:
    """Drives real web interactions using Scrapling.

    Supports:
    - Direct URL scraping with adaptive selectors
    - Search engine queries
    - Multi-step web tasks planned by LLM
    - Stealthy fetching to bypass anti-bot systems
    """

    def __init__(self, model: str = DEFAULT_MODEL, stealthy: bool = True):
        self.model = model
        self.stealthy = stealthy
        self._task_count = 0

    def execute(self, task_description: str) -> Dict[str, Any]:
        """Execute a natural language web task.

        Args:
            task_description: What to do on the web (e.g., "Find the top 3 Python web frameworks")

        Returns:
            Dict with task_summary, plan, results, synthesis
        """
        start_time = time.time()
        self._task_count += 1

        # Plan the task
        plan = _plan_task(task_description, self.model)

        # Execute each step
        raw_results = []
        for step in plan.get("steps", []):
            try:
                result = self._execute_step(step)
                raw_results.append(result)
            except Exception as e:
                raw_results.append({
                    "step": step.get("step_id"),
                    "error": str(e),
                    "text": "",
                })

        # Synthesize results
        synthesis = _synthesize_results(task_description, raw_results, self.model)

        elapsed = time.time() - start_time

        # Log the task
        task_log = {
            "task": task_description,
            "plan": plan,
            "results_count": len(raw_results),
            "synthesis": synthesis,
            "elapsed_seconds": round(elapsed, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._log_task(task_log)

        return {
            "task_summary": plan.get("task_summary", task_description),
            "steps_executed": len(plan.get("steps", [])),
            "results": raw_results,
            "synthesis": synthesis,
            "elapsed_seconds": round(elapsed, 2),
        }

    def scrape(self, url: str, selector: Optional[str] = None) -> Dict[str, Any]:
        """Scrape a specific URL.

        Args:
            url: URL to scrape
            selector: Optional CSS selector to extract specific elements

        Returns:
            Dict with url, text, links, structured_data
        """
        start_time = time.time()

        try:
            page = _fetch_page(url, stealthy=self.stealthy)
            text = _extract_text(page, selector)
            links = _extract_links(page)

            elapsed = time.time() - start_time

            return {
                "url": url,
                "text": text[:5000],  # Truncate for LLM context
                "text_length": len(text),
                "links": links[:50],
                "elapsed_seconds": round(elapsed, 2),
                "status": "success",
            }

        except Exception as e:
            elapsed = time.time() - start_time
            return {
                "url": url,
                "text": "",
                "error": str(e),
                "elapsed_seconds": round(elapsed, 2),
                "status": "error",
            }

    def search(self, query: str, engine: str = "google") -> Dict[str, Any]:
        """Search using a search engine.

        Args:
            query: Search query
            engine: Search engine to use (google, duckduckgo)

        Returns:
            Dict with query, results, synthesis
        """
        # Build search URL
        if engine == "duckduckgo":
            search_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        else:
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"

        # Fetch search results
        try:
            page = _fetch_page(search_url, stealthy=self.stealthy)

            # Extract search results
            results = []
            # Google search result selectors
            for el in page.css("div.g, div[data-sokoban-container]"):
                title_el = el.css("h3")
                link_el = el.css("a")
                snippet_el = el.css("div[data-sncf], span[class='st']")

                if title_el and link_el:
                    results.append({
                        "title": title_el[0].text if title_el[0].text else "",
                        "url": link_el[0].attrib.get("href", ""),
                        "snippet": snippet_el[0].text if snippet_el and snippet_el[0].text else "",
                    })

            # If no structured results, fall back to text extraction
            if not results:
                text = _extract_text(page)
                return {
                    "query": query,
                    "results": [],
                    "raw_text": text[:3000],
                    "status": "partial",
                }

            return {
                "query": query,
                "results": results[:10],
                "status": "success",
            }

        except Exception as e:
            return {
                "query": query,
                "results": [],
                "error": str(e),
                "status": "error",
            }

    def extract_table(self, url: str, row_selector: str, schema: Dict[str, str]) -> List[Dict[str, str]]:
        """Extract structured table data from a page.

        Args:
            url: URL to scrape
            row_selector: CSS selector for table rows
            schema: Mapping of field names to CSS selectors within each row

        Returns:
            List of dicts with extracted data
        """
        try:
            page = _fetch_page(url, stealthy=self.stealthy)
            return _extract_structured(page.css(row_selector)[0] if page.css(row_selector) else page, schema)
        except Exception as e:
            return [{"error": str(e)}]

    def _execute_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single step from a task plan."""
        action = step.get("action", "fetch")
        target = step.get("target", "")
        selector = step.get("selector")

        if action == "fetch" or action == "click":
            return self.scrape(target, selector)
        elif action == "search":
            result = self.search(target)
            return {
                "text": json.dumps(result.get("results", []), indent=2),
                "url": f"search://{target}",
            }
        elif action == "extract":
            return self.scrape(target, selector)
        else:
            return {"text": f"Unknown action: {action}", "error": f"Unsupported action: {action}"}

    def _log_task(self, task_log: Dict[str, Any]) -> None:
        """Log a completed task."""
        try:
            log_file = LOG_DIR / f"task_{self._task_count:04d}.json"
            with open(log_file, "w") as f:
                json.dump(task_log, f, indent=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Convenience functions for skill registration
# ---------------------------------------------------------------------------

def web_search(query: str) -> str:
    """Search the web and return synthesized results."""
    agent = WebTaskAgent()
    result = agent.search(query)
    if result.get("results"):
        parts = []
        for r in result["results"][:5]:
            parts.append(f"- {r.get('title', '')}: {r.get('url', '')}")
        return "\n".join(parts)
    return result.get("error", "No results found")


def web_scrape(url: str, selector: Optional[str] = None) -> str:
    """Scrape a URL and return extracted text."""
    agent = WebTaskAgent()
    result = agent.scrape(url, selector)
    if result.get("status") == "success":
        return result.get("text", "")[:3000]
    return result.get("error", "Scraping failed")


def web_task(task_description: str) -> str:
    """Execute a natural language web task."""
    agent = WebTaskAgent()
    result = agent.execute(task_description)
    return result.get("synthesis", "Task completed but no synthesis available.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    agent = WebTaskAgent()

    if len(sys.argv) > 1:
        command = " ".join(sys.argv[1:])

        # Detect intent
        cmd_lower = command.lower()
        if cmd_lower.startswith("http"):
            # Direct URL scrape
            result = agent.scrape(command)
            print(json.dumps(result, indent=2))
        elif "search" in cmd_lower or "find" in cmd_lower or "look up" in cmd_lower:
            # Search
            query = command.replace("search", "").replace("find", "").replace("look up", "").strip()
            result = agent.search(query)
            print(json.dumps(result, indent=2))
        else:
            # General task
            result = agent.execute(command)
            print(f"\n📋 Task: {result['task_summary']}")
            print(f"⏱  Completed in {result['elapsed_seconds']}s")
            print(f"\n📝 Results:\n{result['synthesis']}")
    else:
        print("🌐 Web Task Agent — CLI Mode")
        print("  Usage:")
        print('    python -m agents.web_task_agent "search for Python tutorials"')
        print('    python -m agents.web_task_agent https://example.com')
        print('    python -m agents.web_task_agent "find the top 3 GitHub repos for ML"')
