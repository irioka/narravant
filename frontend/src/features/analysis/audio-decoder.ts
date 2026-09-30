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

/**
 * Play a single raw 16-bit PCM (24kHz, mono) buffer through an AudioContext and
 * resolve when playback ends. Creates (and returns) the AudioContext so callers
 * can reuse or close it; in non-audio environments (tests) it resolves immediately.
 */
export async function playPcm16(
  pcm: Uint8Array,
  sampleRate = 24000,
  createContext: () => AudioContext | null = defaultAudioContext,
): Promise<void> {
  const context = createContext()
  if (!context) return
  if (context.state === 'suspended') {
    await context.resume()
  }
  const buffer = pcm16ToAudioBuffer([pcm], context, sampleRate)
  await new Promise<void>((resolve) => {
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(context.destination)
    source.onended = () => resolve()
    source.start(0)
  })
}

function defaultAudioContext(): AudioContext | null {
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  return AudioCtx ? new AudioCtx({ sampleRate: 24000 }) : null
}
