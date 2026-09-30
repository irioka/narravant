export interface SseEvent {
  event: string
  data: unknown
  id?: string
}

function parseData(lines: string[]): unknown {
  const values = lines.map((line) => JSON.parse(line) as unknown)
  return values.length === 1 ? values[0] : values
}

/**
 * Minimal SSE frame reconstructor used by both tests and the fetch stream.
 */
export function parseSseChunks(chunks: Iterable<string>): SseEvent[] {
  const events: SseEvent[] = []
  let buffer = ''

  const consumeFrame = (frame: string) => {
    const data: string[] = []
    let event = 'message'
    let id: string | undefined

    for (const line of frame.split('\n')) {
      if (line.startsWith(':')) continue
      if (line.startsWith('event:')) event = line.slice('event:'.length).trim()
      if (line.startsWith('id:')) id = line.slice('id:'.length).trim()
      if (line.startsWith('data:')) data.push(line.slice('data:'.length).trimStart())
    }
    if (data.length > 0) events.push({ event, data: parseData(data), ...(id ? { id } : {}) })
  }

  for (const chunk of chunks) {
    buffer += chunk.replaceAll('\r\n', '\n')
    let separator = buffer.indexOf('\n\n')

    while (separator >= 0) {
      const frame = buffer.slice(0, separator)
      buffer = buffer.slice(separator + 2)
      separator = buffer.indexOf('\n\n')
      consumeFrame(frame)
    }
  }

  if (buffer.trim()) consumeFrame(buffer)

  return events
}

export interface ConsumeSseStreamResult {
  receivedBytes: number
  eventCount: number
}

export async function consumeSseStream(
  response: Response,
  onEvent: (event: SseEvent) => void,
): Promise<ConsumeSseStreamResult> {
  if (!response.body) throw new Error('The SSE response has no body.')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let receivedBytes = 0
  let eventCount = 0

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    if (value) {
      receivedBytes += value.length
    }
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const event of parseSseChunks(frames.map((frame) => `${frame}\n\n`))) {
      eventCount += 1
      onEvent(event)
    }
  }

  return { receivedBytes, eventCount }
}
