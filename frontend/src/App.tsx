import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'

const AnalysisPageRoute = lazy(() => import('@/features/analysis/AnalysisPageRoute').then((module) => ({ default: module.AnalysisPageRoute })))
const DemoAnalysisPage = lazy(() => import('@/features/analysis/DemoAnalysisPage').then((module) => ({ default: module.DemoAnalysisPage })))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: false },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={350}>
        <BrowserRouter>
          <Suspense fallback={<div className="grid h-svh place-items-center bg-background text-sm text-muted-foreground">Loading…</div>}>
            <Routes>
              <Route path="/" element={<Navigate replace to="/analysis/new" />} />
              <Route path="/analysis/new" element={<AnalysisPageRoute />} />
              <Route path="/analysis/demo" element={<DemoAnalysisPage />} />
              <Route path="/analysis/:documentId" element={<AnalysisPageRoute />} />
              <Route path="*" element={<Navigate replace to="/analysis/new" />} />
            </Routes>
          </Suspense>
        </BrowserRouter>
        <Toaster closeButton position="bottom-right" />
      </TooltipProvider>
    </QueryClientProvider>
  )
}
