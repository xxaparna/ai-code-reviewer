// Typed client for the FastAPI backend (app/api/routes.py). Shapes mirror
// app/api/schemas.py; keep the two in step.

export type Severity = "HIGH" | "MEDIUM" | "LOW"

export interface Finding {
  id: string
  file: string
  line: number | null
  start_line: number | null
  severity: Severity
  category: string
  message: string
  suggestion: string | null
  suggested_code: string | null
  agent: string | null
  dedup_collapsed_count: number
}

export interface ChangedFile {
  filename: string
  status: string
  language: string
  additions: number
  deletions: number
  patch: string
}

export interface PullRequest {
  owner: string
  repo: string
  number: number
  title: string
  url: string
  author: string
  head_ref: string
  base_ref: string
  head_sha: string
}

export interface Publication {
  published_at: string
  html_url: string
  inline_comments: number
  suggested_changes: number
  body_findings: number
  min_severity: Severity
}

export interface MergeInfo {
  merged_at: string
  sha: string
  merge_method: string
}

export interface ReviewMetadata {
  total_files?: number
  total_comments?: number
  hallucinated_comments_dropped?: number
  severity_counts?: Partial<Record<Severity, number>>
  category_counts?: Record<string, number>
  errors?: string[]
  complete?: boolean
  unreviewable?: boolean
  partial?: boolean
  partial_reason?: string | null
  reviewed_files?: number
  skipped_files?: { file: string; reason: string }[]
  duplicates_collapsed?: number
  agents?: string[]
}

export interface ReviewDetail {
  id: string
  created_at: string
  duration_ms: number
  pull_request: PullRequest
  summary: string
  findings: Finding[]
  files: ChangedFile[]
  metadata: ReviewMetadata
  publication: Publication | null
  merge: MergeInfo | null
}

export interface ReviewListItem {
  id: string
  created_at: string
  duration_ms: number
  pull_request: PullRequest
  severity_counts: Partial<Record<Severity, number>>
  complete: boolean
  published: boolean
  merged: boolean
}

export interface GateCheck {
  key: string
  label: string
  passed: boolean
  detail: string
}

export interface MergeReadiness {
  ready: boolean
  reviewed_sha: string
  current_sha: string
  merge_method: string
  checks: GateCheck[]
}

export class ApiError extends Error {
  readonly status: number
  readonly checks: GateCheck[] | null

  constructor(status: number, message: string, checks: GateCheck[] | null = null) {
    super(message)
    this.status = status
    this.checks = checks
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    })
  } catch {
    throw new ApiError(0, "Can't reach the API. Is the backend running on port 8000?")
  }

  if (response.ok) return (await response.json()) as T

  let message = `Request failed (${response.status})`
  let checks: GateCheck[] | null = null
  try {
    const body = (await response.json()) as { detail?: unknown }
    const detail = body.detail
    if (typeof detail === "string") message = detail
    else if (detail && typeof detail === "object" && "message" in detail) {
      message = String((detail as { message: unknown }).message)
      checks = ((detail as { checks?: GateCheck[] }).checks ?? null)
    } else if (Array.isArray(detail)) {
      // FastAPI validation errors
      message = detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join("; ") || message
    }
  } catch {
    // non-JSON error body: keep the generic message
  }
  if (response.status === 429) {
    const retry = response.headers.get("Retry-After")
    if (retry) message += ` Retry in ${retry}s.`
  }
  throw new ApiError(response.status, message, checks)
}

export const api = {
  createReview: (prUrl: string) =>
    request<ReviewDetail>("/reviews", { method: "POST", body: JSON.stringify({ pr_url: prUrl }) }),
  getReview: (id: string) => request<ReviewDetail>(`/reviews/${encodeURIComponent(id)}`),
  listReviews: () => request<ReviewListItem[]>("/reviews"),
  publish: (id: string, minSeverity: Severity, force = false) =>
    request<ReviewDetail>(`/reviews/${encodeURIComponent(id)}/publish`, {
      method: "POST",
      body: JSON.stringify({ min_severity: minSeverity, force }),
    }),
  mergeReadiness: (id: string) =>
    request<MergeReadiness>(`/reviews/${encodeURIComponent(id)}/merge-readiness`),
  merge: (id: string, commitTitle: string | null) =>
    request<ReviewDetail>(`/reviews/${encodeURIComponent(id)}/merge`, {
      method: "POST",
      body: JSON.stringify({ commit_title: commitTitle || null }),
    }),
}

const PR_URL_RE = /^https:\/\/(www\.)?github\.com\/[^/\s]+\/[^/\s]+\/pull\/\d+(\/(files|commits|checks))?\/?$/

export function isPullRequestUrl(value: string): boolean {
  return PR_URL_RE.test(value.trim())
}
