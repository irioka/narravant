import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getDocument, listDocuments, updateDocument } from '@/api/documents'
import type { DocumentItem } from '@/api/contracts'
import { DocumentList } from './DocumentList'

vi.mock('@/api/documents', () => ({
  getDocument: vi.fn(),
  listDocuments: vi.fn(),
  updateDocument: vi.fn(),
}))

class TestIntersectionObserver {
  disconnect() {}
  observe() {}
  unobserve() {}
}

vi.stubGlobal('IntersectionObserver', TestIntersectionObserver)

const documentItem: DocumentItem = {
  created_at: '2026-09-29T00:00:00Z',
  current_version_id: 1,
  document_id: 'document-1',
  expected_version: 1,
  is_saved: true,
  owner_email: 'local@narravant.local',
  owner_user_id: 'local-user',
  shared_count: 0,
  title: 'Original title',
  updated_at: '2026-09-29T00:00:00Z',
  version_id: 1,
}

function renderDocumentList() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <DocumentList />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

function rowForTitle(title: string): HTMLButtonElement {
  const row = screen.getByText(title).closest('button')
  if (!row) throw new Error(`Row was not rendered for ${title}`)
  return row
}

describe('DocumentList', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('rename の直前に最新 version を取得して title-only update を送る', async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [documentItem], limit: 50, offset: 0, total: 1 })
    vi.mocked(getDocument).mockResolvedValue({ expected_version: 7 } as never)
    vi.mocked(updateDocument).mockResolvedValue({ ...documentItem, expected_version: 8 } as never)

    renderDocumentList()

    await screen.findByText('Original title')
    const row = rowForTitle('Original title')
    fireEvent.doubleClick(row)
    const input = screen.getByRole('textbox', { name: 'Document title' })
    fireEvent.change(input, { target: { value: 'Renamed title' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(getDocument).toHaveBeenCalledWith('document-1')
      expect(updateDocument).toHaveBeenCalledWith('document-1', {
        expected_version: 7,
        title: 'Renamed title',
      })
    })
  })

  it('Version/Saved at の見出しと行で同じ grid 列を使う', async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [documentItem], limit: 50, offset: 0, total: 1 })

    renderDocumentList()

    await screen.findByText('Original title')
    const row = rowForTitle('Original title')
    const header = screen.getByRole('button', { name: 'Sort by Version' }).parentElement
    expect(header).toHaveClass('grid-cols-[minmax(0,1fr)_5.5rem_9rem]')
    expect(row).toHaveClass('grid-cols-[minmax(0,1fr)_5.5rem_9rem]')
  })
})
