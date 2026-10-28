"""Tuning knobs for one review, decoupled from credentials.

:class:`ReviewConfig` has safe defaults so the pipeline can be built and tested
without a ``.env``; :meth:`ReviewConfig.from_settings` maps the environment.
"""

from dataclasses import dataclass

from app.settings import Settings


@dataclass(frozen=True, slots=True)
class ReviewConfig:
    structured_output: bool = True
    tolerant_fallback: bool = True
    max_concurrency: int = 5
    max_patch_chars: int = 40_000
    max_review_prompt_tokens: int = 400_000
    max_findings_per_file: int = 10
    max_findings_per_review: int = 50
    dedup_similarity_threshold: int = 70
    dedup_line_window: int = 3

    @classmethod
    def from_settings(cls, settings: Settings) -> "ReviewConfig":
        return cls(
            structured_output=settings.llm_structured_output,
            tolerant_fallback=settings.llm_tolerant_fallback,
            max_concurrency=settings.max_concurrent_llm_calls,
            max_patch_chars=settings.max_patch_chars,
            max_review_prompt_tokens=settings.max_review_prompt_tokens,
            max_findings_per_file=settings.max_findings_per_file,
            max_findings_per_review=settings.max_findings_per_review,
            dedup_similarity_threshold=settings.dedup_similarity_threshold,
            dedup_line_window=settings.dedup_line_window,
        )
