import { CircleCheck, CircleX, ExternalLink, GitBranch, TriangleAlert } from "lucide-react"

import { SeverityBar } from "@/components/severity-badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card } from "@/components/ui/card"
import type { ReviewDetail, Severity } from "@/lib/api"
import { SEVERITIES, SEVERITY_META, countBySeverity } from "@/lib/severity"
import { cn } from "@/lib/utils"

const AGENTS = [
  { key: "bug", label: "Bug", category: "BUG" },
  { key: "security", label: "Security", category: "SECURITY" },
  { key: "performance", label: "Performance", category: "PERFORMANCE" },
  { key: "architecture", label: "Architecture", category: "ARCHITECTURE" },
  { key: "style", label: "Style", category: "STYLE" },
  { key: "summary", label: "Summary", category: null },
] as const

function failedAgents(errors: string[]): Set<string> {
  // Nodes report errors as "<agent>_comments: ..." or "summary: ...".
  const failed = new Set<string>()
  for (const e of errors) {
    const key = e.split(":")[0].replace(/_comments$/, "").trim()
    failed.add(key)
  }
  return failed
}

function verdict(review: ReviewDetail, counts: Record<Severity, number>) {
  if (review.metadata.unreviewable)
    return { tone: "muted", label: "Nothing to review", detail: "This pull request has no reviewable patch content." }
  if (review.metadata.complete === false)
    return {
      tone: "high",
      label: "Review incomplete",
      detail:
        review.metadata.partial_reason && !(review.metadata.errors ?? []).length
          ? `${review.metadata.partial_reason} A clean result here does not mean clean code.`
          : "Some agents failed, so a clean result here does not mean clean code. Re-run the review.",
    }
  if (counts.HIGH)
    return {
      tone: "high",
      label: "Merge blocked",
      detail: `${counts.HIGH} high-severity ${counts.HIGH === 1 ? "issue needs" : "issues need"} fixing before this can merge safely.`,
    }
  if (counts.MEDIUM)
    return {
      tone: "medium",
      label: "Mergeable with care",
      detail: `No high-severity issues. ${counts.MEDIUM} medium ${counts.MEDIUM === 1 ? "finding is" : "findings are"} worth fixing first.`,
    }
  return { tone: "ok", label: "Looks safe to merge", detail: "No high or medium findings from any agent." }
}

const TONE = {
  high: "bg-sev-high/15 text-sev-high",
  medium: "bg-sev-medium/15 text-sev-medium",
  ok: "bg-success/15 text-success",
  muted: "bg-muted text-muted-foreground",
} as const

export function ReviewOverview({ review }: { review: ReviewDetail }) {
  const pr = review.pull_request
  const counts = countBySeverity(review.findings)
  const v = verdict(review, counts)
  const errors = review.metadata.errors ?? []
  const failed = failedAgents(errors)
  const withFix = review.findings.filter((f) => f.suggested_code).length
  const rawCount = review.findings.reduce((n, f) => n + f.dedup_collapsed_count, 0)
  const changedLines = review.files.reduce((n, f) => n + f.additions + f.deletions, 0)

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <GitBranch className="size-3.5" aria-hidden />
          {pr.head_ref || "head"} → {pr.base_ref || "base"}
        </span>
        <span aria-hidden>·</span>
        <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">
          {pr.owner}/{pr.repo} #{pr.number}
          <ExternalLink className="size-3" aria-hidden />
        </a>
        {pr.author && (
          <>
            <span aria-hidden>·</span>
            <span>by {pr.author}</span>
          </>
        )}
        <span aria-hidden>·</span>
        <span className="font-mono">reviewed {pr.head_sha.slice(0, 7)}</span>
      </div>

      {errors.length > 0 && (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>
            Partial review: {failed.size} agent{failed.size === 1 ? "" : "s"} failed
            {errors.length > failed.size ? ` (${errors.length} file reviews)` : ""}
          </AlertTitle>
          <AlertDescription>
            {[...failed].join(", ")} did not finish. Findings below are incomplete, and merging is blocked until a
            complete re-run.
          </AlertDescription>
        </Alert>
      )}

      <section className="flex flex-wrap items-stretch gap-6">
        <Card className="min-w-0 flex-[999_1_560px] gap-5 p-7">
          <div className="flex flex-wrap items-center gap-3">
            <span className={cn("inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-semibold", TONE[v.tone as keyof typeof TONE])}>
              {v.tone === "ok" ? <CircleCheck className="size-4" aria-hidden /> : <TriangleAlert className="size-4" aria-hidden />}
              {v.label}
            </span>
            <span className="text-sm text-muted-foreground">
              {review.files.length} file{review.files.length === 1 ? "" : "s"} · {changedLines} changed lines ·{" "}
              {AGENTS.length - failed.size} of {AGENTS.length} agents completed
            </span>
          </div>
          <h1 className="font-serif text-3xl leading-tight font-semibold tracking-tight text-balance sm:text-4xl">
            {pr.title}
          </h1>
          <p className="max-w-[64ch] text-muted-foreground">
            {v.detail}
            {withFix > 0 &&
              ` ${withFix} of the ${review.findings.length} findings come with a ready-to-apply suggested change.`}
          </p>
          <div className="flex flex-col gap-2.5">
            <SeverityBar counts={counts} />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {SEVERITIES.map((s) => (
                <div key={s} className="flex flex-col gap-0.5 rounded-lg bg-muted px-3.5 py-3">
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className={cn("size-2.5 rounded-sm", SEVERITY_META[s].fill)} aria-hidden />
                    {SEVERITY_META[s].label} · {SEVERITY_META[s].hint}
                  </span>
                  <span className={cn("text-3xl font-bold", SEVERITY_META[s].text)}>{counts[s]}</span>
                </div>
              ))}
            </div>
          </div>
        </Card>

        <div className="grid min-w-0 flex-[1_1_320px] grid-cols-2 gap-3">
          <Stat label="Raw → after dedup" value={`${rawCount} → ${review.findings.length}`} note={`${rawCount - review.findings.length} cross-agent repeats merged`} accent />
          <Stat label="Hallucinations dropped" value={String(review.metadata.hallucinated_comments_dropped ?? 0)} note="lines outside the diff are rejected" />
          <Stat label="Suggested changes" value={String(withFix)} note="one-click apply on GitHub" />
          <Stat label="Review time" value={`${(review.duration_ms / 1000).toFixed(1)}s`} note={`${AGENTS.length} agents in parallel`} />
        </div>
      </section>

      <section aria-label="Agents" className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-2.5">
        {AGENTS.map((a) => {
          const isFailed = failed.has(a.key)
          const n = a.category ? (review.metadata.category_counts?.[a.category] ?? 0) : null
          return (
            <div key={a.key} className="flex items-center gap-2.5 rounded-lg border bg-muted px-3.5 py-3">
              {isFailed ? (
                <CircleX className="size-5 shrink-0 text-sev-high" aria-label="failed" />
              ) : (
                <CircleCheck className="size-5 shrink-0 text-success" aria-label="completed" />
              )}
              <span className="flex min-w-0 flex-col">
                <span className="font-semibold">{a.label}</span>
                <span className="text-xs text-muted-foreground">
                  {isFailed ? "failed" : n === null ? "done" : n === 0 ? "no issues" : `${n} ${a.category?.toLowerCase()} finding${n === 1 ? "" : "s"}`}
                </span>
              </span>
            </div>
          )
        })}
      </section>
    </div>
  )
}

function Stat({ label, value, note, accent }: { label: string; value: string; note: string; accent?: boolean }) {
  return (
    <Card className="gap-1.5 p-4.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-2xl font-bold">{value}</span>
      <span className={cn("text-xs", accent ? "text-success" : "text-muted-foreground")}>{note}</span>
    </Card>
  )
}
