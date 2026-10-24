import { CircleAlert, CircleDot, TriangleAlert } from "lucide-react"

import type { Severity } from "@/lib/api"
import { SEVERITY_META } from "@/lib/severity"
import { cn } from "@/lib/utils"

const ICONS = { HIGH: TriangleAlert, MEDIUM: CircleAlert, LOW: CircleDot }

export function SeverityBadge({ severity, className }: { severity: Severity; className?: string }) {
  const meta = SEVERITY_META[severity]
  const Icon = ICONS[severity]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-bold",
        meta.bg,
        meta.text,
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden />
      {severity}
    </span>
  )
}

/** Proportional bar of findings by severity. Hidden from AT; counts are shown as text. */
export function SeverityBar({
  counts,
  className,
}: {
  counts: Record<Severity, number>
  className?: string
}) {
  const total = counts.HIGH + counts.MEDIUM + counts.LOW
  if (total === 0) return <div className={cn("h-3.5 rounded-full bg-success/30", className)} aria-hidden />
  return (
    <div className={cn("flex h-3.5 gap-0.5 overflow-hidden rounded-full", className)} aria-hidden>
      {(["HIGH", "MEDIUM", "LOW"] as const).map((s) =>
        counts[s] ? (
          <div key={s} className={SEVERITY_META[s].fill} style={{ flex: `${counts[s]} 1 0` }} />
        ) : null,
      )}
    </div>
  )
}
