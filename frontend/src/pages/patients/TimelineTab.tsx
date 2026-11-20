import { Checkbox, TextInput } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import type { Timeline, TimelineItem } from '../../api/types'

const LANES = [
  { key: 'recording', label: 'EEG recordings', color: '#1F5FAE' }, { key: 'assessment', label: 'CRS-R', color: '#0F8C8C' },
  { key: 'event', label: 'Clinical events', color: '#B42318' }, { key: 'medication', label: 'Medication', color: '#6B4FA0' },
  { key: 'note', label: 'Notes', color: '#B7791F' },
] as const
const fmt = (d: string) => new Date(d).toISOString().replace('T', ' ').slice(0, 16)
const W = 960, LEFT = 120, ROW = 52, TOP = 24

export default function TimelineTab({ patientId }: { patientId: string }) {
  const [lanes, setLanes] = useState<string[]>(LANES.map((l) => l.key))
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [sel, setSel] = useState<TimelineItem | null>(null)
  const [hideOdd, setHideOdd] = useState(true)
  const qs = new URLSearchParams({ lanes: lanes.join(',') })
  if (from) qs.set('from', `${from}T00:00:00Z`)
  if (to) qs.set('to', `${to}T23:59:59Z`)
  const { data, error } = useQuery({
    queryKey: ['timeline', patientId, lanes.join(','), from, to],
    queryFn: () => api<Timeline>(`/patients/${patientId}/timeline?${qs.toString()}`), enabled: lanes.length > 0,
  })

  const odd = (data?.items ?? []).filter((i) => i.badges.includes('dated before injury'))
  const items = useMemo(() => (hideOdd ? (data?.items ?? []).filter((i) => !i.badges.includes('dated before injury')) : (data?.items ?? [])), [data, hideOdd])
  const { x, ticks } = useMemo(() => {
    if (!items.length) return { x: () => LEFT, ticks: [] as { t: number; label: string }[] }
    const t0 = Math.min(...items.map((i) => +new Date(i.start))), t1 = Math.max(...items.map((i) => +new Date(i.end ?? i.start)), t0 + 86400000)
    const pad = (t1 - t0) * 0.03
    const a = t0 - pad, b = t1 + pad
    const sx = (t: number) => LEFT + ((t - a) / (b - a)) * (W - LEFT - 16)
    const n = 6
    return { x: (s: string) => sx(+new Date(s)), ticks: Array.from({ length: n + 1 }, (_, k) => { const t = a + ((b - a) * k) / n; return { t: sx(t), label: new Date(t).toISOString().slice(0, 10) } }) }
  }, [items])

  const shown = LANES.filter((l) => lanes.includes(l.key))
  const height = TOP + shown.length * ROW + 8
  return (
    <>
      <div className="card" style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'end', marginBottom: 12 }}>
        <Checkbox.Group value={lanes} onChange={setLanes} label="Lanes">
          <div style={{ display: 'flex', gap: 14, marginTop: 6, flexWrap: 'wrap' }}>
            {LANES.map((l) => <Checkbox key={l.key} value={l.key} label={`${l.label}${data ? ` (${data.counts[l.key] ?? 0})` : ''}`} />)}
          </div>
        </Checkbox.Group>
        <TextInput label="From" type="date" value={from} onChange={(e) => setFrom(e.currentTarget.value)} />
        <TextInput label="To" type="date" value={to} onChange={(e) => setTo(e.currentTarget.value)} />
      </div>
      {error && <div className="notice error">{(error as Error).message}</div>}
      {data && <p className="muted" style={{ marginTop: 0 }}>{data.note} Click an item for details.</p>}
      {odd.length > 0 && (
        <div className="notice warn">
          {odd.length} recording(s) have a header date before the date of injury ({odd.map((o) => o.title).join(', ')}); the EDF date may be wrong.{' '}
          <Checkbox style={{ display: 'inline-block' }} size="xs" label="Hide them" checked={hideOdd} onChange={(e) => setHideOdd(e.currentTarget.checked)} />
        </div>
      )}
      {data && items.length === 0 && <p className="muted">Nothing to show for these lanes and dates.</p>}
      {data && items.length > 0 && (
        <div className="card" style={{ overflowX: 'auto', padding: 8 }}>
          <svg viewBox={`0 0 ${W} ${height}`} width="100%" style={{ minWidth: 720 }} role="img" aria-label="Patient timeline with one lane per data type">
            {ticks.map((k) => (
              <g key={k.t}><line x1={k.t} x2={k.t} y1={TOP - 6} y2={height - 4} stroke="#E3E8EF" /><text x={k.t} y={12} fontSize={10} textAnchor="middle" fill="#5B6B7F">{k.label}</text></g>
            ))}
            {shown.map((l, r) => {
              const y = TOP + r * ROW + ROW / 2
              return (
                <g key={l.key}>
                  <line x1={LEFT} x2={W - 16} y1={y} y2={y} stroke="#EEF1F6" />
                  <text x={8} y={y + 4} fontSize={12} fill="#1B2733">{l.label}</text>
                  {items.filter((i) => i.lane === l.key).map((i) => {
                    const on = sel?.id === i.id && sel.lane === i.lane
                    const span = i.end != null && x(i.end) - x(i.start) > 6
                    return span ? (
                      <rect key={`${i.lane}${i.id}`} x={x(i.start)} y={y - 9} width={x(i.end!) - x(i.start)} height={18} rx={4} fill={l.color} opacity={on ? 1 : 0.55}
                        stroke={on ? '#1B2733' : 'none'} tabIndex={0} role="button" aria-label={`${i.title}, ${fmt(i.start)}`} style={{ cursor: 'pointer' }}
                        onClick={() => setSel(i)} onKeyDown={(e) => e.key === 'Enter' && setSel(i)} />
                    ) : (
                      <circle key={`${i.lane}${i.id}`} cx={x(i.start)} cy={y} r={on ? 8 : 6} fill={l.color} stroke={on ? '#1B2733' : '#fff'} strokeWidth={2}
                        tabIndex={0} role="button" aria-label={`${i.title}, ${fmt(i.start)}`} style={{ cursor: 'pointer' }}
                        onClick={() => setSel(i)} onKeyDown={(e) => e.key === 'Enter' && setSel(i)} />
                    )
                  })}
                </g>
              )
            })}
          </svg>
        </div>
      )}
      {sel && (
        <div className="card" style={{ marginTop: 12 }}>
          <strong>{sel.title}</strong> <span className="muted">· {LANES.find((l) => l.key === sel.lane)?.label}</span>
          <div>{fmt(sel.start)}{sel.end && ` → ${fmt(sel.end)}`}</div>
          {sel.subtitle && <div className="muted">{sel.subtitle}</div>}
          <div style={{ marginTop: 6 }}>{sel.badges.map((b) => <span key={b} className="pill role" style={{ marginRight: 6 }}>{b}</span>)}</div>
          {sel.link && <p style={{ marginBottom: 0 }}><Link to={sel.link}>Open</Link></p>}
        </div>
      )}
    </>
  )
}
