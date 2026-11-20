import { useCallback, useEffect, useRef, useState } from 'react'
import type { SegmentAnnotation, SignalWindow } from '../../api/types'

const LABEL_W = 76
const AXIS_H = 26
const PX_PER_MM = 3.78 // 96 dpi
const TRACE = '#1E293B'

type Hover = { x: number; y: number } | null
const REJECT_LABELS = ['artifact_eye', 'artifact_muscle', 'artifact_movement', 'electrode_pop', 'flat', 'excluded']

/** Clinical-style EEG viewer: stacked channels on a 1 s / 0.2 s grid, gain in uV/mm, annotations shaded. */
export default function EegCanvas({ win, sensitivity, height = 640, userAnnotations = [], onSelect }: {
  win: SignalWindow; sensitivity: number; height?: number; userAnnotations?: SegmentAnnotation[]
  onSelect?: (startS: number, endS: number) => void
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [width, setWidth] = useState(900)
  const [hover, setHover] = useState<Hover>(null)
  const [drag, setDrag] = useState<{ x0: number; x1: number } | null>(null)

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(Math.max(480, el.clientWidth)))
    ro.observe(el)
    setWidth(Math.max(480, el.clientWidth))
    return () => ro.disconnect()
  }, [])

  const draw = useCallback(() => {
    const c = canvas.current
    if (!c) return
    const dpr = window.devicePixelRatio || 1
    c.width = width * dpr; c.height = height * dpr
    const ctx = c.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, width, height)

    const n = win.channels.length
    const plotW = width - LABEL_W
    const plotH = height - AXIS_H
    const rowH = plotH / Math.max(n, 1)
    const t0 = win.start, dur = win.duration
    const x = (t: number) => LABEL_W + ((t - t0) / dur) * plotW

    // annotations
    for (const a of win.annotations) {
      const x0 = Math.max(LABEL_W, x(a.onset)), x1 = Math.min(width, x(a.onset + Math.max(a.duration, 0.05)))
      if (x1 <= x0) continue
      ctx.fillStyle = a.text === 'T0' ? 'rgba(15,28,46,0.03)' : 'rgba(183,121,31,0.12)'
      ctx.fillRect(x0, 0, x1 - x0, plotH)
      ctx.fillStyle = '#B7791F'; ctx.font = '11px Inter, sans-serif'; ctx.textAlign = 'left'
      ctx.fillText(a.text, x0 + 3, 11)
    }

    // rejected epochs (processed view) and the user's own segments
    for (const e of win.epochs ?? []) {
      const x0 = Math.max(LABEL_W, x(e.onset)), x1 = Math.min(width, x(e.onset + e.duration))
      if (x1 <= x0) continue
      ctx.fillStyle = e.status === 3 ? 'rgba(91,107,127,0.10)' : e.status === 2 ? 'rgba(180,35,24,0.10)' : 'rgba(180,35,24,0.07)'
      ctx.fillRect(x0, 0, x1 - x0, plotH)
    }
    for (const a of userAnnotations) {
      const x0 = Math.max(LABEL_W, x(a.onset_s)), x1 = Math.min(width, x(a.onset_s + a.duration_s))
      if (x1 <= x0) continue
      const reject = REJECT_LABELS.includes(a.label)
      ctx.fillStyle = reject ? 'rgba(180,35,24,0.16)' : 'rgba(15,140,140,0.13)'
      ctx.fillRect(x0, 0, x1 - x0, plotH)
      ctx.fillStyle = reject ? '#B42318' : '#0F8C8C'; ctx.font = '11px Inter, sans-serif'; ctx.textAlign = 'left'
      ctx.fillText(a.label.replace(/_/g, ' '), x0 + 3, plotH - 6)
    }

    // grid: 0.2 s light, 1 s medium, 5 s stronger
    const first = Math.ceil(t0 / 0.2) * 0.2
    for (let t = first; t <= t0 + dur + 1e-9; t += 0.2) {
      const whole = Math.abs(t - Math.round(t)) < 1e-6
      const five = whole && Math.round(t) % 5 === 0
      ctx.strokeStyle = five ? '#D5DCE5' : whole ? '#E6EBF1' : '#F5F7FA'
      ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(Math.round(x(t)) + 0.5, 0); ctx.lineTo(Math.round(x(t)) + 0.5, plotH); ctx.stroke()
    }

    // channels
    const pxPerUv = PX_PER_MM / sensitivity
    const maxDeflect = rowH * 1.6
    ctx.font = '12px Inter, sans-serif'
    win.channels.forEach((name, i) => {
      const y0 = i * rowH + rowH / 2
      const bad = win.bad_channels.includes(name)
      ctx.strokeStyle = '#EEF2F6'; ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(LABEL_W, Math.round(i * rowH + rowH) + 0.5); ctx.lineTo(width, Math.round(i * rowH + rowH) + 0.5); ctx.stroke()
      ctx.fillStyle = bad ? '#94A3B8' : '#0F1C2E'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'
      ctx.fillText(name + (bad ? ' ⚠' : '') + (win.interpolated?.includes(name) ? ' ≈' : ''), LABEL_W - 8, y0)

      ctx.strokeStyle = bad ? '#94A3B8' : TRACE; ctx.lineWidth = 1
      ctx.setLineDash(bad ? [4, 3] : [])
      ctx.beginPath()
      const d = win.data[i]
      for (let k = 0; k < d.length; k++) {
        const dy = Math.max(-maxDeflect, Math.min(maxDeflect, d[k] * pxPerUv))
        const px = x(win.times[k]), py = y0 - dy
        if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
      }
      ctx.stroke(); ctx.setLineDash([])
    })

    // label column separator and time axis
    ctx.strokeStyle = '#E3E8EE'; ctx.beginPath(); ctx.moveTo(LABEL_W + 0.5, 0); ctx.lineTo(LABEL_W + 0.5, plotH); ctx.stroke()
    ctx.strokeStyle = '#E3E8EE'; ctx.beginPath(); ctx.moveTo(0, plotH + 0.5); ctx.lineTo(width, plotH + 0.5); ctx.stroke()
    ctx.fillStyle = '#5B6B7F'; ctx.font = '11px Inter, sans-serif'; ctx.textBaseline = 'top'; ctx.textAlign = 'center'
    const step = dur <= 12 ? 1 : dur <= 25 ? 2 : 5
    for (let t = Math.ceil(t0 / step) * step; t <= t0 + dur + 1e-9; t += step) {
      ctx.fillText(`${t}s`, x(t), plotH + 7)
    }

    // amplitude scale bar
    const barUv = sensitivity * 7 // ~7 mm tall
    const barPx = barUv * pxPerUv
    ctx.strokeStyle = '#0F1C2E'; ctx.lineWidth = 2
    ctx.beginPath(); ctx.moveTo(width - 14, plotH - 10); ctx.lineTo(width - 14, plotH - 10 - barPx); ctx.stroke()
    ctx.fillStyle = '#0F1C2E'; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom'
    ctx.fillText(`${Math.round(barUv)} µV`, width - 20, plotH - 10)

    if (drag) {
      const a = Math.max(LABEL_W, Math.min(drag.x0, drag.x1)), b = Math.min(width, Math.max(drag.x0, drag.x1))
      ctx.fillStyle = 'rgba(31,95,174,0.16)'; ctx.fillRect(a, 0, b - a, plotH)
      ctx.strokeStyle = '#1F5FAE'; ctx.lineWidth = 1; ctx.strokeRect(a + 0.5, 0.5, b - a, plotH - 1)
    }

    // crosshair + readout
    if (hover && hover.x > LABEL_W && hover.y < plotH && n > 0) {
      const t = t0 + ((hover.x - LABEL_W) / plotW) * dur
      const row = Math.min(n - 1, Math.max(0, Math.floor(hover.y / rowH)))
      let kBest = 0, best = Infinity
      for (let k = 0; k < win.times.length; k++) { const dd = Math.abs(win.times[k] - t); if (dd < best) { best = dd; kBest = k } }
      ctx.strokeStyle = 'rgba(31,95,174,0.55)'; ctx.lineWidth = 1
      ctx.beginPath(); ctx.moveTo(hover.x + 0.5, 0); ctx.lineTo(hover.x + 0.5, plotH); ctx.stroke()
      const text = `${win.channels[row]}  ${t.toFixed(2)} s  ${win.data[row][kBest].toFixed(1)} µV`
      ctx.font = '12px Inter, sans-serif'
      const tw = ctx.measureText(text).width + 14
      const bx = Math.min(width - tw - 4, hover.x + 10), by = Math.max(4, hover.y - 28)
      ctx.fillStyle = 'rgba(15,28,46,0.92)'; ctx.fillRect(bx, by, tw, 22)
      ctx.fillStyle = '#fff'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(text, bx + 7, by + 11)
    }
  }, [win, sensitivity, width, height, hover, userAnnotations, drag])

  useEffect(() => { draw() }, [draw])

  return (
    <div ref={wrap} style={{ width: '100%' }}>
      <canvas ref={canvas} role="img" style={{ width, height, display: 'block', cursor: 'crosshair' }}
        aria-label={`EEG traces, ${win.channels.length} channels, ${win.start.toFixed(0)} to ${(win.start + win.duration).toFixed(0)} seconds`}
        onMouseDown={(e) => { if (!onSelect) return; const r = e.currentTarget.getBoundingClientRect(); const px = e.clientX - r.left; if (px > LABEL_W) setDrag({ x0: px, x1: px }) }}
        onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); setHover({ x: e.clientX - r.left, y: e.clientY - r.top }); if (drag) setDrag({ ...drag, x1: e.clientX - r.left }) }}
        onMouseUp={() => {
          if (drag && onSelect && Math.abs(drag.x1 - drag.x0) > 8) {
            const plotW = width - LABEL_W
            const t = (px: number) => win.start + (Math.max(LABEL_W, Math.min(width, px)) - LABEL_W) / plotW * win.duration
            const a = t(Math.min(drag.x0, drag.x1)), b = t(Math.max(drag.x0, drag.x1))
            onSelect(Math.round(a * 100) / 100, Math.round(b * 100) / 100)
          }
          setDrag(null)
        }}
        onMouseLeave={() => { setHover(null); setDrag(null) }} />
    </div>
  )
}
