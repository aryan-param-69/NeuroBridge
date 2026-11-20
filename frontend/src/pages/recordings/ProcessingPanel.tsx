import { Button, Progress, Select, Textarea } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, post } from '../../api/client'
import type { JobInfo, RunDetail, RunSummary } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const SUB_LABEL: Record<string, string> = {
  bad_channels: 'Bad channels', line_noise: 'Mains interference', emg: 'Muscle activity', drift: 'Slow drift',
  usable: 'Usable duration', rejected: 'Epoch rejection',
}
const SEGMENT_LABEL: Record<string, string> = { high_amplitude: 'artifact_movement', muscle: 'artifact_muscle', flat: 'flat' }
const fmt = (d: string) => new Date(d).toISOString().slice(0, 16).replace('T', ' ')
const gradeClass = (g?: string | null) => (g === 'good' ? 'ok' : g === 'poor' ? 'bad' : 'role')

function ScoreBar({ label, value, weight }: { label: string; value: number | null; weight?: number }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '170px 1fr 56px', alignItems: 'center', gap: 10, fontSize: 13 }}>
      <span>{label}{weight != null && <span className="muted"> · {Math.round(weight * 100)}%</span>}</span>
      <div style={{ background: 'var(--surface)', borderRadius: 6, height: 8, border: '1px solid var(--border)' }}>
        {value != null && <div style={{ width: `${Math.round(value * 100)}%`, height: '100%', borderRadius: 6, background: value > 0.75 ? 'var(--accent)' : value > 0.5 ? '#D9A441' : 'var(--danger)' }} />}
      </div>
      <span className="muted" style={{ textAlign: 'right' }}>{value == null ? 'n/a' : `${Math.round(value * 100)}`}</span>
    </div>
  )
}

export default function ProcessingPanel({ recordingId, selectedRunId, onSelectRun }: {
  recordingId: string; selectedRunId: string | null; onSelectRun: (id: string | null) => void
}) {
  const qc = useQueryClient()
  const { hasRole } = useAuth()
  const canWrite = hasRole('clinician', 'eeg_technologist')
  const navigate = useNavigate()
  const [line, setLine] = useState<string | null>('50')
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState('')

  const runs = useQuery({ queryKey: ['runs', recordingId], queryFn: () => api<RunSummary[]>(`/recordings/${recordingId}/runs`) })
  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: () => api<{ line_freq_hz: number; pipelines: { name: string; version: string; description: string }[] }>('/pipelines') })
  const job = useQuery({
    queryKey: ['job', jobId], enabled: !!jobId,
    queryFn: () => api<JobInfo>(`/jobs/${jobId}`),
    refetchInterval: (q) => (q.state.data && ['succeeded', 'failed'].includes(q.state.data.status) ? false : 1000),
  })
  const detailId = selectedRunId ?? runs.data?.find((r) => r.status === 'succeeded')?.id ?? null
  const detail = useQuery({ queryKey: ['run', detailId], enabled: !!detailId, queryFn: () => api<RunDetail>(`/runs/${detailId}`) })

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ['runs', recordingId] })
    await qc.invalidateQueries({ queryKey: ['run'] })
    await qc.invalidateQueries({ queryKey: ['recording', recordingId] })
    await qc.invalidateQueries({ queryKey: ['signal', recordingId] })
  }
  const start = async () => {
    setError(null)
    try {
      const r = await post<{ job_id: string; run_id: string }>(`/recordings/${recordingId}/runs`, { overrides: { line_freq_hz: Number(line) } })
      setJobId(r.job_id); onSelectRun(r.run_id)
      await refresh()
    } catch (e) { setError((e as Error).message) }
  }
  const jobStatus = job.data?.status
  useEffect(() => {
    if (jobId && (jobStatus === 'succeeded' || jobStatus === 'failed')) { setJobId(null); void refresh() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobStatus, jobId])
  const decide = async (decision: 'approve' | 'reject') => {
    if (!detail.data) return
    setError(null)
    try { await post(`/runs/${detail.data.id}/qc`, { decision, note }); setNote(''); await refresh() }
    catch (e) { setError((e as Error).message) }
  }
  const makeCurrent = async () => { if (detail.data) { try { await post(`/runs/${detail.data.id}/current`); await refresh() } catch (e) { setError((e as Error).message) } } }
  const markSegment = async (onset: number, duration: number, label: string) => {
    try {
      await post(`/recordings/${recordingId}/annotations`, { onset_s: onset, duration_s: duration, label: SEGMENT_LABEL[label] ?? 'excluded', source: 'auto_accepted' })
      await qc.invalidateQueries({ queryKey: ['annotations', recordingId] })
    } catch (e) { setError((e as Error).message) }
  }

  const d = detail.data
  const q = d?.quality
  const es = d?.epochs?.summary
  const active = job.data && !['succeeded', 'failed'].includes(job.data.status)

  return (
    <div className="stack">
      {error && <div className="notice error" role="alert">{error}</div>}

      {canWrite && (
        <div className="card" style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 260px' }}>
            <div style={{ fontWeight: 600 }}>Pre-processing pipeline {pipelines.data?.pipelines[0] && `${pipelines.data.pipelines[0].name} v${pipelines.data.pipelines[0].version}`}</div>
            <div className="muted" style={{ fontSize: 13 }}>Quality check · notch + band-pass 0.5–45 Hz · bad-channel handling · average reference · 2 s epochs. Every run is saved with its parameters.</div>
          </div>
          <Select label="Mains frequency" w={130} allowDeselect={false} value={line} onChange={setLine} data={[{ value: '50', label: '50 Hz' }, { value: '60', label: '60 Hz' }]} />
          <Button onClick={() => void start()} loading={!!active}>Run pipeline</Button>
          {active && job.data && <div style={{ flex: '1 1 100%' }}><div className="muted" style={{ fontSize: 13, marginBottom: 4 }}>{job.data.message}</div><Progress value={job.data.progress} aria-label="Processing progress" /></div>}
        </div>
      )}

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>Run</th><th>Quality</th><th>Epochs kept</th><th>Channels</th><th>QC</th><th /></tr></thead>
          <tbody>
            {runs.data?.map((r) => (
              <tr key={r.id} style={{ background: r.id === detailId ? 'var(--primary-soft)' : undefined }}>
                <td>{fmt(r.created_at)} <span className="mono muted">{r.pipeline_name} v{r.pipeline_version}</span> {r.is_current && <span title="Feeds trends and comparisons">★</span>}</td>
                <td>{r.status === 'succeeded' ? <><span className={`pill ${gradeClass(r.grade)}`}>{r.grade}</span> <span className="muted">{r.score?.toFixed(0)}</span></> : <span className={`pill ${r.status === 'failed' ? 'bad' : 'role'}`}>{r.status}</span>}</td>
                <td>{r.n_epochs_kept != null ? `${r.n_epochs_kept} / ${r.n_epochs_total}` : '—'}{r.low_confidence && <span className="pill bad" style={{ marginLeft: 6 }}>low confidence</span>}</td>
                <td>{r.status === 'succeeded' ? `${r.interpolated_channels.length} interpolated · ${r.dropped_channels.length} dropped` : (r.error ?? '—')}</td>
                <td><span className={`pill ${r.qc_status === 'approved' ? 'ok' : r.qc_status === 'rejected' ? 'bad' : 'role'}`}>{r.qc_status === 'pending' ? 'unreviewed' : r.qc_status}</span></td>
                <td style={{ textAlign: 'right' }}><Button size="compact-xs" variant="default" disabled={r.status !== 'succeeded'} onClick={() => onSelectRun(r.id)}>Review</Button></td>
              </tr>
            ))}
            {runs.data?.length === 0 && <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 24 }}>No processing runs yet. Run the pipeline to get a quality report and cleaned data.</td></tr>}
          </tbody>
        </table>
      </div>

      {d && q && (
        <>
          <div className="card stack">
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
              <h2 style={{ margin: 0 }}>Signal quality</h2>
              <span className={`pill ${gradeClass(q.grade)}`} style={{ fontSize: 14 }}>{q.grade}</span>
              <span style={{ fontSize: 22, fontWeight: 600 }}>{q.overall_score.toFixed(0)}<span className="muted" style={{ fontSize: 13 }}> / 100</span></span>
              <span className="muted">{q.usable_duration_s.toFixed(0)} s usable</span>
            </div>
            {q.grade_cap && <div className="notice warn" style={{ margin: 0 }}>The grade is capped at <b>{q.grade_cap}</b> because many channels are bad, however clean the others are.</div>}
            <div className="stack" style={{ gap: 8 }}>
              {Object.entries(q.sub_scores).map(([k, v]) => <ScoreBar key={k} label={SUB_LABEL[k] ?? k} value={v} weight={q.weights[k]} />)}
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              {q.calibration_note} Correlation reference: {q.correlation_reference}. {q.sampled_all ? '' : `Assessed on ${q.sampled_seconds.toFixed(0)} s sampled evenly from ${q.recording_seconds.toFixed(0)} s. `}
            </div>
            {d.warnings.length > 0 && <div className="notice warn" style={{ margin: 0 }}><ul style={{ margin: 0, paddingLeft: 18 }}>{d.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>}
          </div>

          {es && (
            <div className="card stack">
              <h2 style={{ margin: 0 }}>Epochs ({d.n_epochs_total} × 2 s, 50 % overlap)</h2>
              <div style={{ display: 'flex', height: 14, borderRadius: 7, overflow: 'hidden', border: '1px solid var(--border)' }} role="img"
                aria-label={`${es.kept} kept, ${es.rejected_ptp} rejected for amplitude, ${es.rejected_annotation} by annotation, ${es.outside_selection} outside selection`}>
                <div style={{ flex: es.kept, background: 'var(--accent)' }} />
                <div style={{ flex: es.rejected_ptp, background: 'var(--danger)' }} />
                <div style={{ flex: es.rejected_annotation, background: '#D9A441' }} />
                <div style={{ flex: es.outside_selection, background: '#B6C2D0' }} />
              </div>
              <div className="muted" style={{ fontSize: 13 }}>
                {es.kept} kept · {es.rejected_ptp} rejected (many channels above the amplitude limit) · {es.rejected_annotation} excluded by your annotations · {es.outside_selection} outside the selected segments.
                {' '}Within kept epochs {(100 * es.masked_fraction_of_kept).toFixed(1)} % of channel values are masked{es.most_masked_channels.length > 0 && <> (mostly {es.most_masked_channels.slice(0, 3).join(', ')}, typically eye blinks)</>}; later feature extraction skips them.
              </div>
            </div>
          )}

          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '16px 24px 0' }}><h2>Channels needing attention</h2></div>
            <table className="data">
              <thead><tr><th>Channel</th><th>Flags</th><th>SD (µV)</th><th>Mains (dB)</th><th>Muscle ratio</th><th>Handled as</th></tr></thead>
              <tbody>
                {q.channels.filter((c) => c.flags.length > 0).map((c) => (
                  <tr key={c.name}>
                    <td style={{ fontWeight: 500 }}>{c.name}</td>
                    <td>{c.flags.map((f) => <span key={f} className={`pill ${c.bad && ['flat', 'clipped', 'noisy', 'uncorrelated'].includes(f) ? 'bad' : 'role'}`} style={{ marginRight: 4 }}>{f.replace('_', ' ')}</span>)}</td>
                    <td>{c.std_uv?.toFixed(1) ?? '—'}</td><td>{c.line_noise_db?.toFixed(1) ?? '—'}</td><td>{c.emg_ratio?.toFixed(2) ?? '—'}</td>
                    <td>{d.interpolated_channels.includes(c.name) ? 'interpolated' : d.dropped_channels.includes(c.name) ? 'removed' : c.bad ? 'kept (user override)' : 'warning only'}</td>
                  </tr>
                ))}
                {q.channels.every((c) => c.flags.length === 0) && <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 20 }}>No channel problems detected.</td></tr>}
              </tbody>
            </table>
          </div>

          {d.segments_proposed.length > 0 && canWrite && (
            <div className="card">
              <h2>Proposed artefact segments</h2>
              <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>Found automatically. Nothing is excluded until you accept a segment; then re-run the pipeline.</p>
              <div style={{ maxHeight: 220, overflowY: 'auto' }}>
                {d.segments_proposed.slice(0, 40).map((s, i) => (
                  <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
                    <span className="mono" style={{ width: 120 }}>{s.onset_s.toFixed(0)}–{(s.onset_s + s.duration_s).toFixed(0)} s</span>
                    <span className="pill role">{s.label.replace('_', ' ')}</span>
                    <Button size="compact-xs" variant="default" style={{ marginLeft: 'auto' }} onClick={() => void markSegment(s.onset_s, s.duration_s, s.label)}>Exclude this segment</Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="card stack">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <h2 style={{ margin: 0 }}>Review and sign-off</h2>
              <Button variant="light" onClick={() => navigate(`/runs/${d.id}/analysis`)}>Open EEG analysis →</Button>
            </div>
            {d.qc_status !== 'pending' && <div className="notice" style={{ margin: 0, background: d.qc_status === 'approved' ? 'var(--accent-soft)' : 'var(--danger-soft)' }}>
              This run is <b>{d.qc_status}</b>{d.qc_note ? `: ${d.qc_note}` : ''}.{d.qc_status === 'approved' && (d.is_current ? ' It feeds trends and comparisons.' : ' It is approved but another run is current.')}
            </div>}
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>Only approved runs feed trends and comparisons. Unreviewed data are shown with a watermark in the viewer.</p>
            {canWrite && d.status === 'succeeded' && (
              <>
                <Textarea label={q.grade === 'poor' ? 'Reason (required: this run has poor quality)' : 'Note (required when rejecting)'} autosize minRows={2} value={note} onChange={(e) => setNote(e.currentTarget.value)} />
                <div style={{ display: 'flex', gap: 8 }}>
                  <Button color="teal" onClick={() => void decide('approve')}>Approve</Button>
                  <Button color="red" variant="light" onClick={() => void decide('reject')}>Reject</Button>
                  {hasRole('clinician') && d.qc_status === 'approved' && !d.is_current && <Button variant="default" onClick={() => void makeCurrent()}>Make current</Button>}
                </div>
              </>
            )}
          </div>

          <details className="card">
            <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Provenance — how these numbers were computed</summary>
            <div className="stack" style={{ marginTop: 12 }}>
              <div className="mono" style={{ wordBreak: 'break-all' }}>pipeline {d.pipeline_name} v{d.pipeline_version}<br />params {d.params_hash}<br />input {d.input_sha256}<br />output {d.output_sha256}</div>
              <div className="muted" style={{ fontSize: 13 }}>{Object.entries(d.software_versions).map(([k, v]) => `${k} ${v}`).join(' · ')}</div>
              <table className="data">
                <thead><tr><th>Step</th><th>Seconds</th><th>Parameters</th><th>Result</th></tr></thead>
                <tbody>{d.steps.map((s) => (
                  <tr key={s.name}><td style={{ fontWeight: 500 }}>{s.name.replace(/_/g, ' ')}</td><td>{s.seconds}</td>
                    <td className="mono" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>{JSON.stringify(s.params)}</td>
                    <td className="mono" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis' }}>{JSON.stringify(s.summary)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  )
}
