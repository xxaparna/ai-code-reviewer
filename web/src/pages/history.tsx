import { useQuery } from "@tanstack/react-query"
import { History } from "lucide-react"
import { Link } from "react-router-dom"

import { SeverityBadge } from "@/components/severity-badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { SEVERITIES } from "@/lib/severity"

export function HistoryPage() {
  const reviews = useQuery({ queryKey: ["reviews"], queryFn: api.listReviews })

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h1 className="font-serif text-3xl font-semibold">Review history</h1>
        <p className="text-sm text-muted-foreground">
          Kept in server memory for now, so the list resets when the API restarts.
        </p>
      </div>

      {reviews.isPending ? (
        <Skeleton className="h-60" />
      ) : reviews.isError ? (
        <p className="text-sev-high">{reviews.error.message}</p>
      ) : reviews.data.length === 0 ? (
        <Card className="items-center gap-3 p-12 text-center">
          <History className="size-8 text-muted-foreground" aria-hidden />
          <p className="font-semibold">No reviews yet</p>
          <Button asChild size="lg" className="bg-cta text-white hover:bg-cta/85">
            <Link to="/">Review a pull request</Link>
          </Button>
        </Card>
      ) : (
        <Card className="overflow-x-auto p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">Pull request</TableHead>
                <TableHead>Findings</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Time</TableHead>
                <TableHead className="pr-4 text-right">Reviewed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {reviews.data.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="max-w-md pl-4">
                    <Link to={`/reviews/${r.id}`} className="flex flex-col hover:underline">
                      <span className="truncate font-semibold">{r.pull_request.title}</span>
                      <span className="text-xs text-muted-foreground">
                        {r.pull_request.owner}/{r.pull_request.repo} #{r.pull_request.number}
                      </span>
                    </Link>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1.5">
                      {SEVERITIES.filter((s) => r.severity_counts[s]).map((s) => (
                        <span key={s} className="inline-flex items-center gap-1">
                          <SeverityBadge severity={s} />
                          <span className="text-sm">{r.severity_counts[s]}</span>
                        </span>
                      ))}
                      {SEVERITIES.every((s) => !r.severity_counts[s]) && (
                        <span className="text-sm text-success">clean</span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-sm">
                    {r.merged ? (
                      <span className="text-success">merged</span>
                    ) : r.published ? (
                      "posted to PR"
                    ) : r.complete ? (
                      <span className="text-muted-foreground">reviewed</span>
                    ) : (
                      <span className="text-sev-high">partial</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm tabular-nums">
                    {(r.duration_ms / 1000).toFixed(1)}s
                  </TableCell>
                  <TableCell className="pr-4 text-right text-sm text-muted-foreground">
                    {new Date(r.created_at).toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  )
}
