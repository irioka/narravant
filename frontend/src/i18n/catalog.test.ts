import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { assertCatalogIntegrity, type CatalogResourceCollection, ERROR_CODES, resources } from './catalog'

describe('i18n catalog', () => {
  it('accepts the shipped English and Japanese catalogs', () => {
    expect(() => assertCatalogIntegrity(resources)).not.toThrow()
  })

  it('uses a Japanese label for voice creation', () => {
    expect(resources.ja.analysis.voices.created).toBe('声を作成しました。')
  })

  it('uses output-format wording for export descriptions in both locales', () => {
    expect(resources.ja.importExport.export.description).toBe('出力フォーマットを選択してください。')
    expect(resources.en.importExport.export.description).toBe('Choose an output format.')
  })

  it('rejects a missing Japanese plural category', () => {
    const broken = structuredClone(resources) as CatalogResourceCollection
    delete (broken.ja.documents as Record<string, unknown>).resultCount_other

    expect(() => assertCatalogIntegrity(broken)).toThrow('Missing plural category')
  })

  it('rejects an interpolation-parameter mismatch', () => {
    const broken = structuredClone(resources) as CatalogResourceCollection
    const unknown = (broken.ja.errors as Record<string, unknown>).unknown as Record<string, string>
    unknown.message = '予期しないエラーが発生しました。（コード: {{unexpected}}）'

    expect(() => assertCatalogIntegrity(broken)).toThrow('Interpolation parameters differ')
  })

  it('maps every OpenAPI API error code to the catalog', () => {
    const openapi = JSON.parse(readFileSync(resolve(process.cwd(), '../backend/openapi.json'), 'utf8')) as {
      components: { schemas: { ApiErrorCode: { enum: string[] } } }
    }

    expect(openapi.components.schemas.ApiErrorCode.enum).toEqual(expect.arrayContaining([...ERROR_CODES.api]))
    expect(ERROR_CODES.api).toEqual(expect.arrayContaining(openapi.components.schemas.ApiErrorCode.enum))
  })
})
