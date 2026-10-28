"""Agent registry (plan 2.4): agents are data."""

from pathlib import Path

import pytest
from app.agents.registry import AgentRegistry, default_registry
from app.errors import UnknownAgentError
from app.models.enums import Severity
from app.prompts.shared import OUTPUT_FORMAT


def test_default_registry_has_the_five_reviewers() -> None:
    registry = default_registry()
    assert [a.name for a in registry] == ["bug", "security", "style", "performance", "architecture"]
    assert registry.get("style").severity_floor == Severity.MEDIUM
    assert all(OUTPUT_FORMAT.strip() in a.system_prompt for a in registry)


def test_select_subset_and_reject_unknown() -> None:
    registry = default_registry()
    assert [a.name for a in registry.select(["security", "bug", "security"])] == ["security", "bug"]
    with pytest.raises(UnknownAgentError):
        registry.select(["nope"])
    with pytest.raises(UnknownAgentError):
        registry.select([])


def test_adding_an_agent_is_a_yaml_entry(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        "agents:\n"
        "  - name: docs\n"
        "    category: STYLE\n"
        "    prompt: You review docstrings.\n"
        "    enabled: false\n"
        "    severity_floor: high\n",
        encoding="utf-8",
    )
    registry = AgentRegistry.from_yaml(path)
    docs = registry.get("docs")
    assert docs.severity_floor == Severity.HIGH
    assert docs.accepts(Severity.HIGH) and not docs.accepts(Severity.MEDIUM)
    assert registry.enabled == []
    with pytest.raises(UnknownAgentError, match="disabled"):
        registry.select(["docs"])


def test_registry_rejects_bad_entries(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text("agents:\n  - name: x\n    category: VIBES\n    prompt: p\n", encoding="utf-8")
    with pytest.raises(ValueError, match="category"):
        AgentRegistry.from_yaml(path)
