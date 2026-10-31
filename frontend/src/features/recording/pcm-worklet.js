// AudioWorklet: posts mono Float32 chunks of 320 samples (20 ms at 16 kHz).
// When the context does not run at the target rate, input is downsampled by linear interpolation.
/* global AudioWorkletProcessor, registerProcessor, sampleRate */

const CHUNK = 320

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const target = (options && options.processorOptions && options.processorOptions.targetRate) || 16000
    this.step = sampleRate / target
    this.chunk = new Float32Array(CHUNK)
    this.fill = 0
    // Read position in input samples; -1 refers to the last sample of the previous block.
    this.pos = 0
    this.prev = 0
  }

  emit(value) {
    this.chunk[this.fill++] = value
    if (this.fill === CHUNK) {
      this.port.postMessage(this.chunk, [this.chunk.buffer])
      this.chunk = new Float32Array(CHUNK)
      this.fill = 0
    }
  }

  process(inputs) {
    const channels = inputs[0]
    if (!channels || channels.length === 0) return true
    const length = channels[0].length
    const mono = channels.length === 1 ? channels[0] : new Float32Array(length)
    if (channels.length > 1) {
      for (const channel of channels) for (let i = 0; i < length; i++) mono[i] += channel[i] / channels.length
    }
    if (this.step === 1) {
      for (let i = 0; i < length; i++) this.emit(mono[i])
      return true
    }
    while (this.pos <= length - 1) {
      const i0 = Math.floor(this.pos)
      const frac = this.pos - i0
      const a = i0 < 0 ? this.prev : mono[i0]
      const b = i0 + 1 < length ? mono[i0 + 1] : a
      this.emit(a + (b - a) * frac)
      this.pos += this.step
    }
    this.pos -= length
    this.prev = mono[length - 1]
    return true
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
