import { Select } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import type { PsdResponse, SpectrogramResponse } from '../../api/types'
import { viridis } from './common'

const M = { l: 56, r: 70, t: 12, b: 36 }

function Heatmap({ d }: { d: SpectrogramResponse }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(900)
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setW(Math.max(480, el.clientWidth)))
    ro.observe(el); setW(Math.max(480, el.clientWidth))
    return () => ro.disconnect()
  }, [])
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const H = 380, dpr = window.devicePixelRatio || 1
    c.width = w * dpr; c.height = H * dpr
    const ctx = c.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, H)
    const nf = d.freqs_hz.length, nt = d.times_s.length
    if (!nf || !nt) return
    const flat = d.log10_power.flat().filter(Number.isFinite).sort((a, b) => a - b)
    const lo = flat[Math.floor(flat.length * 0.02)], hi = flat[Math.floor(flat.length * 0.98)]
    const pw = w - M.l - M.r, ph = H - M.t - M.b
    const tmax = d.times_s[nt - 1], fmax = d.freqs_hz[nf - 1], fmin = d.freqs_hz[0]
    // one filled rectangle per cell (cell edges from neighbouring centres)
    for (let j = 0; j < nt; j++) {
      const x0 = M.l + (d.times_s[j] - (d.times_s[1] - d.times_s[0]) / 2) / tmax * pw
      const cw = Math.max(1, (pw / nt) + 0.5)
      for (let i = 0; i < nf; i++) {
        ctx.fillStyle = viridis((d.log10_power[i][j] - lo) / (hi - lo || 1))
        const y = M.t + ph - ((d.freqs_hz[i] - fmin) / (fmax - fmin)) * ph
        ctx.fillRect(x0, y - ph / nf, cw, ph / nf + 0.5)
      }
    }
    // rejected epochs: red ticks along the top edge
    ctx.fillStyle = '#B42318'
    for (const e of d.rejected_epochs) ctx.fillRect(M.l + (e.onset / tmax) * pw, M.t, Math.max(1, (e.duration / tmax) * pw), 4)
    ctx.fillStyle = '#5B6B7F'; ctx.font = '11px Inter, sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'
    for (const f of [5, 10, 15, 20, 25, 30, 35, 40, 45].filter((f) => f <= fmax)) {
      const y = M.t + ph - ((f - fmin) / (fmax - fmin)) * ph
      ctx.fillText(`${f}`, M.l - 6, y)
      ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.beginPath(); ctx.moveTo(M.l, y); ctx.lineTo(M.l + pw, y); ctx.stroke()
    }
    ctx.save(); ctx.translate(14, M.t + ph / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center'; ctx.fillText('Frequency (Hz)', 0, 0); ctx.restore()
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'
    const step = tmax > 600 ? 120 : tmax > 200 ? 30 : 10
    for (let t = 0; t <= tmax; t += step) ctx.fillText(`${t}s`, M.l + (t / tmax) * pw, M.t + ph + 6)
    // colour bar
    for (let k = 0; k < ph; k++) { ctx.fillStyle = viridis(1 - k / ph); ctx.fillRect(w - M.r + 18, M.t + k, 12, 1.5) }
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle'
    ctx.fillText(hi.toFixed(1), w - M.r + 34, M.t + 6); ctx.fillText(lo.toFixed(1), w - M.r + 34, M.t + ph - 6)
    ctx.fillText('log10 µV²/Hz', w - M.r + 4, M.t + ph + 26)
  }, [d, w])
  return <div ref={wrap}><canvas ref={ref} style={{ width: w, height: 380, display: 'block' }} role="img" aria-label={`Spectrogram of channel ${d.channel}`} /></div>
}

export default function SpectrogramTab({ runId }: { runId: string }) {
  const psd = useQuery({ queryKey: ['psd-meta', runId], queryFn: () => api<PsdResponse>(`/runs/${runId}/psd`) })
  const [channel, setChannel] = useState<string | null>(null)
  const chans = psd.data?.available_channels ?? []
  const ch = channel ?? (chans.includes('Oz') ? 'Oz' : chans.includes('O1') ? 'O1' : chans[0] ?? null)
  const { data, isFetching, error } = useQuery({ queryKey: ['spec', runId, ch], enabled: !!ch, queryFn: () => api<SpectrogramResponse>(`/runs/${runId}/spectrogram?channel=${ch}`), placeholderData: (p) => p })
  return (
    <div className="stack">
      <Select label="Channel" w={200} searchable allowDeselect={false} value={ch} onChange={setChannel} data={chans} />
      {error && <div className="notice error">{(error as Error).message}</div>}
      <div className="card" style={{ padding: 16, opacity: isFetching ? 0.7 : 1 }}>
        {data ? <Heatmap d={data} /> : <p className="muted">Loading…</p>}
      </div>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        How the strength of each frequency changes over time (2 s windows, stepped by 0.5 s). Brighter means stronger. Red ticks along the top mark epochs that were rejected during processing.
      </p>
    </div>
  )
}
