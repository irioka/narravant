import { describe, expect, it, vi } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import { ApiClientError } from '@/api/client'
import { isDocumentAccessRevoked, removeDocumentAccessCache } from './document-access'

describe('isDocumentAccessRevoked', () => {
  it('returns true only for ApiClientError with status 404 and code DOCUMENT_NOT_FOUND', () => {
    expect(isDocumentAccessRevoked(new ApiClientError(404, 'DOCUMENT_NOT_FOUND', 'Document not found.'))).toBe(true)

    expect(isDocumentAccessRevoked(new ApiClientError(404, 'NOT_FOUND', 'Not found.'))).toBe(false)
    expect(isDocumentAccessRevoked(new ApiClientError(403, 'FORBIDDEN', 'Forbidden.'))).toBe(false)
    expect(isDocumentAccessRevoked(new ApiClientError(401, 'UNAUTHORIZED', 'Unauthorized.'))).toBe(false)
    expect(isDocumentAccessRevoked(new ApiClientError(500, 'INTERNAL_SERVER_ERROR', 'Error.'))).toBe(false)
    expect(isDocumentAccessRevoked(new Error('Document not found.'))).toBe(false)
    expect(isDocumentAccessRevoked(null)).toBe(false)
    expect(isDocumentAccessRevoked(undefined)).toBe(false)
    expect(isDocumentAccessRevoked({ status: 404, code: 'DOCUMENT_NOT_FOUND' })).toBe(false)
  })
})

describe('removeDocumentAccessCache', () => {
  it('cancels active queries then removes all queries matching the document prefix without touching other cache keys', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    queryClient.setQueryData(['document', 'doc-1'], { id: 'doc-1', title: 'Target' })
    queryClient.setQueryData(['document', 'doc-1', 'versions'], [{ version_id: 1 }])
    queryClient.setQueryData(['document', 'doc-1', 'version', 1], { version_id: 1, text: 'Scene 1' })
    queryClient.setQueryData(['document', 'doc-1', 'sharing'], { is_public: false, shared_users: [] })
    queryClient.setQueryData(['document', 'doc-1', 'valence-similarities'], { items: [] })

    queryClient.setQueryData(['document', 'doc-2'], { id: 'doc-2', title: 'Other' })
    queryClient.setQueryData(['documents'], [{ id: 'doc-1' }, { id: 'doc-2' }])

    const cancelSpy = vi.spyOn(queryClient, 'cancelQueries')
    const removeSpy = vi.spyOn(queryClient, 'removeQueries')

    await removeDocumentAccessCache(queryClient, 'doc-1')

    expect(cancelSpy).toHaveBeenCalledWith({ queryKey: ['document', 'doc-1'] })
    expect(removeSpy).toHaveBeenCalledWith({ queryKey: ['document', 'doc-1'] })
    expect(cancelSpy.mock.invocationCallOrder[0]).toBeLessThan(removeSpy.mock.invocationCallOrder[0])

    expect(queryClient.getQueryData(['document', 'doc-1'])).toBeUndefined()
    expect(queryClient.getQueryData(['document', 'doc-1', 'versions'])).toBeUndefined()
    expect(queryClient.getQueryData(['document', 'doc-1', 'version', 1])).toBeUndefined()
    expect(queryClient.getQueryData(['document', 'doc-1', 'sharing'])).toBeUndefined()
    expect(queryClient.getQueryData(['document', 'doc-1', 'valence-similarities'])).toBeUndefined()

    expect(queryClient.getQueryData(['document', 'doc-2'])).toEqual({ id: 'doc-2', title: 'Other' })
    expect(queryClient.getQueryData(['documents'])).toEqual([{ id: 'doc-1' }, { id: 'doc-2' }])
  })
})
