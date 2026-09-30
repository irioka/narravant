export const DOCUMENT_PAGE_SIZE = 50
export const TASK_SSE_INITIAL_RETRY_DELAY_MS = 250
export const TASK_SSE_MAX_RETRY_DELAY_MS = 4000
export const TASK_SSE_MAX_RETRIES = 4

export const DOCUMENT_SORT_FIELDS = ['title', 'owner_email', 'shared_count', 'updated_at', 'version_id'] as const
export type DocumentSortField = (typeof DOCUMENT_SORT_FIELDS)[number]
export type SortDirection = 'asc' | 'desc'

export interface DocumentSortTerm {
  field: DocumentSortField
  direction: SortDirection
}
