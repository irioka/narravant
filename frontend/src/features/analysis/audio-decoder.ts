/**
 * Utility to decode 16-bit linear PCM (24kHz, 1 channel) into AudioBuffer.
 */

export function base64ToUint8Array(base64: string): Uint8Array {
  const binaryString = window.atob(base64)
  const bytes = new Uint8Array(binaryString.length)
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i)
  }
  return bytes
}

export function pcm16ToAudioBuffer(
  pcmChunks: Uint8Array[],
  audioContext: AudioContext,
  sampleRate = 24000,
): AudioBuffer {
  const totalBytes = pcmChunks.reduce((acc, chunk) => acc + chunk.byteLength, 0)
  const merged = new Uint8Array(totalBytes)
  let offset = 0
  for (const chunk of pcmChunks) {
    merged.set(chunk, offset)
    offset += chunk.byteLength
  }

  // 16-bit signed integer PCM -> 2 bytes per sample
  const numSamples = Math.floor(merged.byteLength / 2)
  const dataView = new DataView(merged.buffer, merged.byteOffset, merged.byteLength)
  const floatSamples = new Float32Array(numSamples)

  for (let i = 0; i < numSamples; i++) {
    const int16 = dataView.getInt16(i * 2, true) // little-endian
    // Normalize -32768..32767 to -1.0..1.0
    floatSamples[i] = int16 < 0 ? int16 / 32768 : int16 / 32767
  }

  const audioBuffer = audioContext.createBuffer(1, Math.max(1, numSamples), sampleRate)
  audioBuffer.getChannelData(0).set(floatSamples)
  return audioBuffer
}

/** Play PCM, releasing the source and context on completion, abort, or failure. */
export async function playPcm16(
  pcm: Uint8Array,
  sampleRate = 24000,
  createContext: () => AudioContext | null = defaultAudioContext,
  signal?: AbortSignal,
): Promise<void> {
  const context = createContext()
  if (!context) return
  let source: AudioBufferSourceNode | undefined
  let finish!: () => void
  const aborted = new Promise<void>((resolve) => { finish = resolve })
  const stop = () => finish()
  signal?.addEventListener('abort', stop, { once: true })
  try {
    if (signal?.aborted) return
    if (context.state === 'suspended') {
      await Promise.race([context.resume(), aborted])
      if (signal?.aborted) return
    }
    const buffer = pcm16ToAudioBuffer([pcm], context, sampleRate)
    source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)
    source.onended = finish
    if (signal?.aborted) return
    source.start(0)
    await aborted
  } finally {
    signal?.removeEventListener('abort', stop)
    if (source) {
      source.onended = null
      try { source.stop() } catch { /* The source may already have ended. */ }
      source.disconnect()
    }
    if (context.state !== 'closed') await context.close()
  }
}

export function defaultAudioContext(): AudioContext | null {
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  return AudioCtx ? new AudioCtx({ sampleRate: 24000 }) : null
}
