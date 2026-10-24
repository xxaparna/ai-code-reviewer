import { Layers, PencilLine } from "lucide-react"
import { useMemo } from "react"

import { SeverityBadge } from "@/components/severity-badge"
import { Card } from "@/components/ui/card"
import type { ChangedFile, Finding } from "@/lib/api"
import { contextWindow, newSideText, parsePatch } from "@/lib/diff"
import { cn } from "@/lib/utils"

interface Props {
  finding: Finding
  file: ChangedFile | undefined
  /** Whether this finding falls inside the current "post to PR" threshold. */
  willPost: boolean
}

export function FindingCard({ finding, file, willPost }: Props) {
  const hunks = useMemo(() => parsePatch(file?.patch ?? ""), [file?.patch])
  const end = finding.line
  const start = finding.start_line ?? finding.line
  const window = end !== null && start !== null ? contextWindow(hunks, start, end) : null
  const before = end !== null && start !== null ? newSideText(hunks, start, end) : []
  const after = finding.suggested_code?.split("\n") ?? []
  const location =
    end === null ? finding.file : start !== null && start !== end ? `${finding.file}:${start}–${end}` : `${finding.file}:${end}`

  return (
    <Card className="gap-0 overflow-hidden p-0">
      <div className="flex flex-col gap-2.5 px-4.5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={finding.severity} />
          <span className="rounded-md border px-2 py-0.5 text-xs text-muted-foreground">{finding.category}</span>
          <span className="min-w-0 font-mono text-sm break-all">{location}</span>
          {finding.dedup_collapsed_count > 1 && (
            <span className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground" title="Reported by several agents and merged into one finding">
              <Layers className="size-3.5" aria-hidden />×{finding.dedup_collapsed_count} merged
            </span>
          )}
        </div>
        <p className="leading-relaxed">{finding.message}</p>
        {finding.suggestion && <p className="text-sm text-muted-foreground">{finding.suggestion}</p>}
      </div>

      {window && (
        <div className="overflow-x-auto border-t bg-background font-mono text-[13px] leading-6">
          {window.map((l, i) => {
            const flagged = l.newNumber !== null && start !== null && end !== null && l.newNumber >= start && l.newNumber <= end
            return (
              <div
                key={i}
                className={cn(
                  "flex",
                  flagged && "bg-sev-high/10 shadow-[inset_3px_0_0_var(--sev-high)]",
                  l.kind === "del" && "text-muted-foreground line-through decoration-sev-high/60",
                )}
              >
                <span className={cn("w-12 shrink-0 pr-3 text-right select-none", flagged ? "text-foreground" : "text-muted-foreground")}>
                  {l.newNumber ?? ""}
                </span>
                <span className="w-4 shrink-0 text-muted-foreground select-none">
                  {l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}
                </span>
                <span className={cn("pr-4 whitespace-pre", !flagged && "text-muted-foreground")}>{l.text}</span>
              </div>
            )
          })}
        </div>
      )}

      {finding.suggested_code !== null ? (
        <div className="border-t">
          <div className="flex flex-wrap items-center gap-2 px-4.5 py-2.5 text-xs text-muted-foreground">
            <PencilLine className="size-3.5" aria-hidden />
            <span className="font-semibold text-foreground">Suggested change</span>
            <span className={cn("ml-auto", willPost && "text-success")}>
              {willPost ? "Will be posted to PR" : "Below posting threshold"}
            </span>
          </div>
          <div className="overflow-x-auto pb-2 font-mono text-[13px] leading-6">
            {before.map((line, i) => (
              <div key={`b${i}`} className="flex bg-sev-high/10">
                <span className="w-12 shrink-0 pr-3 text-right text-sev-high select-none">−</span>
                <span className="pr-4 whitespace-pre text-muted-foreground">{line}</span>
              </div>
            ))}
            {after.map((line, i) => (
              <div key={`a${i}`} className="flex bg-success/10">
                <span className="w-12 shrink-0 pr-3 text-right text-success select-none">+</span>
                <span className="pr-4 whitespace-pre">{line}</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="border-t px-4.5 py-2.5 text-xs text-muted-foreground">
          {end === null
            ? "File-level finding: posted in the review summary, not inline."
            : "No drop-in fix proposed. Posted as a comment only."}
        </div>
      )}
    </Card>
  )
}
