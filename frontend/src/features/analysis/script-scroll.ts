const MEASUREMENT_PROPERTIES = [
  'font-family', 'font-size', 'font-weight', 'font-style', 'font-stretch',
  'font-variant', 'font-kerning', 'font-feature-settings', 'font-variation-settings',
  'line-height', 'letter-spacing', 'word-spacing', 'tab-size',
  'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
  'text-indent', 'text-align', 'text-transform', 'direction', 'word-break', 'line-break',
]

interface MeasurementBlock {
  start: number
  end: number
  text: Text
}

interface TextRangeMeasurement {
  mirror: HTMLDivElement
  blocks: MeasurementBlock[]
  nextOffset: number
  caret: Range
  source: string
  styleKey: string
  requestedStart: number
  generation: number
  timer?: ReturnType<typeof setTimeout>
}

const MEASUREMENT_BLOCK_CHARACTERS = 2048
const MAX_BLOCKS_PER_TASK = 2
const measurements = new WeakMap<HTMLTextAreaElement, TextRangeMeasurement>()

export function clearTextRangeMeasurement(element: HTMLTextAreaElement): void {
  const measurement = measurements.get(element)
  if (measurement?.timer !== undefined) clearTimeout(measurement.timer)
  measurement?.mirror.remove()
  measurements.delete(element)
}

function measurePosition(element: HTMLTextAreaElement, measurement: TextRangeMeasurement): void {
  if (measurements.get(element) !== measurement) return
  const { source } = measurement
  // 改行で分けた小さいblockを追加する。既に測定した前方のレイアウトは再利用する。
  // 一つの論理行は折り返しを変えずに扱うため、途中では分断しない。
  for (let added = 0; measurement.nextOffset <= measurement.requestedStart && added < MAX_BLOCKS_PER_TASK; added++) {
    const start = measurement.nextOffset
    const newline = source.indexOf('\n', start + MEASUREMENT_BLOCK_CHARACTERS)
    const end = newline < 0 ? source.length : newline
    const contentEnd = newline >= 0 && source[end - 1] === '\r' ? end - 1 : end
    const block = document.createElement('div')
    // text-indentは、元の全文と同じく文書の最初の行だけに適用する。
    if (measurement.blocks.length) block.style.textIndent = '0'
    const content = source.slice(start, contentEnd)
    // 境界の行末空白をぶら下げたままにする。末尾markerは空行と文書末尾だけに置く。
    const marker = newline < 0 || !content || content.endsWith('\n') ? '\u200b' : ''
    const text = document.createTextNode(content + marker)
    block.append(text)
    measurement.mirror.append(block)
    measurement.blocks.push({ start, end, text })
    measurement.nextOffset = newline < 0 ? source.length + 1 : end + 1
  }
  if (measurement.nextOffset <= measurement.requestedStart) {
    // 全文をまとめて強制layoutせず、ここまでの小分けのlayoutを確定して次のtaskへ譲る。
    measurement.mirror.getBoundingClientRect()
    const generation = measurement.generation
    measurement.timer = setTimeout(() => {
      if (measurements.get(element) !== measurement || measurement.generation !== generation) return
      measurement.timer = undefined
      measurePosition(element, measurement)
    }, 0)
    return
  }
  const block = measurement.blocks.find((item) => item.start <= measurement.requestedStart && measurement.requestedStart <= item.end)
  if (!block) return
  const offset = Math.min(measurement.requestedStart - block.start, block.text.length)
  measurement.caret.setStart(block.text, offset)
  measurement.caret.setEnd(block.text, Math.min(offset + 1, block.text.length))
  const targetTop = measurement.caret.getBoundingClientRect().top - measurement.mirror.getBoundingClientRect().top
  const viewportOffset = element.clientHeight > 0 ? element.clientHeight * 0.35 : 0
  element.scrollTop = Math.max(0, targetTop - viewportOffset)
}

export function scrollTextRangeIntoView(element: HTMLTextAreaElement, source: string, range: { start: number; end: number }): void {
  const style = window.getComputedStyle(element)
  if (element.clientWidth <= 0) {
    clearTextRangeMeasurement(element)
    const lineHeight = Number.parseFloat(style.lineHeight)
    const paddingTop = Number.parseFloat(style.paddingTop)
    const safeLineHeight = Number.isFinite(lineHeight) && lineHeight > 0 ? lineHeight : 28
    const safePaddingTop = Number.isFinite(paddingTop) ? paddingTop : 0
    const lineNumber = source.slice(0, range.start).split('\n').length - 1
    const viewportOffset = element.clientHeight > 0 ? element.clientHeight * 0.35 : 0
    element.scrollTop = Math.max(0, safePaddingTop + lineNumber * safeLineHeight - viewportOffset)
    return
  }
  let measurement = measurements.get(element)
  if (!measurement) {
    measurement = {
      mirror: document.createElement('div'), blocks: [], nextOffset: 0,
      caret: document.createRange(), source: '', styleKey: '', requestedStart: 0, generation: 0,
    }
    measurements.set(element, measurement)
  }
  // clientWidthはpaddingを含み、borderと縦スクロールバーを含まない。
  const styleKey = 'position:absolute;left:-100000px;top:0;visibility:hidden;pointer-events:none;box-sizing:border-box;border:0;margin:0;'
    + MEASUREMENT_PROPERTIES.map((property) => `${property}:${style.getPropertyValue(property)};`).join('')
    + `width:${element.clientWidth}px;white-space:${element.wrap === 'off' ? 'pre' : 'pre-wrap'};overflow-wrap:${style.overflowWrap || 'break-word'};`
  if (measurement.source !== source || measurement.styleKey !== styleKey) {
    if (measurement.timer !== undefined) clearTimeout(measurement.timer)
    measurement.timer = undefined
    measurement.generation++
    measurement.mirror.replaceChildren()
    measurement.blocks = []
    measurement.nextOffset = 0
    measurement.source = source
    measurement.mirror.style.cssText = styleKey
    measurement.styleKey = styleKey
  }
  if (!measurement.mirror.isConnected) document.body.append(measurement.mirror)
  measurement.requestedStart = Math.max(0, Math.min(source.length, range.start))
  // 測定中の発話が進んだ場合も、未完了の仕事は一つにまとめて最新位置へ追従する。
  if (measurement.timer === undefined) measurePosition(element, measurement)
}
