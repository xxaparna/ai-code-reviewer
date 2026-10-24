import { useQuery } from "@tanstack/react-query"
import { CircleCheck, Sparkles, TriangleAlert } from "lucide-react"
import { useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"

import { FindingCard } from "@/components/finding-card"
import { MergePanel } from "@/components/merge-panel"
import { PublishPanel } from "@/components/publish-panel"
import { ReviewOverview } from "@/components/review-overview"
import { SeverityBar } from "@/components/severity-badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ApiError, api, type ReviewDetail, type Severity } from "@/lib/api"
import { SEVERITIES, SEVERITY_META, atOrAbove, countBySeverity } from "@/lib/severity"

type Filter = "ALL" | Severity

export function ReviewPage() {
  const { id = "" } = useParams()
  const review = useQuery({ queryKey: ["review", id], queryFn: () => api.getReview(id), retry: false })

  if (review.isPending) return <ReviewSkeleton />
  if (review.isError) {
    const gone = review.error instanceof ApiError && review.error.status === 404
    return (
      <Alert variant="destructive" className="mx-auto max-w-2xl">
        <TriangleAlert />
        <AlertTitle>{gone ? "Review not found" : "Couldn't load this review"}</AlertTitle>
        <AlertDescription className="flex flex-col items-start gap-3">
          {gone
            ? "Reviews are kept in server memory and are lost when the API restarts."
            : review.error.message}
          <Button asChild variant="outline" size="lg">
            <Link to="/">Start a new review</Link>
          </Button>
        </AlertDescription>
      </Alert>
    )
  }
  return <ReviewBody review={review.data} />
}

function ReviewBody({ review }: { review: ReviewDetail }) {
  const [filter, setFilter] = useState<Filter>("ALL")
  const [threshold, setThreshold] = useState<Severity>("MEDIUM")
  const counts = countBySeverity(review.findings)
  const files = useMemo(() => new Map(review.files.map((f) => [f.filename, f])), [review.files])
  const visible = review.findings.filter((f) => filter === "ALL" || f.severity === filter)

  return (
    <div className="flex flex-col gap-6">
      <Card className="gap-2.5 p-5 sm:p-6">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Sparkles className="size-4.5 text-chart-1" aria-hidden />
          AI summary
        </h2>
        <p className="leading-relaxed whitespace-pre-line text-muted-foreground">{review.summary}</p>
      </Card>

      <ReviewOverview review={review} />

      <div className="flex flex-wrap items-start gap-6">
        <section aria-label="Findings" className="flex min-w-0 flex-[999_1_560px] flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">Findings</h2>
            <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)}>
              <TabsList aria-label="Filter by severity" className="h-10">
                <TabsTrigger value="ALL" className="px-3.5">
                  All <span className="text-muted-foreground">{review.findings.length}</span>
                </TabsTrigger>
                {SEVERITIES.map((s) => (
                  <TabsTrigger key={s} value={s} className="px-3.5">
                    {SEVERITY_META[s].label} <span className="text-muted-foreground">{counts[s]}</span>
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </div>

          {review.findings.length === 0 ? (
            <Card className="items-center gap-2 p-10 text-center">
              <CircleCheck className="size-8 text-success" aria-hidden />
              <p className="font-semibold">No findings</p>
              <p className="max-w-[48ch] text-sm text-muted-foreground">
                {review.metadata.complete === false
                  ? "But the review was incomplete, so this is not a clean bill of health."
                  : "No agent found anything worth flagging in the changed lines."}
              </p>
            </Card>
          ) : visible.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No {filter.toLowerCase()} findings.</p>
          ) : (
            visible.map((f) => (
              <FindingCard key={f.id} finding={f} file={files.get(f.file)} willPost={atOrAbove(f, threshold)} />
            ))
          )}
        </section>

        <aside className="flex min-w-0 flex-[1_1_340px] flex-col gap-4">
          <PublishPanel review={review} threshold={threshold} onThresholdChange={setThreshold} />
          <MergePanel review={review} />

          <Card className="gap-3 p-5">
            <h2 className="text-base font-semibold">Files</h2>
            {review.files.map((file) => {
              const c = countBySeverity(review.findings.filter((x) => x.file === file.filename))
              const total = c.HIGH + c.MEDIUM + c.LOW
              return (
                <div key={file.filename} className="flex flex-col gap-1.5">
                  <div className="flex justify-between gap-2 font-mono text-[13px]">
                    <span className="min-w-0 break-all">{file.filename}</span>
                    <span className="shrink-0">
                      <span className="text-success">+{file.additions}</span>{" "}
                      <span className="text-sev-high">−{file.deletions}</span>
                    </span>
                  </div>
                  <SeverityBar counts={c} className="h-2" />
                  <span className="text-xs text-muted-foreground">
                    {total === 0 ? "no findings" : `${c.HIGH} high · ${c.MEDIUM} medium · ${c.LOW} low`}
                  </span>
                </div>
              )
            })}
          </Card>
        </aside>
      </div>
    </div>
  )
}

function ReviewSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-busy>
      <Skeleton className="h-5 w-80" />
      <div className="flex flex-wrap gap-6">
        <Skeleton className="h-72 flex-[999_1_560px]" />
        <Skeleton className="h-72 flex-[1_1_320px]" />
      </div>
      <Skeleton className="h-16" />
      <Skeleton className="h-60" />
    </div>
  )
}
