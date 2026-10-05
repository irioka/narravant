import { z } from 'zod'

import type { DocumentSortTerm } from '@/config/narravant'
import type { Analysis, DocumentDetail } from './contracts'

import { apiRequest } from './client'
import {
  documentVersionSchema,
} from './contracts'
import {
  DiscardImportDraftApiV1DocumentsImportTaskIdDeleteResponse,
  GetDocumentDetailApiV1DocumentsDocIdGetResponse,
  GetDocumentVersionApiV1DocumentsDocIdVersionsVersionIdGetResponse,
  GetEmotionArcSimilaritiesApiV1DocumentsDocIdEmotionArcSimilaritiesGetResponse,
  GetValencePatternsApiV1EmotionArcPatternsGetResponse,
  ImportDocumentApiV1DocumentsImportPostResponse,
  ListDocumentsApiV1DocumentsGetResponse,
  ReanalyzeEmotionArcApiV1DocumentsDocIdEmotionArcReanalyzePostResponse,
  SaveImportDraftApiV1DocumentsImportTaskIdSavePostResponse,
  UpdateDocumentApiV1DocumentsDocIdPutResponse,
} from './generated/contracts'

export interface DocumentListParams {
  query?: string
  similarToArcId?: string
  arcPatternId?: string
  sorts?: DocumentSortTerm[]
  limit?: number
  offset?: number
}

function queryString(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '') query.set(key, String(value))
  const serialized = query.toString()
  return serialized ? `?${serialized}` : ''
}

export function listDocuments(params: DocumentListParams = {}) {
  return apiRequest(
    `/api/v1/documents${queryString({
      query: params.query,
      similar_to_arc_id: params.similarToArcId,
      arc_pattern_id: params.arcPatternId,
      sort: params.sorts?.map((term) => `${term.field}:${term.direction}`).join(','),
      limit: params.limit ?? 16,
      offset: params.offset ?? 0,
    })}`,
    ListDocumentsApiV1DocumentsGetResponse,
  )
}

export function getDocument(documentId: string) {
  return apiRequest(`/api/v1/documents/${documentId}`, GetDocumentDetailApiV1DocumentsDocIdGetResponse)
}

export function importDocument(file: File, fetcher?: typeof fetch) {
  const form = new FormData()
  form.append('file', file, file.name)
  return apiRequest('/api/v1/documents/import', ImportDocumentApiV1DocumentsImportPostResponse, {
    method: 'POST',
    body: form,
    fetcher,
  })
}

export function saveImportDraft(
  taskId: string,
  payload: {
    title: string
    fountain_text: string
    metadata: DocumentDetail['metadata']
    analysis: Analysis
    emotion_arc: DocumentDetail['emotion_arc']
    narrator?: DocumentDetail['narrator']
    voice_assignments?: DocumentDetail['voice_assignments']
  },
  fetcher?: typeof fetch,
) {
  return apiRequest(
    `/api/v1/documents/import/${taskId}/save`,
    SaveImportDraftApiV1DocumentsImportTaskIdSavePostResponse,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      fetcher,
    },
  )
}

export function discardImportDraft(taskId: string, fetcher?: typeof fetch) {
  return apiRequest(`/api/v1/documents/import/${taskId}`, DiscardImportDraftApiV1DocumentsImportTaskIdDeleteResponse, {
    method: 'DELETE',
    fetcher,
  })
}

export function updateDocument(
  documentId: string,
  payload: {
    expected_version: number
    base_version_id?: number
    title?: string
    fountain_text?: string
    metadata?: DocumentDetail['metadata']
    analysis?: Analysis
    emotion_arc?: DocumentDetail['emotion_arc']
    narrator?: DocumentDetail['narrator']
    voice_assignments?: DocumentDetail['voice_assignments']
  },
) {
  return apiRequest(`/api/v1/documents/${documentId}`, UpdateDocumentApiV1DocumentsDocIdPutResponse, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteDocument(documentId: string) {
  return apiRequest(`/api/v1/documents/${documentId}`, z.void(), { method: 'DELETE' })
}

export function getVersions(documentId: string) {
  return apiRequest(`/api/v1/documents/${documentId}/versions`, z.object({ items: z.array(documentVersionSchema) }))
}

export function getVersion(documentId: string, versionId: number) {
  return apiRequest(`/api/v1/documents/${documentId}/versions/${versionId}`, GetDocumentVersionApiV1DocumentsDocIdVersionsVersionIdGetResponse)
}

export function getValenceSimilarities(documentId: string, fetcher?: typeof fetch) {
  return apiRequest(
    `/api/v1/documents/${documentId}/emotion-arc/similarities`,
    GetEmotionArcSimilaritiesApiV1DocumentsDocIdEmotionArcSimilaritiesGetResponse,
    { fetcher },
  )
}

export function getValencePatterns(fetcher?: typeof fetch) {
  return apiRequest('/api/v1/emotion-arc/patterns', GetValencePatternsApiV1EmotionArcPatternsGetResponse, { fetcher })
}

export function reanalyzeEmotionArc(documentId: string, sourceFountain?: string, fetcher?: typeof fetch) {
  return apiRequest(
    `/api/v1/documents/${documentId}/emotion-arc/reanalyze`,
    ReanalyzeEmotionArcApiV1DocumentsDocIdEmotionArcReanalyzePostResponse,
    {
      method: 'POST',
      ...(sourceFountain !== undefined ? {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_fountain: sourceFountain }),
      } : {}),
      fetcher,
    },
  )
}

export function reanalyzeImportDraftEmotionArc(importTaskId: string, sourceFountain: string, fetcher?: typeof fetch) {
  return apiRequest(
    `/api/v1/documents/import/${importTaskId}/emotion-arc/reanalyze`,
    ReanalyzeEmotionArcApiV1DocumentsDocIdEmotionArcReanalyzePostResponse,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_fountain: sourceFountain }),
      fetcher,
    },
  )
}
