import json
import re
from typing import Any

import structlog

from app.models.review_comment import FindingList, ReviewComment

logger = structlog.get_logger(__name__)

RAW_PREVIEW_CHARS = 500


class ReviewParser:
    @staticmethod
    def _clean_text(response: str) -> str:
        if not response:
            return ""
        text = response.strip()
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text

    @classmethod
    def parse_comments(cls, response: str | FindingList) -> list[ReviewComment]:
        if isinstance(response, FindingList):
            return response.comments

        cleaned = cls._clean_text(response)
        if not cleaned:
            return []

        data = cls._load_json(cleaned)
        if data is None:
            return []

        items: list[Any] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            candidate = data.get("comments", [])
            items = candidate if isinstance(candidate, list) else []

        comments: list[ReviewComment] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                comment = ReviewComment.model_validate(item)
                comments.append(comment)
            except Exception as exc:
                logger.warning("parser.comment_validation_failed", error=str(exc))

        return comments

    @classmethod
    def _load_json(cls, cleaned: str) -> Any | None:
        """Parse JSON, falling back to embedded array if plain load fails."""
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.warning(
                "parser.json_decode_failed",
                error=str(exc),
                response_length=len(cleaned),
            )
            logger.debug("parser.raw_response", preview=cleaned[:RAW_PREVIEW_CHARS])

        match = re.search(r"\[\s*\{.*\}\s*\]", cleaned, re.DOTALL)
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
            if isinstance(data, dict) and "summary" in data:
                return str(data["summary"])
        except json.JSONDecodeError:
            pass

        return cleaned
