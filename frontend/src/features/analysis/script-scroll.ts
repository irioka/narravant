export function scrollTextRangeIntoView(element: HTMLTextAreaElement, source: string, range: { start: number; end: number }): void {
  const style = window.getComputedStyle(element)
  const lineHeight = Number.parseFloat(style.lineHeight)
  const paddingTop = Number.parseFloat(style.paddingTop)
  const safeLineHeight = Number.isFinite(lineHeight) && lineHeight > 0 ? lineHeight : 28
  const safePaddingTop = Number.isFinite(paddingTop) ? paddingTop : 0
  const lineNumber = source.slice(0, range.start).split('\n').length - 1
  let targetTop = safePaddingTop + lineNumber * safeLineHeight

  if (element.clientWidth > 0) {
    // textareaの折り返しと同じ幅・文字組みで、選択開始位置の表示座標を測る。
    // clientWidthはpaddingを含み、borderと縦スクロールバーを含まない。
    const mirror = document.createElement('div')
    mirror.style.cssText = 'position:absolute;left:-100000px;top:0;visibility:hidden;pointer-events:none;box-sizing:border-box;border:0;margin:0'
    for (const property of [
      'font-family', 'font-size', 'font-weight', 'font-style', 'font-stretch',
      'font-variant', 'font-kerning', 'font-feature-settings', 'font-variation-settings',
      'line-height', 'letter-spacing', 'word-spacing', 'tab-size',
      'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
      'text-indent', 'text-align', 'text-transform', 'direction', 'word-break', 'line-break',
    ]) {
      mirror.style.setProperty(property, style.getPropertyValue(property))
    }
    mirror.style.width = `${element.clientWidth}px`
    mirror.style.whiteSpace = element.wrap === 'off' ? 'pre' : 'pre-wrap'
    mirror.style.overflowWrap = style.overflowWrap || 'break-word'
    // HTMLとして解釈せず、演出タグや任意の入力文字列もそのまま測る。
    mirror.textContent = source.slice(0, range.start)
    const marker = document.createElement('span')
    marker.textContent = source.slice(range.start) || '\u200b'
    mirror.append(marker)
    try {
      document.body.append(mirror)
      targetTop = marker.getBoundingClientRect().top - mirror.getBoundingClientRect().top
    } finally {
      mirror.remove()
    }
  }
  const viewportOffset = element.clientHeight > 0 ? element.clientHeight * 0.35 : 0
  element.scrollTop = Math.max(0, targetTop - viewportOffset)
}
