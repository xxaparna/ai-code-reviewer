"""Agent registry: reviewer agents are data, loaded from ``agents.yaml``."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.errors import UnknownAgentError
from app.models.enums import Category, Severity
from app.prompts.shared import GENERAL_RULES, OUTPUT_FORMAT

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("agents.yaml")


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    category: Category
    prompt: str = Field(min_length=1)
    model_tier: Literal["strong", "fast"] = "strong"
    enabled: bool = True
    severity_floor: Severity = Severity.LOW
    max_findings_per_file: int = Field(default=10, ge=1)
    prompt_version: str = "1"

    @field_validator("severity_floor", mode="before")
    @classmethod
    def _severity(cls, v: object) -> Severity:
        return v if isinstance(v, Severity) else Severity.from_str(str(v))

    @property
    def system_prompt(self) -> str:
        return f"{self.prompt.strip()}\n\n{GENERAL_RULES}\n{OUTPUT_FORMAT}"

    def accepts(self, severity: Severity) -> bool:
        """True when ``severity`` is at or above this agent's floor."""
        return severity.rank <= self.severity_floor.rank


@dataclass(frozen=True, slots=True)
class AgentRegistry:
    agents: tuple[AgentConfig, ...]

    def __post_init__(self) -> None:
        names = [a.name for a in self.agents]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate agent names in registry: {sorted(duplicates)}")

    def __iter__(self) -> Iterator[AgentConfig]:
        return iter(self.agents)

    def __len__(self) -> int:
        return len(self.agents)

    def get(self, name: str) -> AgentConfig:
        for agent in self.agents:
            if agent.name == name:
                return agent
        raise UnknownAgentError(f"Unknown agent {name!r}.")

    @property
    def enabled(self) -> list[AgentConfig]:
        return [a for a in self.agents if a.enabled]

    def select(self, names: Iterable[str] | None = None) -> list[AgentConfig]:
        """The agents to run: every enabled agent, or the requested enabled subset."""
        if names is None:
            return self.enabled
        wanted = list(dict.fromkeys(names))
        if not wanted:
            raise UnknownAgentError("At least one agent must be requested.")
        known = {a.name: a for a in self.agents}
        unknown = [n for n in wanted if n not in known]
        if unknown:
            raise UnknownAgentError(
                f"Unknown agent(s): {', '.join(unknown)}. Available: {', '.join(known)}."
            )
        disabled = [n for n in wanted if not known[n].enabled]
        if disabled:
            raise UnknownAgentError(f"Agent(s) disabled on this server: {', '.join(disabled)}.")
        return [known[n] for n in wanted]

    @classmethod
    def from_yaml(cls, path: Path = DEFAULT_REGISTRY_PATH) -> "AgentRegistry":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("agents", []) if isinstance(data, dict) else []
        return cls(tuple(AgentConfig.model_validate(e) for e in entries))


@lru_cache(maxsize=1)
def default_registry() -> AgentRegistry:
    return AgentRegistry.from_yaml()
