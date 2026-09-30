export function scrollTextRangeIntoView(element: HTMLTextAreaElement, source: string, range: { start: number; end: number }): void {
  const style = window.getComputedStyle(element)
  const lineHeight = Number.parseFloat(style.lineHeight)
  const paddingTop = Number.parseFloat(style.paddingTop)
  const safeLineHeight = Number.isFinite(lineHeight) && lineHeight > 0 ? lineHeight : 28
  const safePaddingTop = Number.isFinite(paddingTop) ? paddingTop : 0
  const lineNumber = source.slice(0, range.start).split('\n').length - 1
  const targetTop = safePaddingTop + lineNumber * safeLineHeight
  const viewportOffset = element.clientHeight > 0 ? element.clientHeight * 0.35 : 0
  element.scrollTop = Math.max(0, targetTop - viewportOffset)
}
