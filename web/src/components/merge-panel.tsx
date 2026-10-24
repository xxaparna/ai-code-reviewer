import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, GitMerge, RefreshCw, TriangleAlert, X } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, api, type GateCheck, type MergeReadiness, type ReviewDetail } from "@/lib/api"
import { cn } from "@/lib/utils"

const METHOD_LABEL: Record<string, string> = {
  squash: "Squash and merge",
  merge: "Create a merge commit",
  rebase: "Rebase and merge",
}

function CheckList({ checks }: { checks: GateCheck[] }) {
  return (
    <ul className="flex flex-col gap-2.5">
      {checks.map((c) => (
        <li key={c.key} className="flex items-start gap-2.5">
          <span
            className={cn(
              "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full",
              c.passed ? "bg-success/20 text-success" : "bg-sev-high/20 text-sev-high",
            )}
          >
            {c.passed ? <Check className="size-3" strokeWidth={3} aria-label="passed" /> : <X className="size-3" strokeWidth={3} aria-label="failed" />}
          </span>
          <span className="flex min-w-0 flex-col">
            <span>{c.label}</span>
            <span className="text-xs break-words text-muted-foreground">{c.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

export function MergePanel({ review }: { review: ReviewDetail }) {
  const queryClient = useQueryClient()
  const pr = review.pull_request
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(`${pr.title} (#${pr.number})`)

  const readiness = useQuery({
    queryKey: ["readiness", review.id],
    queryFn: () => api.mergeReadiness(review.id),
    enabled: !review.merge,
    retry: false,
  })

  const merge = useMutation({
    mutationFn: () => api.merge(review.id, title),
    onSuccess: (updated) => {
      queryClient.setQueryData(["review", review.id], updated)
      queryClient.invalidateQueries({ queryKey: ["reviews"] })
      setOpen(false)
      toast.success(`Merged PR #${pr.number}`)
    },
    onError: (e) => {
      // The server re-ran the gate and refused: show its fresh verdict.
      if (e instanceof ApiError && e.checks) {
        queryClient.setQueryData<MergeReadiness>(["readiness", review.id], (old) =>
          old ? { ...old, ready: false, checks: e.checks ?? old.checks } : old,
        )
      }
      toast.error("Merge refused", { description: e.message })
    },
  })

  if (review.merge) {
    return (
      <Card className="gap-2 p-5">
        <div className="flex items-center gap-2.5 text-success">
          <GitMerge className="size-4.5" aria-hidden />
          <h2 className="text-base font-semibold">Merged</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          {METHOD_LABEL[review.merge.merge_method] ?? review.merge.merge_method} at{" "}
          {new Date(review.merge.merged_at).toLocaleString()} ·{" "}
          <span className="font-mono">{review.merge.sha.slice(0, 7)}</span>
        </p>
      </Card>
    )
  }

  const data = readiness.data
  const failing = data?.checks.filter((c) => !c.passed).length ?? 0

  return (
    <Card className="gap-3.5 p-5">
      <div className="flex items-center gap-2.5">
        <GitMerge className="size-4.5" aria-hidden />
        <h2 className="text-base font-semibold">Safe merge</h2>
        {data && (
          <span className={cn("ml-auto text-xs font-semibold", data.ready ? "text-success" : "text-sev-high")}>
            {data.ready ? "All checks pass" : `${failing} check${failing === 1 ? "" : "s"} failing`}
          </span>
        )}
        <Button
          variant="ghost"
          size="icon"
          className={cn(!data && "ml-auto")}
          aria-label="Re-check merge readiness"
          disabled={readiness.isFetching}
          onClick={() => readiness.refetch()}
        >
          <RefreshCw className={cn(readiness.isFetching && "animate-spin")} />
        </Button>
      </div>

      {readiness.isPending ? (
        <div className="flex flex-col gap-3">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-9" />
          ))}
        </div>
      ) : readiness.isError ? (
        <p className="text-sm text-sev-high">Couldn't check readiness: {readiness.error.message}</p>
      ) : (
        data && <CheckList checks={data.checks} />
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>
          <Button
            size="lg"
            variant={data?.ready ? "default" : "outline"}
            className={cn("h-11", data?.ready && "bg-success text-white hover:bg-success/85 dark:text-accent-foreground")}
            disabled={!data?.ready}
          >
            <GitMerge />
            {data?.ready ? "Merge…" : "Merge blocked"}
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle className="font-serif text-2xl">Merge PR #{pr.number}</DialogTitle>
            <DialogDescription>
              {pr.head_ref} → {pr.base_ref}. The server re-checks everything below the moment you confirm.
            </DialogDescription>
          </DialogHeader>
          {data && <CheckList checks={data.checks} />}
          <div className="grid grid-cols-2 gap-2.5">
            <div className="rounded-lg bg-muted p-3">
              <div className="text-xs text-muted-foreground">Method</div>
              <div className="font-semibold">{METHOD_LABEL[data?.merge_method ?? "squash"]}</div>
            </div>
            <div className="rounded-lg bg-muted p-3">
              <div className="text-xs text-muted-foreground">Pinned to commit</div>
              <div className="font-mono font-semibold">{pr.head_sha.slice(0, 7)}</div>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="commit-title">Commit title</Label>
            <Input id="commit-title" value={title} maxLength={256} onChange={(e) => setTitle(e.target.value)} className="h-11" />
          </div>
          {merge.isError && (
            <p className="flex gap-2 text-sm text-sev-high">
              <TriangleAlert className="size-4 shrink-0" aria-hidden />
              {merge.error.message}
            </p>
          )}
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" size="lg" className="h-11">
                Cancel
              </Button>
            </DialogClose>
            <Button
              size="lg"
              className="h-11 bg-success text-white hover:bg-success/85 dark:text-accent-foreground"
              disabled={merge.isPending}
              onClick={() => merge.mutate()}
            >
              <GitMerge />
              {merge.isPending ? "Merging…" : METHOD_LABEL[data?.merge_method ?? "squash"]}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <p className="text-xs text-muted-foreground">
        Pinned to the reviewed commit: if anyone pushes in between, GitHub refuses the merge.
      </p>
    </Card>
  )
}
