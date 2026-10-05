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

const observeIntersectionTarget = vi.fn()

class TestIntersectionObserver {
  disconnect() {}
  observe = observeIntersectionTarget
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
    window.localStorage.clear()
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

  it('幅変更後も見出し・データ行・rename 行が同じ grid を使う', async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [documentItem], limit: 50, offset: 0, total: 1 })

    renderDocumentList()

    await screen.findByText('Original title')
    const row = rowForTitle('Original title')
    const header = screen.getByRole('button', { name: 'Sort by Version' }).parentElement?.parentElement
    const table = header?.parentElement
    const initialTableStyle = table?.getAttribute('style')
    const initialTemplate = header?.getAttribute('style')
    expect(initialTemplate).toContain('grid-template-columns')
    expect(initialTemplate).toContain('minmax(0, 1fr) 88px 144px')
    expect(row.getAttribute('style')).toBe(initialTemplate)

    const savedAtResize = screen.getByRole('separator', { name: 'Resize Saved at column' })
    fireEvent.pointerDown(savedAtResize, { clientX: 300, pointerId: 1 })
    fireEvent.pointerMove(savedAtResize, { clientX: 324, pointerId: 1 })
    fireEvent.pointerUp(savedAtResize, { clientX: 324, pointerId: 1 })

    await waitFor(() => {
      expect(header?.getAttribute('style')).not.toBe(initialTemplate)
      expect(row.getAttribute('style')).toBe(header?.getAttribute('style'))
      expect(table?.getAttribute('style')).toBe(initialTableStyle)
    })

    fireEvent.doubleClick(row)
    const renameRow = screen.getByRole('textbox', { name: 'Document title' }).parentElement
    expect(renameRow?.getAttribute('style')).toBe(header?.getAttribute('style'))
    expect(window.localStorage.getItem('narravant.documentList.columnWidths')).toContain('savedAt')

    const stored = JSON.parse(window.localStorage.getItem('narravant.documentList.columnWidths') ?? '{}') as { version?: number; savedAt?: number }
    expect(stored.version).toBe(88)
    expect(stored.savedAt).toBe(120)
    cleanup()
    renderDocumentList()
    await screen.findByText('Original title')
    expect(screen.getByRole('separator', { name: 'Resize Saved at column' })).toHaveAttribute('aria-valuenow', '120')
  })

  it('不正な保存幅は既定値へ戻し、下限幅より小さくならない', async () => {
    window.localStorage.setItem('narravant.documentList.columnWidths', 'not-json')
    vi.mocked(listDocuments).mockResolvedValue({ items: [documentItem], limit: 50, offset: 0, total: 1 })

    renderDocumentList()
    await screen.findByText('Original title')

    const versionHandle = screen.getByRole('separator', { name: 'Resize Version column' })
    const savedAtHandle = screen.getByRole('separator', { name: 'Resize Saved at column' })
    expect(versionHandle).toHaveAttribute('aria-valuenow', '88')
    expect(savedAtHandle).toHaveAttribute('aria-valuenow', '144')
    fireEvent.keyDown(savedAtHandle, { key: 'ArrowRight' })
    expect(versionHandle).toHaveAttribute('aria-valuenow', '88')
    expect(savedAtHandle).toHaveAttribute('aria-valuenow', '136')

    cleanup()
    window.localStorage.setItem('narravant.documentList.columnWidths', JSON.stringify({ version: 64, savedAt: 96 }))
    renderDocumentList()
    await screen.findByText('Original title')
    const minimumVersionHandle = screen.getByRole('separator', { name: 'Resize Version column' })
    const minimumSavedAtHandle = screen.getByRole('separator', { name: 'Resize Saved at column' })
    expect(minimumVersionHandle).toHaveAttribute('aria-valuenow', '64')
    expect(minimumSavedAtHandle).toHaveAttribute('aria-valuenow', '96')
    fireEvent.keyDown(minimumVersionHandle, { key: 'ArrowRight' })
    fireEvent.keyDown(minimumSavedAtHandle, { key: 'ArrowRight' })
    expect(minimumVersionHandle).toHaveAttribute('aria-valuenow', '64')
    expect(minimumSavedAtHandle).toHaveAttribute('aria-valuenow', '96')
  })

  it('幅変更後も並び替え・検索条件と無限スクロール監視を維持する', async () => {
    window.localStorage.setItem('narravant.documentList.columnWidths', JSON.stringify({ version: 120 }))
    vi.mocked(listDocuments).mockResolvedValue({ items: [documentItem], limit: 50, offset: 0, total: 2 })
    vi.mocked(getDocument).mockResolvedValue({ expected_version: 7 } as never)
    vi.mocked(updateDocument).mockResolvedValue({ ...documentItem, expected_version: 8 } as never)

    renderDocumentList()
    await screen.findByText('Original title')
    const handle = screen.getByRole('separator', { name: 'Resize Version column' })
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    expect(handle).toHaveAttribute('aria-valuenow', '112')
    expect(observeIntersectionTarget).toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Sort by Version' }))
    await waitFor(() => expect(vi.mocked(listDocuments)).toHaveBeenLastCalledWith(expect.objectContaining({
      sorts: [{ field: 'version_id', direction: 'asc' }],
      offset: 0,
    })))
    fireEvent.click(screen.getByRole('button', { name: 'Sort by Version' }))
    await waitFor(() => expect(vi.mocked(listDocuments)).toHaveBeenLastCalledWith(expect.objectContaining({
      sorts: [{ field: 'version_id', direction: 'desc' }],
      offset: 0,
    })))

    const search = screen.getByRole('textbox', { name: 'Search by title' })
    fireEvent.change(search, { target: { value: 'needle' } })
    fireEvent.keyDown(search, { key: 'Enter' })
    await waitFor(() => expect(vi.mocked(listDocuments)).toHaveBeenLastCalledWith(expect.objectContaining({
      query: 'needle',
      sorts: [{ field: 'version_id', direction: 'desc' }],
      offset: 0,
    })))
    expect(screen.getByText('Original title')).toBeInTheDocument()
    expect(screen.getByRole('separator', { name: 'Resize Version column' })).toHaveAttribute('aria-valuenow', '112')
    expect(screen.getByRole('button', { name: 'Sort by Version' })).toBeInTheDocument()

    const row = rowForTitle('Original title')
    fireEvent.doubleClick(row)
    const renameInput = screen.getByRole('textbox', { name: 'Document title' })
    fireEvent.change(renameInput, { target: { value: 'Renamed after resize' } })
    fireEvent.keyDown(renameInput, { key: 'Enter' })
    await waitFor(() => expect(updateDocument).toHaveBeenCalledWith('document-1', {
      expected_version: 7,
      title: 'Renamed after resize',
    }))
  })
})
