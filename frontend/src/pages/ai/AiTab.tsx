import { Button } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, post } from '../../api/client'
import type { AiPatient, AiPrediction, JobInfo, RunSummary } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const AiTrend = lazy(() => import('./AiTrend'))
const fmt = (d?: string | null) => (d ? new Date(d).toISOString().slice(0, 16).replace('T', ' ') : '—')
const COLOR = { 'awake-like': 'var(--accent)', 'sedated-like': 'var(--warning)', uncertain: 'var(--text-muted)' }

export function Verdict({ p }: { p: AiPrediction }) {
  const pct = Math.round(100 * p.p_sedated)
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div className="muted" style={{ fontSize: 12 }}>Resembles (model {p.model.version}, {p.model.status}) · {p.recording_code ?? ''}</div>
          <div style={{ fontSize: 22, fontWeight: 600, color: COLOR[p.verdict] }}>{p.verdict}</div>
          <div className="muted" style={{ fontSize: 12 }}>Answer set at {Math.round(100 * p.coverage_target)} % coverage: {p.prediction_set.length ? p.prediction_set.join(' / ') : 'neither class fits'}</div>
        </div>
        <div style={{ minWidth: 260 }}>
          <div style={{ fontSize: 12 }} className="muted">P(sedated-like) = {pct} % · {p.n_windows_used} windows of 10 s{p.epoch_spread_sd != null && ` · spread SD ${p.epoch_spread_sd.toFixed(2)}`}</div>
          <div style={{ height: 12, background: 'var(--border)', borderRadius: 6, overflow: 'hidden', marginTop: 4 }} role="img" aria-label={`Probability sedated-like ${pct} percent`}>
            <div style={{ width: `${pct}%`, height: '100%', background: 'var(--primary)' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }} className="muted"><span>awake-like</span><span>sedated-like</span></div>
        </div>
      </div>
      {p.reliability.length > 0 && <div className="notice warn" style={{ marginTop: 10, marginBottom: 0 }}><strong>Be careful with this result</strong><ul style={{ margin: '4px 0 0' }}>{p.reliability.map((w) => <li key={w}>{w}</li>)}</ul></div>}
      {p.explanation.length > 0 && (
        <>
          <div style={{ marginTop: 10, fontWeight: 600, fontSize: 13 }}>What pushed the answer (SHAP, this recording)</div>
          <table className="data" style={{ marginTop: 4 }}>
            <tbody>{p.explanation.map((e) => <tr key={e.feature}><td>{e.plain}</td><td style={{ width: 150, color: e.shap > 0 ? 'var(--warning)' : 'var(--accent)' }}>→ {e.pushes_towards}</td><td className="mono" style={{ width: 70 }}>{e.shap > 0 ? '+' : ''}{e.shap.toFixed(2)}</td></tr>)}</tbody>
          </table>
        </>
      )}
      <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>{p.disclaimer} Model file SHA-256 {p.model.sha256.slice(0, 12)}…</p>
    </div>
  )
}

export default function AiTab({ patientId }: { patientId: string }) {
  const { hasRole } = useAuth()
  const qc = useQueryClient()
  const key = ['ai-patient', patientId]
  const preds = useQuery({ queryKey: key, queryFn: () => api<AiPatient>(`/patients/${patientId}/ai/sedation`) })
  const runs = useQuery({ queryKey: ['analysis-runs', patientId], queryFn: () => api<RunSummary[]>(`/patients/${patientId}/analysis-runs`) })
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const byRun = new Map(preds.data?.items.map((p) => [p.run_id, p]))

  const run = async (runId: string) => {
    setErr(null); setBusy(runId)
    try {
      const j = await post<{ job_id: string }>(`/runs/${runId}/ai/sedation`)
      for (let i = 0; i < 120; i++) {
        const job = await api<JobInfo>(`/jobs/${j.job_id}`)
        if (job.status === 'failed') throw new Error(job.error ?? 'The model run failed')
        if (job.status === 'succeeded') break
        await new Promise((r) => setTimeout(r, 1000))
      }
      await qc.invalidateQueries({ queryKey: key })
    } catch (x) { setErr((x as Error).message) } finally { setBusy(null) }
  }

  return (
    <>
      <div className="notice warn"><strong>Research decision support, not a diagnosis.</strong> The model estimates how much a recording <em>resembles</em> awake or moderately propofol-sedated EEG of 40 healthy volunteers from two laboratories. It has never been validated on patients and does not measure consciousness or recovery. Results from other EEG systems are usually flagged as unfamiliar.</div>
      {err && <div className="notice error" role="alert">{err}</div>}
      <h3>Recordings available for analysis</h3>
      <p className="muted" style={{ marginTop: 0 }}>Only QC-approved, current processing runs are listed.</p>
      <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: 16 }}>
        <table className="data">
          <thead><tr><th>Run</th><th>Pipeline</th><th>Quality</th><th>Latest model output</th><th /></tr></thead>
          <tbody>
            {runs.data?.map((r) => {
              const p = byRun.get(r.id)
              return (
                <tr key={r.id}>
                  <td className="mono"><Link to={`/recordings/${r.recording_id}`}>{r.id.slice(0, 8)}</Link></td><td>{r.pipeline_name} v{r.pipeline_version}</td><td>{r.grade ?? '—'}</td>
                  <td>{p ? <>{p.verdict} <span className="muted">({Math.round(100 * p.p_sedated)} %, {fmt(p.created_at)})</span></> : <span className="muted">not run</span>}</td>
                  <td>{hasRole('clinician') && <Button size="compact-sm" leftSection={<Sparkles size={14} />} loading={busy === r.id} onClick={() => void run(r.id)}>{p ? 'Run again' : 'Run model'}</Button>}</td>
                </tr>
              )
            })}
            {runs.data?.length === 0 && <tr><td colSpan={5} className="muted" style={{ textAlign: 'center', padding: 24 }}>No approved processing runs yet. Process a recording and approve its QC first.</td></tr>}
          </tbody>
        </table>
      </div>
      {preds.data && preds.data.items.length > 0 && (
        <>
          <h3>Over time, next to the CRS-R</h3>
          <p className="muted" style={{ marginTop: 0 }}>{preds.data.note}</p>
          <Suspense fallback={<p className="muted">Loading chart…</p>}><AiTrend data={preds.data} /></Suspense>
          <h3>Latest results</h3>
          {[...preds.data.items].reverse().map((p) => <Verdict key={p.id} p={p} />)}
        </>
      )}
    </>
  )
}
