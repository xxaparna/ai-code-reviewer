"""Tolerant text parsing: the single fallback behind ``LLM_TOLERANT_FALLBACK``.

The primary path is schema-constrained output (``LLMClient.generate_structured``).
When a model cannot honour it, the reviewer node asks again for plain text and
this module recovers JSON from it: bare JSON, a fenced ```json block, or an
array embedded in prose. Nothing else in the codebase parses model text.
"""

import json
import re
from typing import Any

import structlog
from pydantic import ValidationError

from app.models.review_comment import ReviewComment

logger = structlog.get_logger(__name__)

RAW_PREVIEW_CHARS = 500
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_ARRAY_RE = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


def validate_items(
    items: list[Any], *, default_file: str | None = None, agent: str | None = None
) -> tuple[list[ReviewComment], int]:
    """Validate raw finding dicts one by one. Returns (valid findings, rejected count)."""
    comments: list[ReviewComment] = []
    rejected = 0
    for item in items:
        if not isinstance(item, dict):
            rejected += 1
            continue
        data = dict(item)
        if default_file and not data.get("file"):
            data["file"] = default_file
        if agent:
            data["agent"] = agent
        try:
            comments.append(ReviewComment.model_validate(data))
        except ValidationError as exc:
            rejected += 1
            logger.info(
                "finding.rejected_invalid",
                agent=agent,
                fields=sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]}),
            )
    return comments, rejected


class ReviewParser:
    @staticmethod
    def _clean_text(response: str) -> str:
        if not response:
            return ""
        text = response.strip()
        match = _FENCE_RE.search(text)
        return match.group(1).strip() if match else text

    @classmethod
    def parse_items(cls, response: str) -> list[Any]:
        """Extract the list of raw finding objects from free-form model text."""
        cleaned = cls._clean_text(response)
        if not cleaned:
            return []
        data = cls._load_json(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            candidate = data.get("comments", [])
            return candidate if isinstance(candidate, list) else []
        return []

    @classmethod
    def parse_comments(cls, response: str) -> list[ReviewComment]:
        comments, _ = validate_items(cls.parse_items(response))
        return comments

    @classmethod
    def _load_json(cls, cleaned: str) -> Any | None:
        """Parse JSON, falling back to an embedded array if plain load fails."""
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning(
                "parser.json_decode_failed", error=str(exc), response_length=len(cleaned)
            )
            # DEBUG only: raw model output can contain private-repo code.
            logger.debug("parser.raw_response", preview=cleaned[:RAW_PREVIEW_CHARS])

        match = _ARRAY_RE.search(cleaned)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.warning("parser.embedded_array_decode_failed")
        return None

    @classmethod
    def parse_summary(cls, response: str) -> str:
        cleaned = cls._clean_text(response)
        if not cleaned:
            return "Code review completed."
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return cleaned
        if isinstance(data, dict) and "summary" in data:
            return str(data["summary"])
        return cleaned
