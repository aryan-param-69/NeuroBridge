import { SegmentedControl, TextInput } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Fragment, useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { FeatureRow } from '../../api/types'
import { fmtNum, useCatalog } from './common'

type Level = 'global' | 'region' | 'channel' | 'pair'

export default function FeaturesTab({ runId }: { runId: string }) {
  const [level, setLevel] = useState<Level>('global')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const cat = useCatalog()
  const { data, isLoading } = useQuery({ queryKey: ['features', runId, level], queryFn: () => api<{ rows: FeatureRow[] }>(`/runs/${runId}/features?level=${level}`) })
  const defs = useMemo(() => Object.fromEntries((cat.data?.features ?? []).map((f) => [f.name, f])), [cat.data])
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase()
    return (data?.rows ?? []).filter((r) => !t || `${r.feature} ${r.band ?? ''} ${r.name}`.toLowerCase().includes(t))
  }, [data, q])

  return (
    <div className="stack">
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'end' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>Level</div>
          <SegmentedControl value={level} onChange={(v) => setLevel(v as Level)} data={[{ value: 'global', label: 'Whole head' }, { value: 'region', label: 'Regions' }, { value: 'channel', label: 'Channels' }, { value: 'pair', label: 'L–R pairs' }]} />
        </div>
        <TextInput label="Search" placeholder="e.g. alpha, entropy, Fp1, frontal" style={{ flex: '1 1 260px' }} value={q} onChange={(e) => setQ(e.currentTarget.value)} />
        <span className="muted" style={{ fontSize: 13 }}>{rows.length} values</span>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'auto', maxHeight: 600 }}>
        <table className="data">
          <thead style={{ position: 'sticky', top: 0 }}><tr><th>Feature</th><th>Band / scale</th><th>Where</th><th>Mean</th><th>95 % CI</th><th>SD</th><th>Epochs</th><th>Unit</th></tr></thead>
          <tbody>
            {rows.slice(0, 400).map((r, i) => {
              const key = `${r.feature}|${r.band}|${r.name}|${i}`
              const d = defs[r.feature]
              return (
                <Fragment key={key}>
                  <tr style={{ cursor: 'pointer' }} onClick={() => setOpen(open === key ? null : key)}>
                    <td style={{ fontWeight: 500 }}>{r.feature.replace(/_/g, ' ')} <span className="muted" title="What does this mean?">ⓘ</span></td>
                    <td>{r.band?.replace(/_/g, ' ') ?? '—'}</td><td>{r.name.replace('_', ' ')}</td>
                    <td>{fmtNum(r.mean)}</td>
                    <td className="muted">{r.ci_low != null ? `${fmtNum(r.ci_low)} – ${fmtNum(r.ci_high)}` : 'n/a'}</td>
                    <td className="muted">{fmtNum(r.sd)}</td><td>{r.n_epochs}</td><td className="muted">{r.unit}</td>
                  </tr>
                  {open === key && d && (
                    <tr><td colSpan={8} style={{ background: 'var(--primary-soft)' }}>
                      <b>What it means:</b> {d.description}<br /><span className="muted"><b>How:</b> {d.how}{d.caution && <> · <b>Caution:</b> {d.caution}</>}</span>
                    </td></tr>
                  )}
                </Fragment>
              )
            })}
            {isLoading && <tr><td colSpan={8} className="muted">Loading…</td></tr>}
          </tbody>
        </table>
      </div>
      {rows.length > 400 && <p className="muted" style={{ fontSize: 13, margin: 0 }}>Showing the first 400 of {rows.length}; narrow the search or export the CSV for everything.</p>}
    </div>
  )
}
