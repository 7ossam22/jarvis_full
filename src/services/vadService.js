// Voice Activity Detection using Web Audio API AnalyserNode
// Watches frequency energy; fires onSpeechStart / onSpeechEnd callbacks
export class VAD {
  constructor({ threshold = 10, silenceMs = 1300, onSpeechStart, onSpeechEnd } = {}) {
    this.threshold = threshold
    this.silenceMs = silenceMs
    this.onSpeechStart = onSpeechStart
    this.onSpeechEnd = onSpeechEnd

    this._ctx = null
    this._analyser = null
    this._buf = null
    this._speaking = false
    this._silenceTimer = null
    this._running = false
    this._raf = null
  }

  start(stream) {
    this._ctx = new AudioContext()
    this._analyser = this._ctx.createAnalyser()
    this._analyser.fftSize = 512
    this._buf = new Uint8Array(this._analyser.frequencyBinCount)
    this._ctx.createMediaStreamSource(stream).connect(this._analyser)
    this._running = true
    this._tick()
  }

  _level() {
    this._analyser.getByteFrequencyData(this._buf)
    return this._buf.reduce((s, v) => s + v, 0) / this._buf.length
  }

  _tick() {
    if (!this._running) return
    const lv = this._level()

    if (lv > this.threshold) {
      if (!this._speaking) {
        this._speaking = true
        this.onSpeechStart?.()
      }
      if (this._silenceTimer) { clearTimeout(this._silenceTimer); this._silenceTimer = null }
    } else if (this._speaking && !this._silenceTimer) {
      this._silenceTimer = setTimeout(() => {
        this._speaking = false
        this._silenceTimer = null
        this.onSpeechEnd?.()
      }, this.silenceMs)
    }

    this._raf = requestAnimationFrame(() => this._tick())
  }

  destroy() {
    this._running = false
    if (this._raf) cancelAnimationFrame(this._raf)
    if (this._silenceTimer) clearTimeout(this._silenceTimer)
    if (this._ctx) this._ctx.close().catch(() => {})
  }
}
