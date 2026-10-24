"""The safe-merge gate: every condition a pull request must meet before we merge it.

Pure function over GitHub data plus the stored review, so each rule is tested
in isolation. The API evaluates it twice: once to show readiness, and again,
from fresh GitHub data, at the moment of merging. The browser is never trusted.
"""

from dataclasses import dataclass
from typing import Any

from app.models.enums import Severity
from app.services.review_store import ReviewRecord

PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


@dataclass(frozen=True, slots=True)
class GateCheck:
    key: str
    label: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"key": self.key, "label": self.label, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class MergeReadiness:
    checks: tuple[GateCheck, ...]
    reviewed_sha: str
    current_sha: str

    @property
    def ready(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failing(self) -> list[GateCheck]:
        return [c for c in self.checks if not c.passed]


def _short(sha: str) -> str:
    return sha[:7] if sha else "unknown"


def _check_enabled(merge_enabled: bool) -> GateCheck:
    return GateCheck(
        "merge_enabled",
        "Merging enabled on this server",
        merge_enabled,
        "MERGE_ENABLED=true" if merge_enabled else "Set MERGE_ENABLED=true in .env to allow merges",
    )


def _check_open(pr: dict[str, Any]) -> GateCheck:
    if pr.get("merged"):
        return GateCheck("pr_open", "Pull request is open", False, "Already merged")
    if pr.get("state") != "open":
        return GateCheck("pr_open", "Pull request is open", False, "Pull request is closed")
    if pr.get("draft"):
        return GateCheck("pr_open", "Pull request is open", False, "Pull request is a draft")
    return GateCheck("pr_open", "Pull request is open", True, "Open and ready for review")


def _check_complete(record: ReviewRecord) -> GateCheck:
    errors: list[str] = record.response.metadata.get("errors", [])
    if record.response.metadata.get("complete", False):
        return GateCheck("review_complete", "Review completed", True, "Every agent finished")
    return GateCheck(
        "review_complete",
        "Review completed",
        False,
        f"{len(errors)} agent(s) failed, so a clean result can't be trusted. Re-run the review.",
    )


def _check_current(reviewed_sha: str, current_sha: str) -> GateCheck:
    if reviewed_sha and reviewed_sha == current_sha:
        return GateCheck(
            "review_current",
            "Review is current",
            True,
            f"Reviewed {_short(reviewed_sha)} is the PR head",
        )
    return GateCheck(
        "review_current",
        "Review is current",
        False,
        f"New commits since review ({_short(reviewed_sha)} → {_short(current_sha)}). Re-run it.",
    )


def _check_findings(record: ReviewRecord) -> GateCheck:
    high = [c for c in record.response.comments if c.severity == Severity.HIGH]
    if not high:
        return GateCheck("no_high_findings", "No high-severity findings", True, "0 unresolved")
    locations = ", ".join(f"{c.file}:{c.line}" if c.line else c.file for c in high[:3])
    more = f" and {len(high) - 3} more" if len(high) > 3 else ""
    return GateCheck(
        "no_high_findings",
        "No high-severity findings",
        False,
        f"{len(high)} unresolved ({locations}{more}). Push fixes, then re-run the review.",
    )


def _check_conflicts(pr: dict[str, Any]) -> GateCheck:
    mergeable = pr.get("mergeable")
    if mergeable is True:
        return GateCheck("no_conflicts", "No merge conflicts", True, "GitHub reports mergeable")
    if mergeable is None:
        return GateCheck(
            "no_conflicts",
            "No merge conflicts",
            False,
            "GitHub is still computing mergeability. Try again in a few seconds.",
        )
    return GateCheck(
        "no_conflicts", "No merge conflicts", False, "Branch has conflicts with the base branch"
    )


def _check_ci(check_runs: list[dict[str, Any]], combined_status: dict[str, Any]) -> GateCheck:
    pending = [r["name"] for r in check_runs if r.get("status") != "completed"]
    failed = [
        r["name"]
        for r in check_runs
        if r.get("status") == "completed" and r.get("conclusion") not in PASSING_CONCLUSIONS
    ]
    statuses: list[dict[str, Any]] = combined_status.get("statuses", [])
    failed += [s["context"] for s in statuses if s.get("state") in ("failure", "error")]
    pending += [s["context"] for s in statuses if s.get("state") == "pending"]

    if failed:
        return GateCheck(
            "ci_passing", "CI checks passing", False, f"Failing: {', '.join(failed[:4])}"
        )
    if pending:
        return GateCheck(
            "ci_passing", "CI checks passing", False, f"Still running: {', '.join(pending[:4])}"
        )
    total = len(check_runs) + len(statuses)
    if total == 0:
        return GateCheck("ci_passing", "CI checks passing", True, "No CI checks configured")
    return GateCheck("ci_passing", "CI checks passing", True, f"{total} of {total} passing")


def evaluate_merge_gate(
    *,
    record: ReviewRecord,
    pull_request: dict[str, Any],
    check_runs: list[dict[str, Any]],
    combined_status: dict[str, Any],
    merge_enabled: bool,
) -> MergeReadiness:
    reviewed_sha = record.request.head_sha
    head = pull_request.get("head")
    current_sha = head.get("sha", "") if isinstance(head, dict) else ""
    checks = (
        _check_enabled(merge_enabled),
        _check_open(pull_request),
        _check_complete(record),
        _check_current(reviewed_sha, current_sha),
        _check_findings(record),
        _check_conflicts(pull_request),
        _check_ci(check_runs, combined_status),
    )
    return MergeReadiness(checks=checks, reviewed_sha=reviewed_sha, current_sha=current_sha)
