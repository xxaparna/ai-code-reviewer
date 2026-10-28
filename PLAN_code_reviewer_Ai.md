# AI Code Reviewer — Production Hardening Plan

**Status:** Phases 0-2 complete (§5b, §6b); baseline measured (§4b); Phase 10 MVP plus
suggested changes and safe merge shipped early (§14d); next: 6.10, then async review (§14e, P2)
**Owner:** @aparna2-k
**Created:** 2026-09-17
**Baseline commit:** `3753e01`

---

## 1. Goal

Turn the current working prototype into a defensible, production-grade
system: a GitHub App that reviews pull requests with a multi-agent LangGraph
pipeline, persists every review and its cost, posts real inline comments, and
proves its own quality with a measured evaluation harness.

The bar is not "it works." The bar is: **every claim in the README is backed by
code, tests, and a number.**

### In scope (this plan)

- Correctness and resilience of the existing pipeline
- Real per-file map-reduce agent architecture
- Groq-constrained execution (rate limits, budgets, caching)
- Async API with auth, proper error mapping, and a job queue
- PostgreSQL persistence with migrations
- GitHub App + webhooks + inline comments with diff-position mapping
- Verifier agent + evaluation harness with precision/recall
- Test suite with meaningful coverage

### Explicitly out of scope (deferred by decision, see §9)

- Docker / docker-compose
- Any cloud deployment
- Redis (replaced by Postgres-backed equivalents)
- Multi-provider LLM fallback (only the abstraction seam is built)

---

## 2. Key decisions

### 2.1 Database: PostgreSQL, not MongoDB

**Decision: PostgreSQL 16 + SQLAlchemy 2.0 (async) + Alembic.**

Rationale:

- The data is inherently relational: `repository → pull_request → review →
  finding`, plus `agent_run` and `llm_call` rows hanging off each review. Every
  interesting query is a join or an aggregate ("false-positive rate per agent
  per month", "p95 latency by model", "token spend per repo").
- The evaluation harness (§8) is fundamentally analytical. `GROUP BY agent,
  severity` with window functions is the whole job. That is SQL's home turf.
- `JSONB` columns give the schema flexibility Mongo is usually chosen for —
  raw LLM responses, prompt snapshots, GitHub webhook payloads all go in
  `JSONB` and stay queryable.
- Alembic migrations are a concrete, reviewable engineering artifact. "I
  designed the schema and wrote the migrations" is a stronger statement than
  "I inserted documents."
- Postgres also lets us delete two infra dependencies: it becomes the job
  queue (`SELECT … FOR UPDATE SKIP LOCKED`) and the response cache, so no
  Redis is needed. Fewer moving parts, same capability.

Mongo would only win if the shape of a finding were unpredictable across
agents. It is not — it is a fixed five-field record.

### 2.2 LLM provider: Groq only, behind a seam

Groq is the only provider for now (free tier). That constraint is treated as a
**design driver, not a limitation** — engineering a correct system inside a
hard rate/token budget is more interesting than having unlimited quota.

- Define a `LLMProvider` Protocol in `app/llm/base.py` with a single
  `GroqProvider` implementation. No other provider is written, but adding one
  later is a new file, not a refactor.
- All Groq limits (RPM, TPM, RPD, per-model) live in config as values read off
  the Groq console — **never hardcoded from memory**, since they change per
  account and per model. Verify current limits at
  <https://console.groq.com/docs/rate-limits> and record them in `.env`.
- **Model tiering.** Not every agent needs the largest model. Route the
  high-stakes agents (bug, security) to the strongest available model and the
  cheap agents (style) to a small fast one. Make the mapping config, not code,
  and measure the quality delta in the eval harness (§8) — that measurement is
  itself a portfolio result.
- **Verify JSON-mode support per model.** Structured output / JSON mode is not
  uniformly supported across Groq-hosted models. Before committing to
  `with_structured_output`, confirm support for each model chosen, and keep a
  tolerant-parser fallback behind a config flag.

### 2.3 Concurrency model

Async end-to-end (`async def` routes, `httpx.AsyncClient`, `ainvoke`), with
concurrency deliberately *bounded* by a rate limiter rather than left to the
event loop. Reviews run as background jobs, not inside the HTTP request.

---

## 3. Target architecture

```
app/
  main.py                  # FastAPI app factory + lifespan only (no CLI)
  cli.py                   # the CLI, moved out of main.py
  settings.py              # pydantic-settings BaseSettings (replaces config.py)
  logging_config.py        # structlog configuration (not logging.py:
                           #   that name shadows the stdlib module)

  api/
    deps.py                # DI: settings, session, services, auth
    errors.py              # exception handlers -> correct status codes
    routes/
      reviews.py           # POST /reviews (202), GET /reviews/{id}
      webhooks.py          # POST /webhooks/github (HMAC verified)
      health.py            # /healthz, /readyz, /metrics
    schemas.py

  domain/                  # renamed from models/ - pure, no I/O
    finding.py             # Pydantic, validated, Literal severity
    review.py
    pull_request.py
    enums.py               # Severity, Category, AgentName

  llm/
    base.py                # LLMProvider Protocol
    groq_provider.py
    rate_limiter.py        # token bucket: RPM + TPM
    budget.py              # per-review token/cost ceiling
    cache.py               # content-hash cache (Postgres-backed)

  graph/
    state.py               # reducers on every parallel-written key
    build.py               # Send-based map-reduce fan-out
    nodes/
      reviewer.py          # one generic node, parameterised by agent config
      verifier.py          # adversarially re-checks findings
      merge.py
      summary.py

  agents/
    registry.py            # loads agents.yaml
    agents.yaml            # name, prompt, model tier, enabled, severity floor

  github/
    client.py              # async httpx, timeouts, retries
    app_auth.py            # GitHub App JWT -> installation token
    diff.py                # THE hard part: patch hunks -> reviewable positions
    publisher.py           # idempotent review posting

  db/
    models.py              # SQLAlchemy 2.0 ORM
    session.py
    repositories/          # data-access layer, one module per aggregate
    migrations/            # Alembic

  jobs/
    queue.py               # Postgres FOR UPDATE SKIP LOCKED
    worker.py              # standalone async worker process

  services/
    review_service.py      # orchestration only

eval/
  dataset/                 # labeled PRs with known-good findings
  runner.py
  metrics.py               # precision, recall, F1 per agent
  report.py

tests/
  unit/ integration/ fixtures/
```

### Request flow (target)

```
GitHub webhook (pull_request: opened|synchronize)
  -> verify HMAC signature
  -> mint installation token
  -> enqueue job row in Postgres, return 202 immediately
       |
worker picks up job (SKIP LOCKED)
  -> fetch PR + files (async, paginated, timeout, retry)
  -> filter files (skip lockfiles/generated/binary), apply diff budget
  -> cache lookup by (file blob sha, agent, model, prompt version)
  -> LangGraph: Send(file) x N  ->  5 reviewer agents per file (rate-limited)
  -> verifier node drops unsupported findings
  -> merge: fuzzy dedup + severity collapse + per-file cap
  -> summary node
  -> persist review, findings, agent_runs, llm_calls
  -> map findings to diff positions; drop unmappable ones
  -> post GitHub review with inline comments (idempotent)
```

---

## 4. Phase 0 — Cleanup and foundation

No behaviour change. Makes everything after this cheaper. **Do this first.**

| # | Task | Files |
|---|------|-------|
| 0.1 | Delete dead code | `app/services/ai_reviewer.py`, `app/graph/workflow.py`, `app/services/prompt_builder.py` (0 bytes), `app/services/review_mapper.py` (0 bytes) |
| 0.2 | Remove unused `GitHubService.get_authenticated_user` | `app/services/github_service.py:31` |
| 0.3 | Untrack `.DS_Store`, add `.DS_Store` + `.pytest_cache/` + `.ruff_cache/` + `.mypy_cache/` to `.gitignore` | `.gitignore` |
| 0.4 | Move to `pyproject.toml` with a lock file (`uv`). Drop never-imported deps: `openai`, `groq`. Replace `requests` with `httpx`. Pin everything. | `pyproject.toml`, delete `requirements.txt` |
| 0.5 | Add `ruff` (lint + format), `mypy --strict`, `pre-commit` | `pyproject.toml`, `.pre-commit-config.yaml` |
| 0.6 | Remove the `# pyrefly: ignore [missing-import]` suppression at `app/api/routes.py:1` and fix the underlying import | `app/api/routes.py` |
| 0.7 | Replace import-time-raising `config.py` with `pydantic-settings` `Settings`, validated at app startup | new `app/settings.py`, delete `app/config.py` |
| 0.8 | Configure `structlog` JSON logging with levels; stop logging full raw model output at ERROR (leaks private-repo code into logs) | new `app/logging_config.py`, `app/graph/parser.py:30` |
| 0.9 | Split the CLI out of `main.py` so `main.py` is only the ASGI app | `app/main.py` -> `app/cli.py` |

**Why 0.7 matters:** `app/config.py:10-14` raises `ValueError` on *import*, so the
package cannot be imported without secrets present. That breaks CI, a fresh
clone, and any test run on a clean machine.

**Definition of done: MET.** With `.env` removed and `GITHUB_TOKEN`/`GROQ_API_KEY`
unset: `ruff check` clean, `ruff format --check` clean, `mypy` (strict) clean on
47 files, 27 tests passing, 58% coverage.

### Phase 0 outcome notes

Completed as specified, plus the following, which are worth knowing before
Phase 1 starts:

- **Missing package markers.** `app/`, `app/api/` and `tests/` had no
  `__init__.py`; the project was relying on implicit namespace packages, which
  is why `mypy` initially refused to run at all ("source file found twice under
  different module names"). Added.
- **`app/prompts/review_prompt.py` was also dead** — its `SYSTEM_PROMPT` was
  referenced only by the deleted `ai_reviewer.py`. Coverage reporting it at 0%
  is what surfaced it. Deleted. Five dead modules removed in total.
- **No `uv` on this machine.** Every dependency is pinned to an exact version
  in `pyproject.toml`, which gives reproducibility, but the lock file is not
  generated yet. Run `uv lock && uv sync --dev` once `uv` is installed, and
  commit `uv.lock`.
- **`httpx` stays; `httpx2` rejected.** Starlette 1.3 deprecates using
  `TestClient` with `httpx` and asks for the `httpx2` package. Verified
  experimentally that **`respx` cannot intercept `httpx2`** — a mocked request
  escaped to the real network. Since Phase 1 depends on mocking GitHub, the app
  stays on `httpx` and route tests drive the ASGI app through
  `httpx.ASGITransport` instead of `TestClient` (which is what §13 specifies
  anyway). Revisit when `respx` supports `httpx2`.
- **`structlog` logger factory.** `WriteLoggerFactory` is incompatible with the
  `stdlib.add_logger_name` processor (no `.name` on the logger); using
  `stdlib.LoggerFactory` also puts uvicorn and httpx logs on the same handler.
  Caught by a test, not by review.
- **Three Phase 1 items landed early**, because the files were being rewritten
  anyway: explicit GitHub HTTP timeouts (part of 1.1 — `httpx` has no infinite
  default, so a value had to be chosen), the 500 response no longer echoes
  upstream error text (part of 1.7), and reviewer nodes/`ReviewService` no
  longer build an LLM client or graph at import time (part of 1.9, and required
  by this phase's own definition of done).
- **Node signatures are now typed `ReviewState`** rather than `dict`, which
  removed eight `mypy` errors legitimately instead of suppressing them. The
  `app.graph.*` relaxation in `pyproject.toml` is therefore narrower than
  planned — keep shrinking it in Phase 2.
- **`tests/conftest.py` now has `make_review_request` / `make_review_state`
  factories.** Phase 1 and 2 tests should build state through these.

---

## 4b. Measured baseline (2026-09-18)

First real end-to-end runs, on `groq/openai/gpt-oss-120b`. These numbers set the
bar every later change is measured against.

**Run 1 — `psf/requests#7583`** (1 file, 2 changed lines, mature codebase):
6 LLM calls, **0 findings**, `complete: True`. Correct: a dependency bump should
produce nothing. Confirms no false positives on trivial input, and confirms the
parallel fan-out works (5 concurrent calls, all agents finishing within 270ms
of each other).

**Run 2 — `eval/positive_control`** (34 synthetic lines, 7 planted defects):

| Metric | Result |
|---|---|
| Planted defects found | **7 / 7 (100% recall)** |
| Fabricated / hallucinated findings | **0** |
| Comments emitted | 27 |
| Distinct real issues in the file | ~12 |
| **Duplication factor** | **2.2x** |

The reported 26% precision is an artifact of crude keyword matching, not a
quality problem. Hand-classifying all 20 unmatched findings:

- **13 were cross-agent duplicates** of planted defects — the same issue
  restated by a second and third agent (SQL concatenation was reported four
  times: as injection, as brittle, as un-maintainable, and as defeating
  prepared-statement reuse).
- **5 were genuinely new real issues** nobody planted: path traversal on the
  caller-supplied path in `load_config`, a Law-of-Demeter violation in
  `total_price`, a real logic bug in `find_duplicates` (`i == j` is always true,
  so it returns every item), and two module-cohesion findings. All five have
  been added to the planted set so future runs get credit for them.
- **2 were low-value but not wrong** (missing docstrings, unclear function
  name).

### What this changes

**Detection quality is not the problem.** 7/7 recall with zero fabrication on a
first attempt is a genuinely good result and belongs in the README. The prompts
and the model tier are fine.

**Signal-to-noise is the problem.** 27 comments on 34 lines is a tool a human
switches off. Nothing else matters as much as fixing that.

**Root cause found, and it is one line of prompt.** `app/prompts/shared.py`
constrains `severity` ("MUST be one of LOW, MEDIUM, HIGH") but **never
enumerates the valid categories** — the only hint an agent gets is the example
value `"BUG"`. The category list used to live in `review_prompt.py`, which was
dead code and was deleted in Phase 0. Consequences:

1. Every agent copies `BUG`, so a SQL-injection finding is labelled `BUG`, not
   `SECURITY`. `metadata["category_counts"]` is therefore meaningless.
2. One agent invented `CODE_SMELL`, a category no prompt mentions - proof that
   nothing validates the field.
3. **`merge.py` dedups on `(file, line, category, message)`.** Mislabelled
   categories defeat the dedup key, which is why 13 duplicates survived.

So fixing the category enum partially fixes deduplication for free.

### Re-prioritisation

Items promoted into Phase 1 on the strength of this data:

- **2.7 (fuzzy dedup) moves to Phase 1.** It was a Phase 2 sub-item; the
  measured 2.2x duplication makes it the highest-value single fix in the plan.
- **Category enum becomes 1.5a**, ahead of the rest of 1.5: add the allowed
  categories to `OUTPUT_FORMAT` in `app/prompts/shared.py`, and validate the
  field against a `Category` enum so an invented value is rejected rather than
  passed through.
- **Style-agent severity floor moves to Phase 1** (was 2.4): the two low-value
  findings both came from the style agent.

---

## 5. Phase 1 — Correctness and resilience

The prototype's failure modes, fixed.

| # | Task | Detail |
|---|------|--------|
| 1.1 | **Timeouts everywhere** | No call in the codebase has one today. `httpx` connect/read/write/pool timeouts; explicit timeout on every LLM call. |
| 1.2 | **Retries with backoff** | `tenacity`: retry on 429/5xx/timeout, exponential backoff with jitter, honour the `Retry-After` header, cap attempts. Never retry a 4xx that isn't 429. |
| 1.3 | **Typed exceptions** | `PRNotFoundError`, `RepoAccessDeniedError`, `UpstreamRateLimitError`, `LLMUnavailableError`, `DiffTooLargeError`, `BudgetExceededError` in `app/errors.py`. |
| 1.4 | **Structured LLM output** | Use `with_structured_output(FindingList)` (or Groq JSON mode) per §2.2. Then **delete all three regex layers**: `llm_client.py:41-49`, `parser.py:12-19`, and the array-regex fallback at `parser.py:32`. Keep one tolerant fallback behind a flag. |
| 1.5 | **Validate model output** | `Finding` becomes Pydantic: `severity: Literal["LOW","MEDIUM","HIGH"]`, `category: Category` enum, `line: int \| None` coerced, `message` non-empty and length-capped. Today `parser.py:53-61` passes `line` through untouched, so `"line": "around 15"` flows straight to the client. |
| 1.6 | **Drop hallucinated locations** | Reject any finding whose `file` is not in the PR's changed-file set, and any `line` outside that file's diff hunks. This is the most common LLM failure and it is cheap to eliminate. |
| 1.7 | **Fix GitHub error mapping** | `raise_for_status` at `github_service.py:23` becomes a generic 500 with upstream text leaked to the client (`routes.py:37`). Map: missing PR -> 404, bad token -> 502, upstream 429 -> 429 with `Retry-After`. Never echo upstream bodies. |
| 1.8 | **Validate PR URLs properly** | `github_parser.py:16-24` accepts `https://evil.com/a/b/pull/1`, and `owner`/`repo` are interpolated raw into the API path (`github_service.py:35`), so `owner=".."` can reshape the request path. Assert host is GitHub; URL-encode every path segment. |
| 1.5a | **Enumerate and validate categories** (promoted, see §4b) | `OUTPUT_FORMAT` never lists the allowed categories, so agents default to `BUG` and one invented `CODE_SMELL`. Add the enum to the prompt *and* validate against a `Category` enum. Partially fixes dedup, because the dedup key includes category. |
| 1.11 | **Fuzzy cross-agent dedup** (promoted from 2.7) | Measured 2.2x duplication. Collapse findings within an `(file, line +/- 3)` window by message similarity (`rapidfuzz`), keep the highest severity, record the collapse count. |
| 1.12 | **Severity floor per agent** (promoted from 2.4) | The style agent produced the only two low-value findings. Allow a per-agent minimum severity, and allow disabling an agent entirely. |
| 1.10 | **Guard empty and unreviewable pull requests** | Found while testing against `psf/requests#7587`, which has 0 changed files: the pipeline makes all 6 LLM calls against an empty diff and returns `complete: True` with `total_files: 0`. Short-circuit before any LLM call when no file has a reviewable patch, and return an explicit "nothing to review" result. Same guard covers PRs where every file was filtered out by 2.5. |
| 1.9 | **Kill import-time singletons** | `nodes.py:10`, `summary.py:8`, and `routes.py:7` (which compiles the whole graph at import). Move to lifespan + `Depends` so tests can inject fakes. |
| 1.13 | **Green the existing suite** (added 2026-10-06, §14e) | 6 tests and 7 `mypy` errors fail on the uncommitted Phase 1 work: `FakeLLMClient` lacks `generate_structured`, so the pipeline tests exercise only the fallback path; the URL-parser tests expect `ValueError`, but `InvalidPRURLError` does not subclass it; `tests/test_merge_node.py` passes `str` where `Severity`/`Category` are expected; `ChatGroq(request_timeout=...)` is not a valid keyword. A red suite hides real regressions. |
| 1.14 | **GitHub retries never fire** (added 2026-10-06, §14e) | `GitHubService._request` converts 429/5xx into domain exceptions *inside* the `@retry`-decorated function, but `_should_retry_exception` only matches raw `httpx` errors. So 1.2's backoff never runs for GitHub. Raise inside, classify outside: retry on the `httpx` error, map to the domain exception after the last attempt. Same check is needed for `llm_client.py`, which retries by substring-matching the message. |

**Definition of done:** a test suite that simulates upstream 429s, timeouts,
malformed JSON, and hallucinated file paths, and asserts the correct typed
exception or the correct dropped finding — not a 500.

### 5b. Phase 1 outcome (2026-10-27)

**Definition of done: MET.** `tests/test_github_service.py` and
`tests/test_llm_client.py` simulate 429s (with and without `Retry-After`),
5xx, timeouts and connection errors and assert the typed exception and the
attempt count; `tests/test_review_pipeline.py` and `tests/test_validation.py`
cover malformed JSON, fences, prose-wrapped JSON, bad severities/categories,
string line numbers and hallucinated paths/lines. Suite: 167 passing, `ruff`
and `mypy --strict` clean on 66 files, 91% line coverage.

| # | What landed |
|---|---|
| 1.1 | `ChatGroq(timeout=..., max_retries=0)` (the invalid `request_timeout=` kwarg is gone); `LLM_TIMEOUT_SECONDS`. GitHub timeouts unchanged. |
| 1.2 / 1.14 | One retry policy in `app/utils/retry.py`. GitHub: errors are raised as raw `httpx` errors *inside* the tenacity loop and mapped to domain exceptions *after* it, so 429/5xx/timeouts now actually retry. LLM: classified by Groq exception **type**, not substring. `Retry-After` (seconds or HTTP date) and GitHub's `x-ratelimit-reset` are honoured; a requested wait over 30s is not slept on but surfaced as `UpstreamRateLimitError(retry_after=...)`. POSTs are not retried on 5xx (the review may already exist). The Groq SDK's own retries are off, removing the 18-calls-per-agent worst case. |
| 1.3 | Added `GitHubAuthError` (401, server credential problem), `UpstreamError` (5xx/network after retries), `StructuredOutputError`, `UnknownAgentError`. |
| 1.4 | Structured output targets a loose `RawFindingList`; each item is then validated on its own, so one bad finding no longer fails the batch. Rate limits/timeouts are *not* retried as text. The tolerant parser is the only regex left and sits behind `LLM_TOLERANT_FALLBACK`. |
| 1.5 / 1.5a | Strict `Severity`/`Category` parsing with a small alias table (`critical`→HIGH, `code smell`→STYLE); anything else is **rejected and counted** (`invalid_findings_dropped`), never relabelled. Empty messages rejected; paths normalised; `"around 15"`, `True`, unicode digits → `None`. |
| 1.6 | Done per (file, agent) in the reviewer node: wrong path or out-of-hunk line → dropped and counted. |
| 1.7 | 401 → 502, 403 rate-limit → 429, 5xx → 502, `Retry-After` now actually reaches the client (it was set on the injected `Response`, which FastAPI discards when an `HTTPException` is raised). Tests assert upstream text never leaks. |
| 1.8 | Host via `hostname`; userinfo/ports rejected; owner/repo checked against GitHub's character rules; ASCII-digit PR numbers only. |
| 1.9 | No module-level node singletons; graph, LLM pool and registry are injected; the lifespan builds `ReviewService.from_settings()` + store on `app.state`, closed on shutdown. |
| 1.10 | Covered by 2.5: no reviewable file → zero LLM calls, `unreviewable: true`. |
| 1.11 / 1.12 | See 2.7 / 2.4. Style floor is MEDIUM in `agents.yaml`. |
| 1.13 | Suite green; `FakeLLMClient.generate_structured` behaves like JSON mode, so both paths are exercised. |

**Also removed:** the Phase 0 dead modules were still in the working tree
(`config.py`, `workflow.py`, `ai_reviewer.py`, `review_prompt.py`, the two empty
`services/*.py`) and are now deleted, along with `base_node.py`, `nodes.py` and
the five `*_prompt.py` files superseded by `agents.yaml`. `requirements.txt` is
still present and stale; delete it when `uv.lock` is committed.

---

## 6. Phase 2 — The real multi-agent architecture

This is the single highest-value change in the plan, technically and on a
resume.

### The bug being fixed

`ReviewState.current_file` (`state.py:10`) is initialised to `None`
(`review_service.py:47`) and **never set by anything**. So
`PromptBuilder.build` always takes its `else` branch
(`prompt_builder.py:27-33`) and concatenates *the entire PR diff — every file —
into one prompt, sent five times in parallel*. The per-file code path exists
and is dead. Any real PR blows the context window.

### Tasks

| # | Task | Detail |
|---|------|--------|
| 2.1 | **Per-file fan-out via LangGraph `Send`** | `START -> Send(file) × N -> 5 reviewer agents per file -> merge -> verify -> summary -> END`. Dynamic map-reduce, file count known only at runtime. |
| 2.2 | **State redesign** | Every key written by parallel branches needs a reducer (`Annotated[list[Finding], operator.add]`), not just `errors`. Today the five comment lists are safe only because nothing fans out. |
| 2.3 | **One generic reviewer node** | `nodes.py` is five near-identical functions. Replace with one node parameterised by an agent config record. Adding an agent becomes a YAML entry. |
| 2.4 | **Agent registry** | `app/agents/agents.yaml`: `name`, `prompt`, `model_tier`, `enabled`, `severity_floor`, `max_findings_per_file`. Clients may request a subset of agents per review. |
| 2.5 | **File filtering** | Skip lockfiles, `*.min.*`, generated code, vendored dirs, binaries, and files over a size cap. Record every skip and why, in the review metadata. |
| 2.6 | **Diff budget** | Hard cap on total prompt tokens per review. When exceeded, prioritise files by risk (source > config > docs) and mark the review `partial` — never silently truncate. |
| 2.7 | **Better dedup** | `merge.py:34-39` keys on exact lowercased message, so five agents describing the same bug five different ways all survive. Use `rapidfuzz` similarity within an `(file, line±3)` window, keep the highest severity, record the collapse count. |
| 2.8 | **Per-file and per-review finding caps** | A review with 200 comments gets ignored by humans. Cap, ranked by severity. |

**Definition of done:** a 40-file PR produces one LLM call per
(file × enabled agent), each prompt containing exactly one file's diff, with a
graph test asserting the fan-out width and that reducers merge correctly.

### 6b. Phase 2 outcome (2026-10-27)

**Definition of done: MET.** `tests/test_graph_fanout.py` reviews a 40-file PR
and asserts 200 reviewer calls (40 files × 5 agents) + 1 summary, exactly one
file's diff per prompt, every (file, agent) pair once, and that the reducers
merged all 200 branches.

| # | What landed |
|---|---|
| 2.1 | `START --Send(file, agent)--> review --> merge --> summary`. One `Send` per (file × agent), chosen over one per file: maximum parallelism and one `AgentRun` record per call, which maps 1:1 onto Phase 5's `agent_runs`. Concurrency bounded by LangGraph's `max_concurrency` (`MAX_CONCURRENT_LLM_CALLS`, default 5) - a stopgap until the Phase 3 token bucket. |
| 2.2 | `findings`, `agent_runs`, `errors` use `operator.add`. The five per-agent keys are gone. |
| 2.3 | `app/graph/reviewer.py`: one `ReviewerNode`, parameterised by `AgentConfig`. |
| 2.4 | `app/agents/agents.yaml` + `registry.py` (`name, category, model_tier, enabled, severity_floor, max_findings_per_file, prompt_version, prompt`). `model_tier: fast` → `GROQ_MODEL_FAST`. Clients pick a subset with `agents: [...]` on `POST /reviews` and `/review`, or `--agents` on the CLI; `GET /api/v1/agents` lists them. Unknown or disabled agents → 400. |
| 2.5 | `app/utils/file_filter.py`: lockfiles, minified/maps, vendored, generated/build output, binaries, deleted files, missing patches, diffs over `MAX_PATCH_CHARS`. Every skip lands in `metadata.skipped_files` with its reason. |
| 2.6 | Estimated prompt tokens (chars/4 + overhead, × agents) against `MAX_REVIEW_PROMPT_TOKENS`. Over budget: files admitted in risk order (source → config → docs, larger diffs first), the rest skipped, review marked `partial` with `partial_reason`, `complete: false` (so the merge gate blocks). No diff is ever truncated. |
| 2.7 | `rapidfuzz.token_set_ratio` ≥ 70 within `(file, line ± 3)`, category ignored; highest severity kept, `dedup_collapsed_count` and `metadata.duplicates_collapsed` recorded. |
| 2.8 | Per (file, agent) cap from the registry, then `MAX_FINDINGS_PER_FILE` and `MAX_FINDINGS_PER_REVIEW` after dedup, severity-ranked; drops counted. |

New review metadata: `reviewed_files`, `skipped_files`, `agents`,
`estimated_prompt_tokens`, `raw_findings`, `duplicates_collapsed`,
`invalid_findings_dropped`, `below_severity_floor_dropped`,
`capped_findings_dropped`, `agent_stats` (per agent: runs, failed, findings,
dropped, slowest call), `structured_output_fallbacks`, `llm_calls`, `partial`,
`partial_reason`. Error strings are now `"<agent>: <file>: <reason>"`.

**Not done here, on purpose:** the verifier node (7.1) slots in as
`merge → verify → summary`; the dedup threshold of 70 is a starting point that
should be tuned against `eval/positive_control.py` - **re-run it**, since the
prompts now carry one file each and the baseline numbers in §4b predate that.

---

## 7. Phase 3 — Groq-constrained execution layer

Making a rate-limited free tier behave like a reliable dependency.

| # | Task | Detail |
|---|------|--------|
| 3.1 | **Token-bucket rate limiter** | Two buckets per model: requests/min and tokens/min. Async, shared across the worker. Limits come from config (read off the Groq console, per §2.2). |
| 3.2 | **Bounded concurrency** | An `asyncio.Semaphore` sized to the limiter, so fan-out over 40 files does not fire 200 simultaneous calls. |
| 3.3 | **Pre-flight token estimation** | Estimate prompt tokens before sending; if a call cannot fit the TPM budget, queue it rather than eat a 429. |
| 3.4 | **Content-hash cache (Postgres)** | Key: `sha256(file blob sha + agent + model + prompt version)`. Re-reviewing an unchanged file across a PR update costs zero tokens. On a `synchronize` event, only changed files are re-reviewed. This is the highest-leverage cost optimisation available. |
| 3.5 | **Per-review budget guard** | Max tokens and max wall-clock per review; on breach, finish cleanly and mark `partial` with a reason. |
| 3.6 | **Token accounting** | Record prompt/completion tokens, model, latency, and attempt count for every single LLM call into `llm_calls`. This table is what makes the observability and eval phases possible. |
| 3.7 | **Prompt versioning** | Every prompt gets a version string, stored on each `agent_run`. Without this, eval results are not comparable across prompt changes. |

**Definition of done:** a test that fans out 50 files against a fake provider
with a 10 RPM limit and asserts zero 429s, correct total call count, and that
cache hits on the second run reduce calls to zero.

---

## 8. Phase 4 — Async API and hardening

| # | Task | Detail |
|---|------|--------|
| 4.1 | **Async end-to-end** | All routes `async def`; `httpx.AsyncClient`; `ainvoke` on the graph. Today every route is sync `def`, so FastAPI runs each in the threadpool and a handful of concurrent reviews exhausts it. |
| 4.2 | **Lifespan + DI** | Build settings, DB engine, HTTP client, graph, and provider once at startup; inject via `Depends`. |
| 4.3 | **API-key auth** | `POST /api/v1/reviews` is currently wide open (`routes.py:10`) — anyone who finds the URL can burn the Groq quota and drive the GitHub token. Hashed keys in Postgres, `Authorization: Bearer`, per-key scopes. **Now a hard prerequisite for `MERGE_ENABLED=true` anywhere but a local machine:** the publish and merge endpoints (§14d) act with the server's GitHub token on behalf of whoever calls them. |
| 4.4 | **Rate limiting** | `slowapi`, per API key and per IP. |
| 4.5 | **Exception handlers** | Phase-1 typed exceptions -> RFC 9457 `application/problem+json` responses. One handler module, no `try/except` in routes. |
| 4.6 | **Request-ID middleware** | Generate/propagate `X-Request-ID`, bind it into `structlog` context and onto the job row, so one ID traces HTTP -> job -> agent runs -> LLM calls. |
| 4.7 | **Real health endpoints** | `/healthz` (liveness, no deps) and `/readyz` (checks DB + provider reachability). The current `/health` at `main.py:15` always returns healthy. |
| 4.8 | **Payload limits** | Max body size, max files per review, request timeout middleware. |

---

## 9. Phase 5 — Persistence and job queue (PostgreSQL)

### 9.1 Schema

```
repositories        id, owner, name, installation_id, created_at
                    UNIQUE (owner, name)

pull_requests       id, repository_id, number, title, description,
                    head_sha, base_sha, author, created_at
                    UNIQUE (repository_id, number, head_sha)

reviews             id, pull_request_id, status, trigger, request_id,
                    summary, is_partial, partial_reason,
                    total_tokens, estimated_cost, duration_ms,
                    started_at, finished_at
                    INDEX (pull_request_id, started_at DESC)

findings            id, review_id, file_path, line, side, severity,
                    category, agent_name, message, suggestion,
                    diff_position, was_published, verifier_verdict,
                    dedup_collapsed_count, confidence
                    INDEX (review_id, severity)

agent_runs          id, review_id, agent_name, file_path, model,
                    prompt_version, status, findings_count,
                    prompt_tokens, completion_tokens, duration_ms,
                    error, raw_response JSONB

llm_calls           id, agent_run_id, model, prompt_tokens,
                    completion_tokens, latency_ms, attempt,
                    was_rate_limited, created_at

response_cache      cache_key PK, model, prompt_version,
                    response JSONB, hit_count, created_at, last_hit_at

jobs                id, kind, payload JSONB, status, attempts,
                    locked_at, locked_by, run_after, last_error,
                    created_at
                    INDEX (status, run_after) WHERE status='pending'

api_keys            id, key_hash, name, scopes, is_active,
                    last_used_at, created_at

webhook_deliveries  delivery_id PK, event, action, payload JSONB,
                    received_at, processed_at
                    -- idempotency: GitHub retries deliveries
```

### 9.2 Tasks

| # | Task |
|---|------|
| 5.1 | SQLAlchemy 2.0 async ORM models (`Mapped[]` style, typed) + `asyncpg` |
| 5.2 | Alembic setup; every schema change ships as a migration from here on |
| 5.3 | Repository layer (`app/db/repositories/`) — routes and services never write raw queries |
| 5.4 | **Postgres job queue** — `SELECT … FROM jobs WHERE status='pending' AND run_after <= now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1`. No Redis, no Celery. Retries with `run_after` backoff, `attempts` cap, dead-letter status. |
| 5.5 | Standalone async worker (`python -m app.jobs.worker`), graceful shutdown on SIGTERM, releases its locks |
| 5.6 | `POST /reviews` returns **202 + review_id**; `GET /reviews/{id}` returns status and, when complete, the result. A 60-second blocking HTTP call is not a production API. |
| 5.7 | Wire the Phase-3 cache to `response_cache` |
| 5.8 | Webhook delivery idempotency via `webhook_deliveries` |

**Testing note:** use a real Postgres for integration tests via
`pytest-postgresql` or a locally installed instance — **not** SQLite. SQLite
does not support `SKIP LOCKED` or `JSONB`, so it would not exercise the code
that matters.

---

## 10. Phase 6 — GitHub integration, properly

| # | Task | Detail |
|---|------|--------|
| 6.1 | **Diff position mapping** | The hard, interesting part. Parse `@@ -a,b +c,d @@` hunks into a map from file line -> reviewable diff position/side. GitHub rejects inline comments on lines outside the diff, so unmappable findings must be demoted into the summary body rather than lost or rejected. Pure function, heavily unit-tested against real patches — add/delete/rename/binary/no-newline-at-EOF/multi-hunk. |
| 6.2 | **Actually post inline comments** | `GitHubService.create_pull_request_review` already accepts `comments` (`github_service.py:62`) and the docstring promises inline comments, but `_publish_review_to_github` (`review_service.py:93-99`) never passes them — it posts only a summary blob. `head_sha` is captured at `review_mapper.py:39-41` and never used; it is exactly what anchoring needs. **Shipped (§14d):** inline comments anchored with `line`/`side` + `commit_id=head_sha`; unanchorable findings go into the review body. |
| 6.3 | **GitHub App** | Replace the personal access token with a GitHub App: JWT signed with the app private key -> short-lived installation token, cached until expiry. Removes a long-lived PAT from the design and scopes access per install. |
| 6.4 | **Webhook endpoint** | `POST /webhooks/github` handling `pull_request: opened/synchronize/reopened`. **Verify the HMAC-SHA256 signature with `hmac.compare_digest` before parsing the body.** Enqueue and return 202 fast — GitHub times out slow webhooks. |
| 6.5 | **Idempotent publishing** | On `synchronize`, resolve/minimise superseded comments instead of stacking a new copy. Track `was_published` per finding. |
| 6.6 | **Batch by rate limit** | Respect GitHub's secondary rate limits when posting many comments; chunk and pace. |
| 6.7 | **Review event policy** | Config: post as `COMMENT` always, or `REQUEST_CHANGES` when a HIGH-severity finding survives verification. Default to `COMMENT` — auto-blocking merges on LLM output is a bad default and saying so is a good interview answer. **Shipped as `COMMENT`-only (§14d).** |
| 6.8 | **Suggested changes** (shipped early, §14d) | Findings carry `start_line` + `suggested_code`; posted as GitHub ```` ```suggestion ```` blocks via `line`/`side`/`start_line` (not the legacy `position` field). Ranges must sit inside one hunk or the fix is dropped and the finding kept. Severity threshold chosen per publish. 422 on an inline anchor falls back to a body-only review rather than losing it. |
| 6.9 | **Safe merge gate** (shipped early, §14d) | `app/services/merge_gate.py`: merging enabled, PR open and not draft, review complete, review current (head SHA unchanged), zero HIGH findings, mergeable, CI green. Re-evaluated server-side on fresh GitHub data at merge time; merge pinned to the reviewed SHA so GitHub enforces the race atomically. |
| 6.10 | **Gate: caller can actually merge** (added 2026-10-06, §14e) | Today all checks can pass on a repo the token cannot write to, and GitHub then answers 403. Add a `can_merge` check from the repo's `permissions.push` (or `GET /repos/{o}/{r}/collaborators/{user}/permission`) so the UI never shows a merge that cannot happen. |

---

## 11. Phase 7 — Quality: verifier + evaluation harness

**This is the differentiator.** Almost no portfolio LLM project measures itself.
A single sentence like *"a verification pass cut false positives from 38% to
11% on a 30-PR benchmark"* outweighs every other bullet in the README.

| # | Task | Detail |
|---|------|--------|
| 7.1 | **Verifier node** | A second pass that receives each finding plus its file diff and adversarially asks: is this real, is it in the changed lines, is it actionable? Drops or downgrades anything unsupported. Store `verifier_verdict` and `confidence` on each finding. |
| 7.2 | **Benchmark dataset** | ~30 real PRs in `eval/dataset/`, each as a frozen JSON fixture (PR metadata + file patches) plus a hand-labeled list of true findings. Freeze the fixtures so eval never hits the network and never costs GitHub calls. |
| 7.3 | **Metrics** | Precision, recall, F1 — overall, per agent, and per severity. Matching is fuzzy: `(file, line ± 3, semantic message match)`. Document the matching rule; it is the part a reviewer will question. |
| 7.4 | **Eval runner** | `python -m eval.runner --agents all --model X` writes a timestamped report. Compare model tiers and prompt versions against the same dataset. |
| 7.5 | **Regression gate** | Fail if precision drops more than N points versus the stored baseline. Prompt changes become measurable rather than vibes. |
| 7.6 | **Publish the numbers** | Results table in the README, with the dataset size and methodology stated honestly, including where it is weak (30 PRs is a small sample — say so). |
| 7.7 | **Validate suggested changes before posting** (added 2026-10-06, §14e) | Apply each `suggested_code` to the new-side file content and syntax-check the result (`ast.parse` for Python first; `tree-sitter` later for other languages). Drop fixes that do not parse; record the drop on the finding. Report **"% of suggestions that apply cleanly"** alongside precision/recall — few AI reviewers can quote that number. |
| 7.8 | **Human labels from the UI** (added 2026-10-06, §14e) | Per-finding "dismiss as wrong" in the frontend (10.12) writes a label against the finding. Dismissals become negative examples in `eval/dataset/`, so the benchmark grows from real use rather than only hand curation. |

---

## 12. Phase 8 — Observability

| # | Task |
|---|------|
| 8.1 | `structlog` JSON logs with request-ID/review-ID correlation throughout |
| 8.2 | `/metrics` (Prometheus text format): review count by status, agent latency histogram, tokens by model, rate-limit hits, cache hit ratio, verifier drop rate |
| 8.3 | `GET /api/v1/stats` — aggregate queries over `reviews`/`findings`/`llm_calls` (reviews/day, avg findings, token spend, p95 latency) |
| 8.4 | Optional: LangSmith tracing behind a config flag, for graph-level debugging |
| 8.5 | Cost estimation per review, stored on the row, from a config price table |

---

## 13. Testing strategy (cross-cutting — not a phase)

Every phase ships with its tests. An AI code reviewer with a weak test suite is
an interview liability — it is the first thing anyone will poke at. Current
state: 9 tests, all on pure helpers; nothing covers the service, the graph, the
LLM client, the API, or the parser's failure paths.

**Infrastructure to build early (during Phase 0/1):**

- `FakeLLMProvider` — scripted responses, including malformed JSON, empty
  output, hallucinated file paths, and simulated 429s
- `respx`-mocked GitHub API + recorded real payload fixtures
- `httpx.AsyncClient` + `ASGITransport` for route tests
- Real Postgres for integration tests (see §9.2 note), transaction-rollback
  fixtures for isolation
- `freezegun` for rate-limiter and token-bucket time control

**Required coverage by area:**

| Area | Must test |
|------|-----------|
| `github/diff.py` | Add/delete/rename/binary/multi-hunk/no-newline-at-EOF; the most test-dense module in the repo |
| Parser + validation | Malformed JSON, fences, prose-wrapped JSON, bad severity, string line numbers, unknown files |
| Graph | Fan-out width, reducer merging, one agent failing while others succeed -> `is_partial=True` |
| Rate limiter | Burst, sustained load, `Retry-After` handling, no 429s under limit |
| Cache | Hit/miss, key sensitivity to prompt version and model |
| Job queue | Concurrent workers never double-claim a job; crashed worker's lock is reclaimed |
| Webhooks | Valid signature, invalid signature rejected, duplicate delivery ignored |
| API | Auth required, error codes correct, 202 flow, no upstream text leaked |

**Target: ~85% line coverage, with the diff-mapper and merge logic at 100%.**

---

## 14. Phase 9 — Documentation

| # | Task |
|---|------|
| 9.1 | **Rewrite `README.md`** — it is currently the literal string `## First PR`, and it is the highest-leverage file in the repo. Needs: one-line pitch, architecture diagram of the graph, sample output, quickstart, eval results table, design-decisions section. |
| 9.2 | `docs/architecture.md` — the graph, the state machine, why per-file fan-out |
| 9.3 | `docs/decisions/` — short ADRs for the choices in §2 (Postgres over Mongo, Postgres queue over Redis, `COMMENT` over `REQUEST_CHANGES`) |
| 9.4 | `CONTRIBUTING.md` + local setup without Docker (local Postgres, `uv sync`, Alembic upgrade, run worker) |
| 9.5 | Rich OpenAPI: examples, descriptions, documented error responses |

---

## 14b. Phase 10 — Frontend

**Why this exists.** The backend is where the engineering is, but a reviewer
skimming a resume will not read `app/graph/build.py`. They will look at one
screenshot and one number. The frontend's job is to make the measured results
of Phase 7 visible in five seconds, and to make the system demonstrable without
a terminal.

**Scope discipline: three screens, not an application.** No user signup, no
multi-tenancy, no settings pages. Those add work and zero signal.

### Stack

Vite + React + TypeScript + Tailwind v4 + shadcn/ui (Radix) + TanStack Query. A separate `web/`
directory talking to the FastAPI backend over the API from Phase 4. No SSR, no
Next.js - there is no SEO or auth story here that would justify it, and it
keeps the no-Docker/no-cloud constraint easy.

### Screen 1 — Live review (the demo)

Paste a pull request URL, watch the review happen.

| # | Task |
|---|------|
| 10.1 | `GET /api/v1/reviews/{id}/stream` - Server-Sent Events emitting `agent.started`, `agent.completed`, `finding`, `review.completed`. FastAPI `StreamingResponse`; the worker publishes through Postgres `LISTEN/NOTIFY`, so still no Redis. |
| 10.2 | Agent progress strip: five reviewer agents plus verifier and summary, each showing queued / running / done / failed with its own latency. This is the screen that makes the multi-agent architecture *visible* rather than claimed. |
| 10.3 | Findings stream in grouped by severity, each rendered against its actual diff hunk with the flagged line highlighted (reuse the Phase 6.1 position mapping - the frontend consumes the same data the GitHub comments do). |
| 10.4 | Partial-review banner when `complete: false`, naming which agents failed. Never let the UI imply a clean result when the review was incomplete. |

### Screen 2 — Review history

| # | Task |
|---|------|
| 10.5 | Table of past reviews from Postgres: repo, PR, findings by severity, duration, tokens, cost, status. Sortable, filterable by repo. |
| 10.6 | Detail view: full findings list, per-agent breakdown, cache hit ratio, which findings were published to GitHub and which were dropped as unmappable. |
| 10.7 | Re-run button that reuses the content-hash cache, so the cost of a re-run is visibly near zero. |

### Screen 3 — Evaluation dashboard (the differentiator)

This is the screen that separates the project from every other "I built an AI
code reviewer" repo. It visualises Phase 7.

| # | Task |
|---|------|
| 10.8 | Precision / recall / F1 per agent, over time, from the `agent_runs` and eval tables. |
| 10.9 | Prompt-version comparison: pick two versions, see the metric delta on the same dataset. Makes prompt engineering measurable instead of anecdotal. |
| 10.10 | Model tier comparison on identical inputs - quality against latency against token cost. |
| 10.11 | Duplication factor and verifier drop-rate over time: the 2.2x from the §4b baseline should visibly fall. |

### Added 2026-10-06

| # | Task |
|---|------|
| 10.12 | Per-finding selection: a checkbox to include or exclude each finding from a publish (today only a severity threshold), plus "dismiss as wrong", which feeds 7.8. |
| 10.13 | Replace the indeterminate "all agents running" progress view with real per-agent state once 10.1 (SSE) exists. |

### Definition of done

A five-minute screen recording where a pull request URL goes in, agents light
up in parallel, findings stream in anchored to real diff lines, and the eval
dashboard shows the recall number. That recording goes in the README.

---

## 14c. Phase 11 — Proof and demo artifacts

Cheap, and disproportionately effective. Most of this is writing, not code.

| # | Task |
|---|------|
| 11.1 | **README screenshot and GIF at the top.** The single highest-leverage asset in the repo. |
| 11.2 | **The numbers, stated plainly**: recall on the benchmark, hallucination rate, duplication factor before and after, tokens and cost per review, p95 latency. With the dataset size and methodology, including its weaknesses. |
| 11.3 | **Clickable evidence**: links to real PRs in a public demo repo where the bot actually reviewed and commented. A recruiter can verify it in one click. |
| 11.4 | **Architecture diagram** of the graph and the request flow, in the README, not buried in `docs/`. |
| 11.5 | **Multi-language proof.** Everything so far has been tested on Python. Run the benchmark on TypeScript, Go and Java files and publish per-language recall - `FileAnalyzer` already detects 25+ languages, so this is prompt and eval work, not new code. |
| 11.6 | **Comparison against an existing tool.** Run the same benchmark PRs through a commercial reviewer and publish the comparison honestly, including where yours loses. Bold, and very few portfolio projects do it. |
| 11.7 | **ADRs for the decisions that have real trade-offs** (Postgres over Mongo, Postgres queue over Redis, diff-only over repo-wide context, `COMMENT` over `REQUEST_CHANGES`). Shows judgement, not just execution. |

---

## 14d. Shipped early: frontend MVP, suggested changes, safe merge (2026-10-06)

Built ahead of Phases 4-6 on request, against the existing synchronous API.
Recorded here so the deviations from the plan are explicit, not forgotten.

**What exists**

- `web/`: three screens (paste URL, review results, history) using the
  project theme. Verdict, severity mix, per-agent status, findings rendered
  against their real diff lines, suggested change before/after, partial-review
  banner (10.4). Design source: the Claude Design canvas "AI Code Reviewer UI".
- API: `POST/GET /api/v1/reviews`, `GET /reviews/{id}`, `POST /reviews/{id}/publish`,
  `GET /reviews/{id}/merge-readiness`, `POST /reviews/{id}/merge`. The legacy
  `POST /api/v1/review` is unchanged.
- 6.8 suggested changes and 6.9 safe merge (see §10).
- Reviewer prompts now show **new-file line numbers** beside every diff line
  (`annotate_patch`), because a suggestion must replace an exact range.
  **This changes the prompts the §4b baseline was measured on: re-run
  `eval/positive_control.py` before comparing numbers.**
- Settings: `SUGGESTION_MIN_SEVERITY`, `MERGE_ENABLED` (default **false**),
  `MERGE_METHOD` (default `squash`).
- Tests: 29 → 61 passing (`test_suggestions.py`, `test_merge_gate.py`,
  `test_review_api.py`). The 6 pre-existing failures are tracked as 1.13.

**Deliberate stopgaps, each with the phase that replaces it**

| Stopgap | Why acceptable now | Replaced by |
|---|---|---|
| In-memory `ReviewStore` (bounded, lost on restart) | Publish and merge must act on the server's copy of a review, never on values echoed by the browser | Phase 5 (`reviews`, `findings` tables) |
| `POST /reviews` blocks for the whole review (~30s on a 1-file PR) | Works for small PRs locally | 5.6 (202 + job id) and 10.1 (SSE) |
| No authentication; merging off by default | Local-only use | 4.3, which is now a prerequisite for enabling merge |
| Re-publishing posts a second copy (`force=true`) | Explicit, never automatic | 6.5 idempotent publishing |
| Progress view shows all agents as running | Honest: the backend reports nothing until the end | 10.13 |
| No evaluation screen | Phase 7 has no data to show yet | 10.8-10.11 |

---

## 14e. Re-prioritisation (2026-10-06)

Building the frontend against real PRs surfaced problems that now outrank the
original phase order. Work in this order:

| Priority | Items | Why now |
|---|---|---|
| **P1 — Stabilise** (hours) | 1.13 green the suite, 1.14 GitHub retries, 6.10 merge-permission check | A red suite hides regressions; retries silently do nothing; the merge gate can show "ready" when GitHub will refuse. All small, all correctness. |
| **P2 — Work on real PRs** | **2.1 per-file fan-out** (+ 2.2 reducers), then 5.6 + 10.1 async review with SSE progress | Every file's diff still goes into one prompt per agent, so medium and large PRs exceed the context window or Groq's TPM. This is the main blocker to reviewing arbitrary repositories. A blocking 30s+ request is the next. |
| **P3 — Trustworthy suggestions** (the differentiator) | 7.7 validate suggestions, 10.12 per-finding selection, 7.8 human labels, 7.1 verifier | Suggested changes are now the most visible output. Proving they apply cleanly is a measurable, rare claim; per-finding dismissal turns everyday use into eval data. |
| **P4 — Before anyone else uses it** | 4.3 auth, Phase 5 persistence, 6.5 idempotent publishing | Required before the API leaves a single local machine, and before `MERGE_ENABLED=true` anywhere shared. |
| **P5 — Later** | 6.3 GitHub App, 6.4 webhooks, Screen 3 (10.8-10.11) | Automatic review on PR open; the eval dashboard needs Phase 7 data first. |

---

## 15. Deferred backlog (deliberately not now)

Recorded so it is clear these are **decisions**, not oversights.

| Item | Why deferred | Revisit when |
|------|--------------|--------------|
| Docker / docker-compose | Per project decision; local Postgres is enough for development | Before any deployment |
| Cloud deployment | Per project decision | After Phase 6 is stable |
| GitHub Actions CI | Not blocking; the same `ruff`/`mypy`/`pytest` gate runs via `pre-commit` locally | Worth adding once Phase 5 lands — a green badge does real work on a resume |
| Redis | Postgres covers both the queue and the cache; one less dependency | Only if queue throughput becomes a real bottleneck |
| Multi-provider LLM fallback | Groq only for now; the `LLMProvider` seam is built so this is a new file, not a refactor | If a second provider becomes available |
| Embedding-based dedup | `rapidfuzz` is likely sufficient and has no extra dependency | If eval shows dedup is the bottleneck |
| Repo-wide context (beyond the diff) | Large scope increase; diff-only review is a defensible, explicit design choice | After the eval harness can measure whether it helps |

---

## 16. Sequencing

```
Phase 0  (cleanup)          -> unblocks everything
Phase 1  (correctness)      -> depends on 0
Phase 2  (architecture)     -> depends on 1
Phase 3  (Groq limits)      -> depends on 2  [cache needs 5.1-5.2]
Phase 4  (async API)        -> depends on 1
Phase 5  (Postgres + jobs)  -> depends on 4
Phase 6  (GitHub App)       -> depends on 5
Phase 7  (verifier + eval)  -> depends on 2; do NOT defer to the end
Phase 8  (observability)    -> depends on 5
Phase 9  (docs)             -> continuous; README rewrite after Phase 7
Phase 10 (frontend)         -> depends on 5 (history) and 7 (eval screen)
Phase 11 (proof artifacts)  -> depends on 10 for the GIF; 11.2/11.5 can start
                               as soon as Phase 7 exists
```

Two notes on ordering:

- **Phase 7 is not last.** Build the eval harness as soon as the Phase-2
  architecture exists. Every later prompt and model decision should be measured
  against it, otherwise those decisions are guesses.
- **§14e overrides the strict phase order above** where they conflict: P1
  first, then 2.1, then async review, then suggestion validation.
- **Phase 0 is not optional.** Four dead modules (two of them empty files) in a
  ~700-line project is the cheapest possible quality win, and it is what a
  reader sees first.

---

## 17. Progress tracker

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Cleanup and foundation | ☑ **complete** |
| 1 | Correctness and resilience | ☑ **complete** (2026-10-27, §5b) |
| 2 | Per-file map-reduce architecture | ☑ **complete** (2026-10-27, §6b) |
| 3 | Groq-constrained execution | ☐ not started |
| 4 | Async API and hardening | ☐ not started |
| 5 | PostgreSQL + job queue | ☐ not started |
| 6 | GitHub App + inline comments | ◐ 6.2/6.7/6.8/6.9 shipped early (§14d); 6.10 **P1** |
| 7 | Verifier + evaluation harness | ☐ not started (7.7/7.8/7.1 are **P3**) |
| 8 | Observability | ☐ not started |
| 9 | Documentation | ☐ not started |
| 10 | Frontend (3 screens + SSE streaming) | ◐ MVP shipped (§14d): screens 1-2 without SSE/persistence; screen 3 not started |
| 11 | Proof and demo artifacts | ☐ not started |

---

## 18. Dependencies to add

```
# core  (pydantic-settings, httpx, structlog: DONE in Phase 0)
pydantic-settings, httpx, structlog       # installed + pinned
tenacity, rapidfuzz, pyyaml, groq         # Phase 1-2: installed + pinned

# database
sqlalchemy[asyncio]>=2.0, asyncpg, alembic

# api
slowapi, python-multipart

# github app auth
pyjwt[crypto]

# dev  (ruff, mypy, pre-commit, pytest-cov, respx: DONE in Phase 0)
ruff, mypy, pre-commit, pytest-cov, respx  # installed + pinned
pytest-asyncio, freezegun, pytest-postgresql  # Phase 4-5

# removed in Phase 0 (never imported directly)
openai, groq, requests
```


Pin exact versions in `pyproject.toml` and commit the lock file.
