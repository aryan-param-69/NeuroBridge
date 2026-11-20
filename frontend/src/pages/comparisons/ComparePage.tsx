import { Select } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../../api/client'
import type { CmpCandidates, CmpItem, CmpRun, Comparison, FeatureTrend } from '../../api/types'

const Charts = {
  Paired: lazy(() => import('./CompareCharts').then((m) => ({ default: m.PairedChart }))),
  Trend: lazy(() => import('./CompareCharts').then((m) => ({ default: m.TrendWithCrsr }))),
}
const fmtDate = (d: string | null) => (d ? new Date(d).toISOString().slice(0, 16).replace('T', ' ') : '—')
const num = (v: number | null | undefined) => (v == null ? '—' : Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3))
const label = (s: string) => s.replace(/_/g, ' ')
const runLabel = (r: CmpRun) => `${r.recording_code} · ${fmtDate(r.start)} · ${label(r.condition ?? 'condition?')}${r.is_baseline ? ' ★' : ''}`
const itemLabel = (i: CmpItem) => `${i.feature}${i.band ? ` ${i.band}` : ''} · ${i.name}`

function Side({ r, title }: { r: CmpRun; title: string }) {
  return (
    <div className="card" style={{ flex: 1, minWidth: 240 }}>
      <strong>{title}</strong>
      <div className="mono">{r.recording_code}</div>
      <div className="muted" style={{ fontSize: 12 }}>
        {fmtDate(r.start)} · {label(r.condition ?? 'unknown condition')} · {r.patient_state ?? 'state ?'} · quality {r.quality_grade ?? '—'}<br />
        {r.sedation.length ? `Sedation: ${r.sedation.join(', ')}` : 'No sedation recorded'} · {r.n_epochs} epochs · {r.pipeline}
      </div>
    </div>
  )
}

export default function ComparePage() {
  const { pid } = useParams()
  const cand = useQuery({ queryKey: ['cmp-cand', pid], queryFn: () => api<CmpCandidates>(`/patients/${pid}/comparison-candidates`) })
  const [baseSel, setBase] = useState<string | null>(null)
  const [targetSel, setTarget] = useState<string | null>(null)
  const base = baseSel ?? cand.data?.default_base ?? null
  const target = targetSel ?? cand.data?.default_target ?? null
  const [level, setLevel] = useState<string | null>('global')
  const [feat, setFeat] = useState<string | null>('rel_power')
  const [onlySep, setOnlySep] = useState(false)
  const ready = !!base && !!target && base !== target
  const cmp = useQuery({
    queryKey: ['cmp', pid, base, target, level], enabled: ready, retry: false,
    queryFn: () => api<Comparison>(`/patients/${pid}/comparison?base=${base}&target=${target}&level=${level}`),
  })
  const trendFeat = feat ?? 'rel_power'
  const trend = useQuery({
    queryKey: ['trend-f', pid, trendFeat, level], enabled: (cand.data?.runs.length ?? 0) > 0,
    queryFn: () => api<FeatureTrend>(`/patients/${pid}/feature-trend?feature=${trendFeat}&level=global&name=global${trendFeat === 'rel_power' || trendFeat === 'abs_power' ? '&band=alpha' : ''}`),
  })
  const features = useMemo(() => [...new Set(cmp.data?.items.map((i) => i.feature) ?? [])], [cmp.data])
  const shown = useMemo(() => (cmp.data?.items ?? []).filter((i) => (!feat || i.feature === feat) && (!onlySep || i.interval_separated))
    .sort((a, b) => Math.abs(b.relative ?? 0) - Math.abs(a.relative ?? 0)), [cmp.data, feat, onlySep])
  const err = cmp.error instanceof ApiError ? cmp.error : null
  const reasons = err && typeof err.detail === 'object' && err.detail && 'reasons' in err.detail ? (err.detail as { reasons: string[] }).reasons : []
  const opts = (cand.data?.runs ?? []).map((r) => ({ value: r.run_id, label: runLabel(r) }))

  return (
    <>
      <p style={{ margin: 0 }}><Link to={`/patients/${pid}`}>← Patient</Link></p>
      <h1 style={{ marginBottom: 4 }}>Longitudinal comparison</h1>
      <p className="muted" style={{ marginTop: 0 }}>Compares EEG features between two recordings of this patient. {cand.data?.note}</p>
      <div className="notice warn">This view describes <strong>change between recordings</strong>. It does not say that the patient is improving or recovering: differences in condition, sedation, quality or time of day can change EEG without any change in the brain.</div>
      {cand.data && cand.data.runs.length < 2 && <div className="notice error">At least two QC-approved recordings with computed features are needed. Approve the processing run of each recording and compute its features first.</div>}

      <div className="card" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 160px', gap: 12, marginBottom: 12 }}>
        <Select label="Base (earlier)" data={opts} value={base} onChange={setBase} allowDeselect={false} searchable />
        <Select label="Target (later)" data={opts} value={target} onChange={setTarget} allowDeselect={false} searchable />
        <Select label="Level" data={['global', 'region', 'channel', 'pair']} value={level} onChange={setLevel} allowDeselect={false} />
      </div>
      {base === target && <div className="notice error">Choose two different recordings.</div>}
      {err && <div className="notice error" role="alert">{err.message}{reasons.length > 0 && <ul style={{ margin: '6px 0 0' }}>{reasons.map((r) => <li key={r}>{r}</li>)}</ul>}</div>}

      {cmp.data && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
            <Side r={cmp.data.base} title="Base" /><Side r={cmp.data.target} title="Target" />
          </div>
          <p className="muted">{cmp.data.days_between != null && `${cmp.data.days_between} days apart. `}
            {cmp.data.assessments.base && `CRS-R near base: ${cmp.data.assessments.base.total}/23 (${cmp.data.assessments.base.hours_from_recording} h). `}
            {cmp.data.assessments.target && `CRS-R near target: ${cmp.data.assessments.target.total}/23 (${cmp.data.assessments.target.hours_from_recording} h).`}</p>
          {cmp.data.warnings.length > 0 ? (
            <div className="notice warn"><strong>The two recordings differ in context:</strong><ul style={{ margin: '6px 0 0' }}>{cmp.data.warnings.map((w) => <li key={w.key}>{w.message}</li>)}</ul></div>
          ) : <div className="notice" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}>Recording context matches (condition, state, sedation, quality, channels).</div>}

          <div style={{ display: 'flex', gap: 16, alignItems: 'end', flexWrap: 'wrap', margin: '8px 0' }}>
            <Select label="Feature" data={features.map((f) => ({ value: f, label: label(f) }))} value={feat} onChange={setFeat} searchable style={{ minWidth: 260 }} clearable />
            <label style={{ fontSize: 13 }}><input type="checkbox" checked={onlySep} onChange={(e) => setOnlySep(e.currentTarget.checked)} /> only interval-separated ({cmp.data.n_separated} of {cmp.data.n_features})</label>
          </div>
          {feat && shown.length > 0 && shown.length <= 40 && (
            <Suspense fallback={<p className="muted">Loading chart…</p>}><Charts.Paired items={[...shown].sort((a, b) => itemLabel(a).localeCompare(itemLabel(b)))} labelOf={itemLabel} /></Suspense>
          )}
          <div className="card" style={{ padding: 0, overflow: 'hidden', maxHeight: 460, overflowY: 'auto' }}>
            <table className="data">
              <thead><tr><th>Feature</th><th>Where</th><th>Base (95 % CI)</th><th>Target (95 % CI)</th><th>Change</th><th>%</th><th>Intervals</th></tr></thead>
              <tbody>
                {shown.slice(0, 300).map((i) => (
                  <tr key={`${i.feature}${i.band}${i.name}`}>
                    <td>{label(i.feature)}{i.band ? ` · ${i.band}` : ''}</td><td>{i.name}</td>
                    <td className="mono">{num(i.base)} <span className="muted">({num(i.base_ci[0])}–{num(i.base_ci[1])})</span></td>
                    <td className="mono">{num(i.target)} <span className="muted">({num(i.target_ci[0])}–{num(i.target_ci[1])})</span></td>
                    <td className="mono">{i.delta > 0 ? '+' : ''}{num(i.delta)}</td>
                    <td className="mono">{i.relative == null ? '—' : `${(100 * i.relative).toFixed(0)} %`}</td>
                    <td>{!i.has_ci ? <span className="muted">no interval</span> : i.interval_separated ? <span className="pill bad">separated</span> : <span className="pill ok">overlap</span>}</td>
                  </tr>
                ))}
                {shown.length === 0 && <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 20 }}>No rows.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 12 }}>{cmp.data.note}</p>
        </>
      )}

      {trend.data && trend.data.points.length > 0 && (
        <>
          <h2 style={{ marginTop: 28 }}>Over time: {label(trend.data.feature)}{trend.data.band ? ` (${trend.data.band})` : ''}, whole head</h2>
          {trend.data.mixed_context && <div className="notice warn">{trend.data.note}</div>}
          <Suspense fallback={<p className="muted">Loading chart…</p>}><Charts.Trend t={trend.data} /></Suspense>
          <p className="muted" style={{ fontSize: 12 }}>Vertical bars: 95 % block-bootstrap intervals. Diamonds: recordings made under a different condition than the first. Dashed line: signed CRS-R totals.</p>
        </>
      )}
    </>
  )
}
