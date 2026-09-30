import { en } from './resources/en'
import { ja } from './resources/ja'

export const NAMESPACES = ['common', 'documents', 'analysis', 'importExport', 'errors'] as const
export type AppNamespace = (typeof NAMESPACES)[number]

export const API_ERROR_CODES = [
  'VALIDATION_ERROR', 'EMPTY_FILE', 'FILE_TOO_LARGE', 'UNSUPPORTED_MEDIA_TYPE', 'INVALID_ENCODING', 'MALFORMED_FDX', 'MALFORMED_PDF', 'INVALID_NATIVE_DOCUMENT', 'PROCESS_RESTARTED', 'UNAUTHENTICATED', 'AUTHENTICATION_UNAVAILABLE', 'ACCOUNT_BINDING_CONFLICT', 'DOCUMENT_NOT_FOUND', 'TASK_NOT_FOUND', 'NOT_FOUND', 'FORBIDDEN', 'CONFLICT', 'DOCUMENT_CONTENT_MISSING', 'CONTRACT_MISMATCH', 'INTERNAL_ERROR',
] as const

export const SSE_ERROR_CODES = ['IMPORT_FAILED', 'REANALYSIS_FAILED', 'SOURCE_CLASSIFICATION_INCONCLUSIVE', 'ADAPTATION_VERIFICATION_FAILED', 'SSE_CONNECTION_FAILED', 'SSE_DISCONNECTED'] as const
export const FRONTEND_ERROR_CODES = ['HTTP_ERROR', 'DOCUMENTS_LOAD_FAILED', 'DOCUMENT_OVERVIEW_LOAD_FAILED', 'SAVE_FAILED', 'DELETE_FAILED', 'DISCARD_FAILED', 'IMPORT_BLOCKED', 'REANALYSIS_RESULT_INVALID', 'SHARING_SAVE_FAILED', 'STORAGE_READ_FAILED', 'STORAGE_WRITE_FAILED'] as const
export const ERROR_CODES = { api: API_ERROR_CODES, sse: SSE_ERROR_CODES, frontend: FRONTEND_ERROR_CODES } as const

export const resources = { en, ja } as const

export type TranslationTree = { [key: string]: string | TranslationTree }
export type CatalogResourceCollection = Record<'en' | 'ja', TranslationTree>

type Leaf = { path: string; value: string }
const pluralSuffix = /_(zero|one|two|few|many|other)$/
const interpolation = /{{\s*([A-Za-z0-9_]+)(?:\s*,[^}]*)?\s*}}/g

function flatten(value: TranslationTree, prefix = ''): Leaf[] {
  return Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return typeof child === 'string' ? [{ path, value: child }] : flatten(child, path)
  })
}

function semanticId(path: string): string {
  return path.replace(pluralSuffix, '')
}

function parameters(entries: Leaf[]): string[] {
  const names = new Set<string>()
  for (const entry of entries) {
    for (const match of entry.value.matchAll(interpolation)) names.add(match[1])
  }
  return [...names].sort()
}

function groups(leaves: Leaf[]): Map<string, Leaf[]> {
  const result = new Map<string, Leaf[]>()
  for (const leaf of leaves) {
    const group = result.get(semanticId(leaf.path)) ?? []
    group.push(leaf)
    result.set(semanticId(leaf.path), group)
  }
  return result
}

export function assertCatalogIntegrity(catalogs: CatalogResourceCollection): void {
  const byLocale = new Map(Object.entries(catalogs).map(([locale, resource]) => [locale, groups(flatten(resource))]))
  const english = byLocale.get('en')
  const japanese = byLocale.get('ja')
  if (!english || !japanese) throw new Error('English and Japanese catalogs are required')

  const englishIds = [...english.keys()].sort()
  const japaneseIds = [...japanese.keys()].sort()
  const allIds = [...new Set([...englishIds, ...japaneseIds])].sort()
  for (const id of allIds) {
    const present = english.get(id) ?? japanese.get(id)
    if (!english.has(id) || !japanese.has(id)) {
      if (present?.some((entry) => pluralSuffix.test(entry.path))) {
        const missingLocale = english.has(id) ? 'ja' : 'en'
        const required = new Intl.PluralRules(missingLocale).resolvedOptions().pluralCategories
        throw new Error(`Missing plural category ${missingLocale}:${id}_${required[0]}`)
      }
      throw new Error('Catalog semantic IDs differ')
    }
  }

  for (const id of allIds) {
    const enEntries = english.get(id)!
    const jaEntries = japanese.get(id)!
    if (parameters(enEntries).join(',') !== parameters(jaEntries).join(',')) {
      throw new Error(`Interpolation parameters differ for ${id}`)
    }
    if (!enEntries.some((entry) => pluralSuffix.test(entry.path)) && !jaEntries.some((entry) => pluralSuffix.test(entry.path))) continue
    for (const [locale, entries] of [['en', enEntries], ['ja', jaEntries]] as const) {
      const required = new Intl.PluralRules(locale).resolvedOptions().pluralCategories
      const paths = new Set(entries.map((entry) => entry.path))
      for (const category of required) {
        if (!paths.has(`${id}_${category}`)) throw new Error(`Missing plural category ${locale}:${id}_${category}`)
      }
    }
  }
}

assertCatalogIntegrity(resources)
