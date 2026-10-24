import { GitPullRequestArrow, Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import type { ReactNode } from "react"
import { Link, NavLink } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme !== "light"
  return (
    <Button
      variant="outline"
      size="icon-lg"
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      onClick={() => setTheme(dark ? "light" : "dark")}
    >
      {dark ? <Sun /> : <Moon />}
    </Button>
  )
}

const navClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-md px-3 py-2 text-sm transition-colors",
    isActive ? "bg-card font-semibold text-foreground" : "text-muted-foreground hover:text-foreground",
  )

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-svh">
      <header className="border-b">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-7 gap-y-3 px-4 py-3 sm:px-6">
          <Link to="/" className="flex items-center gap-2.5 font-bold tracking-tight">
            <span className="flex size-8 items-center justify-center rounded-lg bg-cta text-white">
              <GitPullRequestArrow className="size-4.5" aria-hidden />
            </span>
            reviewer<span className="text-chart-1">/</span>ai
          </Link>
          <nav aria-label="Main" className="flex gap-1">
            <NavLink to="/" end className={navClass}>
              Review
            </NavLink>
            <NavLink to="/history" className={navClass}>
              History
            </NavLink>
          </nav>
          <div className="ml-auto">
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 pt-7 pb-16 sm:px-6">{children}</main>
    </div>
  )
}
