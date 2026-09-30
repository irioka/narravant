/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

describe('browser app shell', () => {
  it('NARRAVANTのtitleと台本mark faviconを公開する', () => {
    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')
    const favicon = readFileSync(resolve(process.cwd(), 'public/favicon.svg'), 'utf8')

    expect(html).toContain('<title>NARRAVANT</title>')
    expect(html).toContain('href="/favicon.svg"')
    expect(favicon).toContain('aria-label="NARRAVANT screenplay mark"')
    expect(favicon).toContain('data-part="brad"')
    expect(favicon).toContain('data-part="analysis-lens"')
  })
})