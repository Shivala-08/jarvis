"""Skill Manager — composable agent capabilities.

Inspired by OpenJarvis's skill system that allows agents to discover
and use capabilities dynamically. Skills are reusable compositions of
tools and instructions that can be shared and optimized.

Features:
- Skill discovery and registration
- Tool wrapping for agent invocation
- Skill composition (skills calling other skills)
- Trace-based skill optimization
- Performance benchmarking
"""
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.event_bus import EventType, publish

import logging

logger = logging.getLogger(__name__)


class SkillType(Enum):
    """Types of skills."""
    TOOL = "tool"           # Wraps a tool/function
    INSTRUCTION = "instruction"  # Provides markdown instructions
    PIPELINE = "pipeline"   # Executes a sequence of steps


@dataclass
class SkillStep:
    """A step in a skill pipeline."""
    tool_name: Optional[str] = None
    skill_name: Optional[str] = None
    arguments_template: str = "{}"
    output_key: str = "result"


@dataclass
class Skill:
    """A composable agent capability."""
    name: str
    description: str
    skill_type: SkillType
    version: str = "0.1.0"
    author: str = "adhd-copilot"
    tags: List[str] = field(default_factory=list)
    
    # For TOOL skills
    tool_function: Optional[Callable] = None
    
    # For INSTRUCTION skills
    markdown_content: Optional[str] = None
    
    # For PIPELINE skills
    steps: List[SkillStep] = field(default_factory=list)
    
    # Metadata
    required_capabilities: List[str] = field(default_factory=list)
    depends: List[str] = field(default_factory=list)
    
    # Performance tracking
    invocation_count: int = 0
    total_duration_ms: float = 0
    success_count: int = 0
    error_count: int = 0
    
    def invoke(self, **kwargs) -> Any:
        """Invoke the skill with given arguments."""
        start_time = time.time()
        
        try:
            if self.skill_type == SkillType.TOOL and self.tool_function:
                result = self.tool_function(**kwargs)
                self.invocation_count += 1
                self.success_count += 1
                self.total_duration_ms += (time.time() - start_time) * 1000
                return result
            elif self.skill_type == SkillType.INSTRUCTION:
                # Return markdown content for the agent to follow
                self.invocation_count += 1
                self.success_count += 1
                self.total_duration_ms += (time.time() - start_time) * 1000
                return self.markdown_content
            elif self.skill_type == SkillType.PIPELINE:
                # Execute pipeline steps
                result = self._execute_pipeline(kwargs)
                self.invocation_count += 1
                self.success_count += 1
                self.total_duration_ms += (time.time() - start_time) * 1000
                return result
            else:
                raise ValueError(f"Invalid skill type or missing implementation: {self.skill_type}")
        
        except Exception as e:
            self.invocation_count += 1
            self.error_count += 1
            self.total_duration_ms += (time.time() - start_time) * 1000
            raise
    
    def _execute_pipeline(self, initial_args: Dict[str, Any]) -> Any:
        """Execute a pipeline skill's steps."""
        context = dict(initial_args)
        
        for step in self.steps:
            if step.tool_name:
                # Get tool function
                tool_func = SkillManager.get_tool(step.tool_name)
                if tool_func is None:
                    raise ValueError(f"Tool not found: {step.tool_name}")
                
                # Format arguments from template
                args = self._format_template(step.arguments_template, context)
                
                # Execute tool
                result = tool_func(**args)
                
                # Store result in context
                context[step.output_key] = result
            
            elif step.skill_name:
                # Get sub-skill
                sub_skill = SkillManager.get_skill(step.skill_name)
                if sub_skill is None:
                    raise ValueError(f"Skill not found: {step.skill_name}")
                
                # Format arguments from template
                args = self._format_template(step.arguments_template, context)
                
                # Execute sub-skill
                result = sub_skill.invoke(**args)
                
                # Store result in context
                context[step.output_key] = result
        
        return context
    
    def _format_template(self, template: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Format an arguments template with context values."""
        try:
            # Simple template: {"key": "{context_key}"}
            formatted = template
            for key, value in context.items():
                formatted = formatted.replace(f"{{{key}}}", str(value))
            return json.loads(formatted)
        except json.JSONDecodeError:
            # Fallback: return template as-is
            return json.loads(template)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get skill performance statistics."""
        avg_duration = (
            self.total_duration_ms / self.invocation_count
            if self.invocation_count > 0
            else 0
        )
        return {
            "name": self.name,
            "type": self.skill_type.value,
            "invocations": self.invocation_count,
            "successes": self.success_count,
            "errors": self.error_count,
            "avg_duration_ms": round(avg_duration, 2),
            "success_rate": (
                self.success_count / self.invocation_count * 100
                if self.invocation_count > 0
                else 0
            ),
        }


class SkillManager:
    """Central manager for skill discovery, registration, and invocation."""
    
    _skills: Dict[str, Skill] = {}
    _tools: Dict[str, Callable] = {}
    _lock = None  # Will be initialized lazily
    
    @classmethod
    def _get_lock(cls):
        if cls._lock is None:
            import threading
            cls._lock = threading.Lock()
        return cls._lock
    
    @classmethod
    def register_skill(cls, skill: Skill) -> None:
        """Register a skill."""
        with cls._get_lock():
            cls._skills[skill.name] = skill
            logger.debug(f"Registered skill: {skill.name}")
    
    @classmethod
    def get_skill(cls, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return cls._skills.get(name)
    
    @classmethod
    def list_skills(cls, skill_type: Optional[SkillType] = None) -> List[Skill]:
        """List all registered skills, optionally filtered by type."""
        skills = list(cls._skills.values())
        if skill_type:
            skills = [s for s in skills if s.skill_type == skill_type]
        return skills
    
    @classmethod
    def register_tool(cls, name: str, tool: Callable) -> None:
        """Register a tool for pipeline skills."""
        with cls._get_lock():
            cls._tools[name] = tool
            logger.debug(f"Registered tool: {name}")
    
    @classmethod
    def get_tool(cls, name: str) -> Optional[Callable]:
        """Get a tool by name."""
        return cls._tools.get(name)
    
    @classmethod
    def invoke_skill(cls, name: str, **kwargs) -> Any:
        """Invoke a skill by name."""
        skill = cls.get_skill(name)
        if skill is None:
            raise ValueError(f"Skill not found: {name}")
        
        result = skill.invoke(**kwargs)
        
        # Publish event
        publish(
            EventType.SPRINT_COMPLETED,
            {"skill_name": name, "success": True},
            source="skill_manager",
        )
        
        return result
    
    @classmethod
    def get_skill_catalog(cls) -> str:
        """Get an XML catalog of available skills for agent system prompts."""
        skills = cls.list_skills()
        
        catalog_lines = ["<available_skills>"]
        for skill in skills:
            catalog_lines.append(
                f'  <skill name="{skill.name}" '
                f'description="{skill.description}" '
                f'type="{skill.skill_type.value}" />'
            )
        catalog_lines.append("</available_skills>")
        
        return "\n".join(catalog_lines)
    
    @classmethod
    def get_stats(cls) -> List[Dict[str, Any]]:
        """Get performance statistics for all skills."""
        return [skill.get_stats() for skill in cls._skills.values()]


# ---------------------------------------------------------------------------
# Built-in ADHD Co-Processor Skills
# ---------------------------------------------------------------------------

def _register_builtin_skills() -> None:
    """Register built-in skills for the ADHD Co-Processor."""
    
    # Brain Dump Processing Skill
    def process_braindump_skill(text: str) -> dict:
        from agents.braindump_agent import process_braindump
        return process_braindump(text)
    
    SkillManager.register_tool("braindump_process", process_braindump_skill)
    SkillManager.register_skill(Skill(
        name="braindump",
        description="Process raw stream-of-consciousness text into structured tasks",
        skill_type=SkillType.TOOL,
        tool_function=process_braindump_skill,
        tags=["braindump", "tasks", "adhd"],
    ))
    
    # Schedule Building Skill
    def build_schedule_skill(tasks: list, alpha: float) -> list:
        from agents.scheduler_agent import build_schedule
        return build_schedule(tasks, alpha)
    
    SkillManager.register_tool("build_schedule", build_schedule_skill)
    SkillManager.register_skill(Skill(
        name="scheduler",
        description="Build time-blocked schedule with scaled durations",
        skill_type=SkillType.TOOL,
        tool_function=build_schedule_skill,
        tags=["schedule", "time-blocking", "adhd"],
    ))
    
    # Study Decomposition Skill
    def decompose_study_skill(topic: str) -> dict:
        from agents.study_agent import decompose_topic
        return decompose_topic(topic)
    
    SkillManager.register_tool("study_decompose", decompose_study_skill)
    SkillManager.register_skill(Skill(
        name="study",
        description="Decompose study topics into micro-units with active recall",
        skill_type=SkillType.TOOL,
        tool_function=decompose_study_skill,
        tags=["study", "learning", "decomposition"],
    ))
    
    # Micro-Sprint Skill
    def generate_sprint_skill(task: str) -> str:
        from agents.scheduler_agent import generate_micro_sprint
        return generate_micro_sprint(task)
    
    SkillManager.register_tool("generate_sprint", generate_sprint_skill)
    SkillManager.register_skill(Skill(
        name="sprint",
        description="Generate calm micro-sprint suggestions for tasks",
        skill_type=SkillType.TOOL,
        tool_function=generate_sprint_skill,
        tags=["sprint", "motivation", "adhd"],
    ))
    
    # Memory Search Skill
    def search_memory_skill(query: str, limit: int = 5) -> list:
        from memory.adhd_memory import ADHDMemoryEngine
        engine = ADHDMemoryEngine()
        return engine.retrieve_context_for_task(query, limit=limit)
    
    SkillManager.register_tool("memory_search", search_memory_skill)
    SkillManager.register_skill(Skill(
        name="memory",
        description="Search semantic memory for relevant past information",
        skill_type=SkillType.TOOL,
        tool_function=search_memory_skill,
        tags=["memory", "search", "context"],
    ))
    
    # Rebalancing Skill (pipeline example)
    SkillManager.register_skill(Skill(
        name="rebalance",
        description="Silently rebalance schedule after missed blocks",
        skill_type=SkillType.PIPELINE,
        steps=[
            SkillStep(
                tool_name="build_schedule",
                arguments_template='{"tasks": "{tasks}", "alpha": "{alpha}"}',
                output_key="schedule",
            ),
            SkillStep(
                tool_name="generate_sprint",
                arguments_template='{"task": "{next_task}"}',
                output_key="suggestion",
            ),
        ],
        tags=["rebalance", "schedule", "resilience"],
    ))
    
    # Coding Assistant Skill (Phase 8)
    def code_fix_skill(description: str, file_path: str = None) -> dict:
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant()
        return assistant.fix_bug(description, file_path)
    
    def code_add_skill(description: str, file_path: str = None) -> dict:
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant()
        return assistant.add_feature(description, file_path)
    
    def code_explain_skill(query: str, file_path: str = None) -> dict:
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant()
        return assistant.explain(query, file_path)
    
    SkillManager.register_tool("code_fix", code_fix_skill)
    SkillManager.register_tool("code_add", code_add_skill)
    SkillManager.register_tool("code_explain", code_explain_skill)
    SkillManager.register_skill(Skill(
        name="coding",
        description="Fix bugs, add features, or explain code via local LLM + Aider",
        skill_type=SkillType.TOOL,
        tool_function=code_fix_skill,
        tags=["coding", "aider", "development"],
    ))
    
    # Web Task Skill (Phase 9 — Scrapling)
    def web_search_skill(query: str) -> str:
        from agents.web_task_agent import web_search
        return web_search(query)
    
    def web_scrape_skill(url: str, selector: str = None) -> str:
        from agents.web_task_agent import web_scrape
        return web_scrape(url, selector)
    
    def web_task_skill(task_description: str) -> str:
        from agents.web_task_agent import web_task
        return web_task(task_description)
    
    SkillManager.register_tool("web_search", web_search_skill)
    SkillManager.register_tool("web_scrape", web_scrape_skill)
    SkillManager.register_tool("web_task", web_task_skill)
    SkillManager.register_skill(Skill(
        name="webtask",
        description="Search, scrape, or complete web tasks using Scrapling",
        skill_type=SkillType.TOOL,
        tool_function=web_task_skill,
        tags=["web", "scraping", "research", "scrapling"],
    ))


# Auto-register builtin skills when module is imported
_register_builtin_skills()
