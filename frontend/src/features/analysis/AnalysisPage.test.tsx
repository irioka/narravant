import { afterEach, describe, expect, it, vi } from 'vitest'

import { scrollTextRangeIntoView } from './script-scroll'

describe('script selection scrolling', () => {
  it('折り返し後の表示座標とスクロールバーを除いた幅で対象を表示する', () => {
    const textarea = document.createElement('textarea')
    textarea.style.cssText = 'padding:28px; font:14px monospace; line-height:28px'
    Object.defineProperty(textarea, 'clientHeight', { configurable: true, value: 100 })
    Object.defineProperty(textarea, 'clientWidth', { configurable: true, value: 180 })
    const source = '長い合成台詞です。'.repeat(100) + '選択対象'
    const start = source.indexOf('選択対象')
    const mirrorStyles: string[] = []
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.tagName === 'DIV') mirrorStyles.push(this.style.cssText)
      return { top: this.tagName === 'SPAN' ? 400 : 100 } as DOMRect
    })
    const childrenBefore = document.body.childElementCount
    scrollTextRangeIntoView(textarea, source, { start, end: source.length })
    expect(textarea.scrollTop).toBe(265)
    expect(mirrorStyles.some((style) => style.includes('width: 180px') && style.includes('pre-wrap'))).toBe(true)
    expect(document.body.childElementCount).toBe(childrenBefore)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('scrolls the selected scene line into the editor viewport', () => {
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      lineHeight: '28px',
      paddingTop: '28px',
    } as CSSStyleDeclaration)
    const textarea = document.createElement('textarea')
    Object.defineProperty(textarea, 'clientHeight', { configurable: true, value: 100 })
    const source = 'Title: Sample\n\nINT. ROOM - DAY #1#\n\nNarration.'
    const start = source.indexOf('INT.')

    scrollTextRangeIntoView(textarea, source, { start, end: start + 20 })

    // 2nd source line: padding (28) + 2 * line-height (56) - 35% viewport (35).
    expect(textarea.scrollTop).toBe(49)
  })
})
