import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { BrowserRouter, Route, Routes } from "react-router-dom"

import { AppShell } from "@/components/app-shell"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { HistoryPage } from "@/pages/history"
import { NewReviewPage } from "@/pages/new-review"
import { ReviewPage } from "@/pages/review"

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 30_000 } },
})

export default function App() {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <BrowserRouter>
            <AppShell>
              <Routes>
                <Route path="/" element={<NewReviewPage />} />
                <Route path="/reviews/:id" element={<ReviewPage />} />
                <Route path="/history" element={<HistoryPage />} />
              </Routes>
            </AppShell>
          </BrowserRouter>
          <Toaster richColors position="bottom-right" />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
