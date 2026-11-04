import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearTextRangeMeasurement, scrollTextRangeIntoView } from './script-scroll'

const editors: HTMLTextAreaElement[] = []
const makeEditor = () => {
  const editor = document.createElement('textarea')
  editors.push(editor)
  return editor
}

describe('script selection scrolling', () => {
  beforeEach(() => {
    vi.spyOn(document, 'createRange').mockImplementation(() => ({
      setStart: vi.fn(), setEnd: vi.fn(), getBoundingClientRect: () => ({ top: 400 }),
    }) as unknown as Range)
  })
  it('長時間の本文追従で全文の測定DOMを繰り返し生成しない', () => {
    const textarea = makeEditor()
    Object.defineProperty(textarea, 'clientWidth', { value: 500 })
    const append = vi.spyOn(document.body, 'append')
    const source = '合成の長い本文です。\n'.repeat(1000)
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 0 } as DOMRect)
    for (let index = 0; index < 100; index++) {
      scrollTextRangeIntoView(textarea, source, { start: index * 10, end: index * 10 + 1 })
    }
    expect(append).toHaveBeenCalledTimes(1)
    const mirror = append.mock.calls[0][0] as HTMLElement
    expect(mirror.textContent!.length).toBeLessThan(source.length)
    expect(source.startsWith(mirror.textContent!)).toBe(true)
    clearTextRangeMeasurement(textarea)
    expect(mirror.isConnected).toBe(false)
    scrollTextRangeIntoView(textarea, source, { start: 10, end: 11 })
    expect(append).toHaveBeenCalledTimes(2)
  })

  it('後半への初回移動でも全文の測定を一度に行わず、破棄後に処理を再開しない', () => {
    vi.useFakeTimers()
    const textarea = makeEditor()
    Object.defineProperty(textarea, 'clientWidth', { value: 500 })
    const append = vi.spyOn(document.body, 'append')
    const measure = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 100 } as DOMRect)
    const source = `${'長い合成段落です。'.repeat(10)}\n`.repeat(1000)
    textarea.scrollTop = 17
    scrollTextRangeIntoView(textarea, source, { start: source.length - 10, end: source.length })
    const mirror = append.mock.calls[0][0] as HTMLElement
    expect(mirror.textContent!.length).toBeLessThan(source.length / 2)
    expect(textarea.scrollTop).toBe(17)
    expect(vi.getTimerCount()).toBe(1)
    clearTextRangeMeasurement(textarea)
    const measured = measure.mock.calls.length
    vi.runAllTimers()
    expect(measure).toHaveBeenCalledTimes(measured)
    expect(mirror.isConnected).toBe(false)
  })

  it('分割測定は最新の位置にまとめ、途中で本文が変わった場合は旧位置を適用しない', () => {
    vi.useFakeTimers()
    const textarea = makeEditor()
    Object.defineProperty(textarea, 'clientWidth', { value: 500 })
    const append = vi.spyOn(document.body, 'append')
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 100 } as DOMRect)
    const longSource = `${'合成の段落です。'.repeat(10)}\n`.repeat(1000)
    scrollTextRangeIntoView(textarea, longSource, { start: longSource.length - 1, end: longSource.length })
    scrollTextRangeIntoView(textarea, longSource, { start: 3, end: 4 })
    expect(vi.getTimerCount()).toBe(1)
    vi.runAllTimers()
    expect(textarea.scrollTop).toBe(300)
    const caret = vi.mocked(document.createRange).mock.results[0].value
    expect(caret.setStart).toHaveBeenLastCalledWith(expect.any(Text), 3)
    const mirror = append.mock.calls[0][0] as HTMLElement
    expect(mirror.textContent!.length).toBeLessThan(longSource.length / 2)
    scrollTextRangeIntoView(textarea, longSource, { start: longSource.length - 1, end: longSource.length })
    scrollTextRangeIntoView(textarea, 'new synthetic source', { start: 2, end: 3 })
    vi.runAllTimers()
    expect(mirror.textContent).toBe('new synthetic source\u200b')
    expect(caret.setStart).toHaveBeenLastCalledWith(expect.any(Text), 2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('幅が0になった後に未完了の旧測定がスクロールを上書きしない', () => {
    vi.useFakeTimers()
    const textarea = makeEditor()
    let width = 500
    Object.defineProperty(textarea, 'clientWidth', { get: () => width })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 100 } as DOMRect)
    const source = `${'合成の段落です。'.repeat(10)}\n`.repeat(1000)
    scrollTextRangeIntoView(textarea, source, { start: source.length - 1, end: source.length })
    width = 0
    scrollTextRangeIntoView(textarea, 'new\nsource', { start: 4, end: 5 })
    const fallbackTop = textarea.scrollTop
    vi.runAllTimers()
    expect(textarea.scrollTop).toBe(fallbackTop)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('途中blockの行末空白・tabの後へ余分な文字を足さない', () => {
    const textarea = makeEditor()
    Object.defineProperty(textarea, 'clientWidth', { value: 180 })
    const append = vi.spyOn(document.body, 'append')
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 100 } as DOMRect)
    const source = 'abc\tdef  \n'.repeat(1000)
    scrollTextRangeIntoView(textarea, source, { start: 0, end: 1 })
    const mirror = append.mock.calls[0][0] as HTMLElement
    expect(source.startsWith(mirror.textContent!)).toBe(true)
    expect(mirror.textContent!.endsWith('  ')).toBe(true)
  })

  it('分割測定後も移動先の確定した座標を表示する', () => {
    vi.useFakeTimers()
    const textarea = makeEditor()
    Object.defineProperty(textarea, 'clientWidth', { value: 500 })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 100 } as DOMRect)
    const source = `${'合成段落です。'.repeat(10)}\n`.repeat(1000)
    scrollTextRangeIntoView(textarea, source, { start: source.length - 1, end: source.length })
    vi.runAllTimers()
    expect(textarea.scrollTop).toBe(300)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('折り返し後の表示座標とスクロールバーを除いた幅で対象を表示する', () => {
    const textarea = makeEditor()
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
    expect(document.body.childElementCount).toBe(childrenBefore + 1)
    clearTextRangeMeasurement(textarea)
    expect(document.body.childElementCount).toBe(childrenBefore)
  })

  it('本文・幅・折り返しの変更で現在の測定内容だけを更新する', () => {
    const textarea = makeEditor()
    let width = 180
    Object.defineProperty(textarea, 'clientWidth', { get: () => width })
    const append = vi.spyOn(document.body, 'append')
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 0 } as DOMRect)
    scrollTextRangeIntoView(textarea, '古い合成本文', { start: 1, end: 2 })
    const mirror = append.mock.calls[0][0] as HTMLElement
    width = 320
    textarea.wrap = 'off'
    scrollTextRangeIntoView(textarea, '新しい合成本文\n', { start: 9, end: 9 })
    expect(append).toHaveBeenCalledOnce()
    expect(mirror.textContent).toBe('新しい合成本文\n\u200b')
    expect(mirror.style.width).toBe('320px')
    expect(mirror.style.whiteSpace).toBe('pre')
    expect(mirror.querySelectorAll('*')).toHaveLength(1)
  })

  afterEach(() => {
    for (const editor of editors.splice(0)) clearTextRangeMeasurement(editor)
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('scrolls the selected scene line into the editor viewport', () => {
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      lineHeight: '28px',
      paddingTop: '28px',
    } as CSSStyleDeclaration)
    const textarea = makeEditor()
    Object.defineProperty(textarea, 'clientHeight', { configurable: true, value: 100 })
    const source = 'Title: Sample\n\nINT. ROOM - DAY #1#\n\nNarration.'
    const start = source.indexOf('INT.')

    scrollTextRangeIntoView(textarea, source, { start, end: start + 20 })

    // 2nd source line: padding (28) + 2 * line-height (56) - 35% viewport (35).
    expect(textarea.scrollTop).toBe(49)
  })
})
