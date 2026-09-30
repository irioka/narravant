import type { QueryClient } from '@tanstack/react-query'
import { ApiClientError } from '@/api/client'

export function isDocumentAccessRevoked(error: unknown): error is ApiClientError {
  return error instanceof ApiClientError && error.status === 404 && error.code === 'DOCUMENT_NOT_FOUND'
}

export async function removeDocumentAccessCache(
  queryClient: QueryClient,
  documentId: string,
): Promise<void> {
  await queryClient.cancelQueries({ queryKey: ['document', documentId] })
  queryClient.removeQueries({ queryKey: ['document', documentId] })
}
