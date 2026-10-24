# reviewer/ai — web

The frontend for the AI code reviewer: paste a pull request URL, see findings by
severity against their real diff lines, post fixes back to the PR as GitHub
suggested changes, and merge through a server-enforced safety gate.

Vite + React + TypeScript, Tailwind v4, shadcn/ui (Radix), TanStack Query.

## Run locally

```bash
# 1. backend (repo root)
.venv/bin/uvicorn app.main:app --port 8000

# 2. frontend
cd web
npm install
npm run dev          # http://localhost:5173
```

Vite proxies `/api` to `http://127.0.0.1:8000`, so the backend needs no CORS setup.

## Screens

| Route | What it does |
|---|---|
| `/` | Paste a PR URL; agents run, then you land on the results |
| `/reviews/:id` | Verdict, severity mix, per-agent status, findings with diff context and suggested changes, publish and merge panels |
| `/history` | Reviews held by the running API |

## Publishing and merging

- **Suggest changes on PR** posts one `COMMENT` review. Findings at or above the
  chosen severity go inline; those with a drop-in fix become GitHub
  `suggestion` blocks the author applies with one click. Findings that cannot be
  anchored to a diff line are listed in the review body.
- **Safe merge** is decided by the server (`app/services/merge_gate.py`), which
  re-checks everything on fresh GitHub data at the moment of merging:
  merging enabled, PR open and not a draft, review complete, review current
  (no new commits), zero HIGH findings, no conflicts, CI green. The merge is
  pinned to the reviewed commit SHA, so a push in between makes GitHub refuse it.
- Merging is **off by default** (`MERGE_ENABLED=false`): the API has no
  authentication yet (PLAN.md §4.3). Enable it only on a machine you control.

Reviews live in server memory until Phase 5 adds Postgres, so they disappear
when the API restarts.
