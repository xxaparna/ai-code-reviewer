"""Decide which changed files a review spends tokens on (plan 2.5 and 2.6).

Pure functions: given the PR's files, return the files to review plus a record
of every skipped file and why. Nothing is ever dropped silently - each skip is
reported in the review metadata, and a budget cut marks the review partial.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import PurePosixPath

from app.models.review_file import ReviewFile

# Rough, model-agnostic estimate: ~4 characters per token for code and English.
CHARS_PER_TOKEN = 4
# Per-call overhead: system prompt, PR title/description, instructions.
PROMPT_OVERHEAD_TOKENS = 900

LOCKFILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "poetry.lock",
        "pipfile.lock",
        "uv.lock",
        "pdm.lock",
        "cargo.lock",
        "gemfile.lock",
        "composer.lock",
        "go.sum",
        "packages.lock.json",
        "podfile.lock",
        "pubspec.lock",
        "mix.lock",
        "flake.lock",
    }
)

VENDORED_DIRS = frozenset(
    {"vendor", "vendors", "node_modules", "third_party", "third-party", "bower_components"}
)
BUILD_DIRS = frozenset({"dist", "build", "out", ".next", "target", "__generated__", "generated"})

GENERATED_SUFFIXES = (
    "_pb2.py",
    "_pb2_grpc.py",
    ".pb.go",
    ".pb.cc",
    ".pb.h",
    ".g.dart",
    ".freezed.dart",
    ".designer.cs",
    ".generated.ts",
    ".generated.js",
    ".gen.go",
)

BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".psd",
        ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
        ".whl", ".egg", ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class",
        ".pyc", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov",
        ".wav", ".avi", ".sqlite", ".db", ".parquet",
    }
)  # fmt: skip

DOC_EXTENSIONS = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
CONFIG_EXTENSIONS = frozenset(
    {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".env", ".properties"}
)
CONFIG_NAMES = frozenset({"dockerfile", "makefile", "jenkinsfile", ".gitignore", ".dockerignore"})


class Risk(IntEnum):
    """Review priority under a budget: lower value is reviewed first."""

    SOURCE = 0
    CONFIG = 1
    DOCS = 2


@dataclass(frozen=True, slots=True)
class SkippedFile:
    file: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"file": self.file, "reason": self.reason}


@dataclass(slots=True)
class ReviewPlan:
    files: list[ReviewFile] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    estimated_prompt_tokens: int = 0
    partial_reason: str | None = None

    @property
    def is_partial(self) -> bool:
        return self.partial_reason is not None


def estimate_tokens(text: str) -> int:
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def classify_risk(filename: str) -> Risk:
    path = PurePosixPath(filename.lower())
    if path.suffix in DOC_EXTENSIONS or path.name in {"license", "changelog", "authors"}:
        return Risk.DOCS
    if path.suffix in CONFIG_EXTENSIONS or path.name in CONFIG_NAMES:
        return Risk.CONFIG
    return Risk.SOURCE


def skip_reason(file: ReviewFile, *, max_patch_chars: int) -> str | None:
    """Why ``file`` should not be reviewed, or None if it should."""
    path = PurePosixPath(file.filename.lower())
    parts = set(path.parts[:-1])

    if file.status == "removed":
        return "deleted file (nothing on the new side to review)"
    if path.name in LOCKFILES:
        return "lockfile"
    if ".min." in path.name or path.suffix == ".map":
        return "minified or source map"
    if parts & VENDORED_DIRS:
        return "vendored dependency"
    if parts & BUILD_DIRS or path.name.endswith(GENERATED_SUFFIXES) or ".generated." in path.name:
        return "generated or build output"
    if path.suffix in BINARY_EXTENSIONS:
        return "binary file"
    if not file.patch or not file.patch.strip():
        # GitHub omits the patch for binaries and for diffs too large to show.
        return "no textual diff (binary, or too large for GitHub to render)"
    if len(file.patch) > max_patch_chars:
        return f"diff too large ({len(file.patch):,} chars > {max_patch_chars:,})"
    return None


def plan_review(
    files: list[ReviewFile],
    *,
    agent_count: int,
    max_patch_chars: int,
    max_prompt_tokens: int,
) -> ReviewPlan:
    """Filter files, then fit the remainder into the prompt-token budget.

    Each reviewable file costs ``agent_count`` prompts. When the total exceeds
    the budget, files are admitted in risk order (source, config, docs; larger
    diffs first within a tier, since they carry more change) and the rest are
    skipped, which marks the review partial. Never truncates a file's diff.
    """
    plan = ReviewPlan()
    candidates: list[ReviewFile] = []
    for file in files:
        reason = skip_reason(file, max_patch_chars=max_patch_chars)
        if reason:
            plan.skipped.append(SkippedFile(file.filename, reason))
        else:
            candidates.append(file)

    def cost(file: ReviewFile) -> int:
        return (estimate_tokens(file.patch) + PROMPT_OVERHEAD_TOKENS) * max(agent_count, 1)

    ordered = sorted(
        enumerate(candidates),
        key=lambda pair: (classify_risk(pair[1].filename), -len(pair[1].patch), pair[0]),
    )
    admitted: set[int] = set()
    over_budget: list[ReviewFile] = []
    for index, file in ordered:
        file_cost = cost(file)
        if plan.estimated_prompt_tokens + file_cost <= max_prompt_tokens:
            plan.estimated_prompt_tokens += file_cost
            admitted.add(index)
        else:
            over_budget.append(file)

    # Keep the PR's own file order for the files we do review.
    plan.files = [f for i, f in enumerate(candidates) if i in admitted]
    for file in over_budget:
        plan.skipped.append(SkippedFile(file.filename, "over the review's token budget"))
    if over_budget:
        plan.partial_reason = (
            f"{len(over_budget)} file(s) not reviewed: the diff exceeds the review budget of "
            f"~{max_prompt_tokens:,} prompt tokens. Lower-risk files were skipped first."
        )
    return plan
