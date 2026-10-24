import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, ExternalLink, PencilLine, Send } from "lucide-react"
import { useMemo } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { api, type ReviewDetail, type Severity } from "@/lib/api"
import { anchorableLines } from "@/lib/diff"
import { atOrAbove } from "@/lib/severity"

const LEVELS: { value: Severity; label: string }[] = [
  { value: "HIGH", label: "High only" },
  { value: "MEDIUM", label: "Medium +" },
  { value: "LOW", label: "All" },
]

interface Props {
  review: ReviewDetail
  threshold: Severity
  onThresholdChange: (s: Severity) => void
}

export function PublishPanel({ review, threshold, onThresholdChange }: Props) {
  const queryClient = useQueryClient()
  const pr = review.pull_request

  // Same split the backend makes in app/services/pr_publisher.py.
  const plan = useMemo(() => {
    const anchors = new Map(review.files.map((f) => [f.filename, anchorableLines(f.patch)]))
    let suggestions = 0
    let comments = 0
    let summaryOnly = 0
    for (const f of review.findings) {
      if (!atOrAbove(f, threshold)) continue
      const inline = f.line !== null && (anchors.get(f.file)?.has(f.line) ?? false)
      if (!inline) summaryOnly += 1
      else if (f.suggested_code) suggestions += 1
      else comments += 1
    }
    return { suggestions, comments, summaryOnly, total: suggestions + comments + summaryOnly }
  }, [review, threshold])

  const publish = useMutation({
    mutationFn: (force: boolean) => api.publish(review.id, threshold, force),
    onSuccess: (updated) => {
      queryClient.setQueryData(["review", review.id], updated)
      queryClient.invalidateQueries({ queryKey: ["reviews"] })
      const p = updated.publication
      toast.success(`Posted to PR #${pr.number}`, {
        description: p ? `${p.suggested_changes} suggested changes, ${p.inline_comments} inline comments.` : undefined,
      })
    },
    onError: (e) => toast.error("Couldn't post to GitHub", { description: e.message }),
  })

  const pub = review.publication

  return (
    <Card className="gap-3.5 p-5">
      <div className="flex items-center gap-2.5">
        <PencilLine className="size-4.5" aria-hidden />
        <h2 className="text-base font-semibold">Suggest changes on PR</h2>
      </div>
      <p className="text-sm text-muted-foreground">
        Post inline comments with GitHub suggested changes. The author applies each fix with one click.
      </p>

      <ToggleGroup
        type="single"
        variant="outline"
        value={threshold}
        onValueChange={(v) => v && onThresholdChange(v as Severity)}
        aria-label="Lowest severity to post"
        className="w-full"
      >
        {LEVELS.map((l) => (
          <ToggleGroupItem
            key={l.value}
            value={l.value}
            className="h-10 flex-1 data-[state=on]:border-ring data-[state=on]:bg-cta/15 data-[state=on]:font-semibold data-[state=on]:text-foreground"
          >
            {l.label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      <div className="grid grid-cols-2 gap-2.5">
        <Tally value={plan.suggestions} label="suggested changes" />
        <Tally value={plan.comments + plan.summaryOnly} label="comment-only findings" />
      </div>

      {pub ? (
        <div className="flex flex-col gap-2.5 rounded-lg bg-success/10 p-3 text-sm">
          <span className="flex items-center gap-2 font-semibold text-success">
            <CheckCircle2 className="size-4" aria-hidden />
            Posted {new Date(pub.published_at).toLocaleString()}
          </span>
          <span className="text-muted-foreground">
            {pub.suggested_changes} suggestions · {pub.inline_comments} inline · {pub.body_findings} in summary (
            {pub.min_severity.toLowerCase()} and above)
          </span>
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline" size="lg">
              <a href={pub.html_url} target="_blank" rel="noreferrer">
                View on GitHub <ExternalLink />
              </a>
            </Button>
            <Button variant="ghost" size="lg" disabled={publish.isPending} onClick={() => publish.mutate(true)}>
              Post again
            </Button>
          </div>
        </div>
      ) : (
        <Button
          size="lg"
          className="h-11 bg-cta text-white hover:bg-cta/85"
          disabled={plan.total === 0 || publish.isPending}
          onClick={() => publish.mutate(false)}
        >
          <Send />
          {publish.isPending
            ? "Posting…"
            : plan.total === 0
              ? "Nothing at this level"
              : `Post ${plan.total} comment${plan.total === 1 ? "" : "s"} to PR #${pr.number}`}
        </Button>
      )}
      <p className="text-xs text-muted-foreground">
        Posted as a COMMENT review. The bot never approves or requests changes on its own.
      </p>
    </Card>
  )
}

function Tally({ value, label }: { value: number; label: string }) {
  return (
    <div className="rounded-lg bg-muted p-3">
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  )
}
