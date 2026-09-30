import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/features/analysis/AnalysisPageRoute', () => ({
  AnalysisPageRoute: () => <div data-testid="analysis-page">Analysis Page</div>,
}))

import App from './App'

describe('App', () => {
  it('ルートパスアクセス時に分析画面（/analysis/new）へ遷移する', async () => {
    window.history.replaceState({}, '', '/')
    render(<App />)

    expect(await screen.findByTestId('analysis-page')).toBeInTheDocument()
  })
})
