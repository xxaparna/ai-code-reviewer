"""File filtering (plan 2.5) and the review token budget (plan 2.6)."""

from app.utils.file_filter import Risk, classify_risk, estimate_tokens, plan_review, skip_reason

from tests.fakes import make_file

PATCH = "@@ -1 +1,2 @@\n+x = 1\n+y = 2\n"


def _f(name: str, patch: str = PATCH, status: str = "modified"):  # type: ignore[no-untyped-def]
    return make_file(name, patch=patch, status=status)


def test_skip_reasons() -> None:
    cases = {
        "package-lock.json": "lockfile",
        "backend/poetry.lock": "lockfile",
        "web/static/app.min.js": "minified",
        "vendor/github.com/x/y.go": "vendored",
        "web/node_modules/a/index.js": "vendored",
        "proto/user_pb2.py": "generated",
        "web/dist/index.js": "generated",
        "assets/logo.png": "binary",
    }
    for name, expected in cases.items():
        reason = skip_reason(_f(name), max_patch_chars=10_000)
        assert reason is not None and expected in reason, name


def test_deleted_empty_and_huge_files_are_skipped() -> None:
    assert "deleted" in (skip_reason(_f("a.py", status="removed"), max_patch_chars=10_000) or "")
    assert "no textual diff" in (skip_reason(_f("a.py", patch=""), max_patch_chars=10_000) or "")
    assert "too large" in (
        skip_reason(_f("a.py", patch="+x\n" * 5_000), max_patch_chars=10_000) or ""
    )
    assert skip_reason(_f("src/app.py"), max_patch_chars=10_000) is None


def test_risk_tiers() -> None:
    assert classify_risk("src/app.py") is Risk.SOURCE
    assert classify_risk("config/settings.yaml") is Risk.CONFIG
    assert classify_risk("Dockerfile") is Risk.CONFIG
    assert classify_risk("docs/guide.md") is Risk.DOCS


def test_every_skip_is_recorded() -> None:
    files = [_f("a.py"), _f("yarn.lock"), _f("b.png")]
    plan = plan_review(files, agent_count=5, max_patch_chars=10_000, max_prompt_tokens=10**9)
    assert [f.filename for f in plan.files] == ["a.py"]
    assert {s.file for s in plan.skipped} == {"yarn.lock", "b.png"}
    assert not plan.is_partial


def test_budget_prioritises_source_over_config_over_docs_and_marks_partial() -> None:
    big = "@@ -1 +1,400 @@\n" + "+code = 1\n" * 400
    files = [_f("README.md", big), _f("settings.yaml", big), _f("app.py", big)]
    one_file = plan_review(
        [files[0]], agent_count=2, max_patch_chars=10**6, max_prompt_tokens=10**9
    )
    budget = one_file.estimated_prompt_tokens + 10  # room for exactly one file

    plan = plan_review(files, agent_count=2, max_patch_chars=10**6, max_prompt_tokens=budget)

    assert [f.filename for f in plan.files] == ["app.py"]
    assert plan.is_partial
    assert {s.file for s in plan.skipped if "budget" in s.reason} == {"README.md", "settings.yaml"}
    assert plan.estimated_prompt_tokens <= budget


def test_budget_never_truncates_a_diff() -> None:
    files = [_f("a.py", PATCH)]
    plan = plan_review(files, agent_count=5, max_patch_chars=10**6, max_prompt_tokens=10**9)
    assert plan.files[0].patch == PATCH
    assert estimate_tokens("abcd") == 1
