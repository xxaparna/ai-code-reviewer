import type { Finding, Severity } from "@/lib/api"

export const SEVERITIES: Severity[] = ["HIGH", "MEDIUM", "LOW"]

export const SEVERITY_RANK: Record<Severity, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }

export const SEVERITY_META: Record<
  Severity,
  { label: string; hint: string; text: string; bg: string; fill: string }
> = {
  HIGH: {
    label: "High",
    hint: "fix before merge",
    text: "text-sev-high",
    bg: "bg-sev-high/15",
    fill: "bg-sev-high",
  },
  MEDIUM: {
    label: "Medium",
    hint: "should fix",
    text: "text-sev-medium",
    bg: "bg-sev-medium/15",
    fill: "bg-sev-medium",
  },
  LOW: {
    label: "Low",
    hint: "nice to have",
    text: "text-sev-low",
    bg: "bg-sev-low/15",
    fill: "bg-sev-low",
  },
}

export function atOrAbove(finding: Finding, min: Severity): boolean {
  return SEVERITY_RANK[finding.severity] <= SEVERITY_RANK[min]
}

export function countBySeverity(findings: Finding[]): Record<Severity, number> {
  const counts: Record<Severity, number> = { HIGH: 0, MEDIUM: 0, LOW: 0 }
  for (const f of findings) counts[f.severity] += 1
  return counts
}
