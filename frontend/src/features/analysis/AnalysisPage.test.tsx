import { afterEach, describe, expect, it, vi } from 'vitest'

import { scrollTextRangeIntoView } from './script-scroll'

describe('script selection scrolling', () => {
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
