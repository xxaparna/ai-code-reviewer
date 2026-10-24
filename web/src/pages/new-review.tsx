import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowRight, Loader2, TriangleAlert } from "lucide-react"
import { useEffect, useState, type FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"

import { SeverityBar } from "@/components/severity-badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, isPullRequestUrl } from "@/lib/api"

const AGENTS = ["Bug", "Security", "Performance", "Architecture", "Style"]

function useElapsed(running: boolean) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    if (!running) return
    const started = Date.now()
    const t = setInterval(() => setSeconds((Date.now() - started) / 1000), 100)
    return () => {
      clearInterval(t)
      setSeconds(0)
    }
  }, [running])
  return seconds
}

function ReviewInProgress({ url }: { url: string }) {
  const elapsed = useElapsed(true)
  return (
    <Card aria-live="polite" className="gap-4.5 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-base font-semibold">Reviewing</h2>
        <span className="min-w-0 truncate text-sm text-muted-foreground">{url}</span>
        <span className="ml-auto font-mono text-sm text-muted-foreground tabular-nums">{elapsed.toFixed(1)}s</span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-2.5">
        {AGENTS.map((a) => (
          <div key={a} className="flex items-center gap-3 rounded-lg bg-muted px-3.5 py-3">
            <Loader2 className="size-5 shrink-0 animate-spin text-chart-1" aria-hidden />
            <span className="flex flex-col">
              <span className="font-semibold">{a}</span>
              <span className="text-xs text-muted-foreground">reviewing…</span>
            </span>
          </div>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">
        All five agents run in parallel, then findings are deduplicated, checked against the diff, and summarised.
        Large pull requests can take a minute.
      </p>
    </Card>
  )
}

export function NewReviewPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [url, setUrl] = useState("")
  const [touched, setTouched] = useState(false)
  const valid = isPullRequestUrl(url)

  const recent = useQuery({ queryKey: ["reviews"], queryFn: api.listReviews })

  const create = useMutation({
    mutationFn: (prUrl: string) => api.createReview(prUrl),
    onSuccess: (review) => {
      queryClient.setQueryData(["review", review.id], review)
      queryClient.invalidateQueries({ queryKey: ["reviews"] })
      navigate(`/reviews/${review.id}`)
    },
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    setTouched(true)
    if (valid && !create.isPending) create.mutate(url.trim())
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-10 pt-10">
      <div className="flex flex-col items-start gap-4">
        <span className="text-xs tracking-[0.12em] text-success uppercase">Five agents · one verdict</span>
        <h1 className="font-serif text-4xl leading-[1.08] font-semibold tracking-tight text-balance sm:text-5xl">
          Is this pull request <em className="text-chart-1">safe to merge?</em>
        </h1>
        <p className="max-w-[60ch] text-muted-foreground">
          Paste a GitHub PR. Bug, security, performance, architecture and style agents review every changed line, then
          you choose which fixes to post back as suggested changes.
        </p>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-2.5" noValidate>
        <Label htmlFor="pr-url">Pull request URL</Label>
        <div className="flex flex-wrap gap-2.5">
          <Input
            id="pr-url"
            type="url"
            inputMode="url"
            autoFocus
            placeholder="https://github.com/owner/repo/pull/123"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onBlur={() => setTouched(true)}
            aria-invalid={touched && url !== "" && !valid}
            aria-describedby="pr-url-hint"
            className="h-13 min-w-0 flex-[1_1_360px] bg-card px-4 text-[15px]"
            disabled={create.isPending}
          />
          <Button type="submit" size="lg" className="h-13 bg-cta px-5.5 text-white hover:bg-cta/85" disabled={create.isPending}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <ArrowRight />}
            Review PR
          </Button>
        </div>
        <p id="pr-url-hint" className="text-sm text-muted-foreground">
          {touched && url !== "" && !valid
            ? "That doesn't look like a pull request URL. Expected https://github.com/owner/repo/pull/123"
            : "Nothing is posted to GitHub until you choose to."}
        </p>
      </form>

      {create.isPending && <ReviewInProgress url={url} />}

      {create.isError && (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Review failed</AlertTitle>
          <AlertDescription>{create.error.message}</AlertDescription>
        </Alert>
      )}

      {!create.isPending && recent.data && recent.data.length > 0 && (
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">Recent reviews</h2>
            <Link to="/history" className="text-sm text-muted-foreground hover:text-foreground">
              All history →
            </Link>
          </div>
          {recent.data.slice(0, 3).map((r) => (
            <Link
              key={r.id}
              to={`/reviews/${r.id}`}
              className="flex flex-col gap-2 rounded-lg border p-3.5 transition-colors hover:bg-card"
            >
              <span className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="font-semibold">{r.pull_request.title}</span>
                <span className="text-muted-foreground">
                  {r.pull_request.owner}/{r.pull_request.repo} #{r.pull_request.number}
                </span>
              </span>
              <SeverityBar
                className="h-2"
                counts={{ HIGH: r.severity_counts.HIGH ?? 0, MEDIUM: r.severity_counts.MEDIUM ?? 0, LOW: r.severity_counts.LOW ?? 0 }}
              />
            </Link>
          ))}
        </section>
      )}
    </div>
  )
}
