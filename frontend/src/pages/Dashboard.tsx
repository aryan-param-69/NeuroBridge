import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'

type Summary = {
  counts: { patients: number; active_patients: number; recordings: number; qc_pending: number; reports_signed: number; reports_draft: number; ai_runs: number; multi_recording_patients: number }
  quality: Record<string, number>
  qc: Record<string, number>
  ai_verdicts: Record<string, number>
  ai_unfamiliar: number
  recent_recordings: { id: string; recording_code: string; patient_id: string; display_id: string; start_time: string | null; duration_s: number; quality_grade: string | null; qc_status: string; state: string | null }[]
  recent_reports: { id: string; patient_id: string; display_id: string; title: string; status: string; created_at: string }[]
  patients: { id: string; display_id: string; n_recordings: number; n_reports: number; last: string | null }[]
}

const CLINICAL = ['clinician', 'eeg_technologist', 'viewer']
const fmtDur = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`
const day = (v: string | null) => (v ? new Date(v).toISOString().slice(0, 16).replace('T', ' ') : '—')

function Tile({ label, value, hint, to }: { label: string; value: number | string; hint?: string; to?: string }) {
  const body = (
    <div className="card" style={{ padding: '14px 16px', height: '100%' }}>
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 600, lineHeight: 1.2 }}>{value}</div>
      {hint && <div className="muted" style={{ fontSize: 12 }}>{hint}</div>}
    </div>
  )
  return to ? <Link to={to} style={{ textDecoration: 'none', color: 'inherit' }}>{body}</Link> : body
}

function Bar({ parts }: { parts: { label: string; n: number; color: string }[] }) {
  const total = parts.reduce((a, p) => a + p.n, 0)
  if (!total) return <p className="muted" style={{ margin: 0 }}>Nothing to show yet.</p>
  return (
    <>
      <div style={{ display: 'flex', height: 14, borderRadius: 7, overflow: 'hidden', background: '#E3E8EF' }} role="img"
        aria-label={parts.map((p) => `${p.label} ${p.n}`).join(', ')}>
        {parts.filter((p) => p.n).map((p) => <div key={p.label} style={{ width: `${(100 * p.n) / total}%`, background: p.color }} title={`${p.label}: ${p.n}`} />)}
      </div>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8, fontSize: 13 }}>
        {parts.map((p) => <span key={p.label}><span style={{ display: 'inline-block', width: 9, height: 9, borderRadius: 2, background: p.color, marginRight: 5 }} />{p.label} {p.n}</span>)}
      </div>
    </>
  )
}

function Clinical() {
  const { data, error, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: () => api<Summary>('/overview/dashboard') })
  if (error) return <div className="notice error">{(error as Error).message}</div>
  if (isLoading || !data) return <p className="muted">Loading…</p>
  const c = data.counts
  const v = data.ai_verdicts
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 14, marginTop: 20 }}>
        <Tile label="Patients" value={c.patients} hint={`${c.active_patients} active · ${c.multi_recording_patients} with repeat recordings`} to="/patients" />
        <Tile label="EEG recordings" value={c.recordings} hint={`${data.qc.approved} approved by QC`} to="/recordings" />
        <Tile label="Awaiting QC review" value={c.qc_pending} hint={c.qc_pending ? 'open Recordings to review' : 'nothing waiting'} to="/recordings" />
        <Tile label="Signed reports" value={c.reports_signed} hint={`${c.reports_draft} draft`} to="/reports" />
        <Tile label="Model analyses" value={c.ai_runs} hint="research decision support" to="/research" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 14, marginTop: 14 }}>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Signal quality</h2>
          <Bar parts={[{ label: 'good', n: data.quality.good, color: '#2E9E6B' }, { label: 'fair', n: data.quality.fair, color: '#E0A030' },
            { label: 'poor', n: data.quality.poor, color: '#D2504B' }, { label: 'not processed', n: data.quality.not_run, color: '#9AA7B5' }]} />
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Sedation-model outputs</h2>
          <Bar parts={[{ label: 'awake-like', n: v['awake-like'] ?? 0, color: '#2E9E6B' }, { label: 'sedated-like', n: v['sedated-like'] ?? 0, color: '#4C6FB5' },
            { label: 'uncertain', n: v.uncertain ?? 0, color: '#9AA7B5' }]} />
          <p className="muted" style={{ marginBottom: 0, fontSize: 12, marginTop: 10 }}>
            {data.ai_unfamiliar} of {c.ai_runs} recordings were flagged as unlike the EEG the model was trained on. These outputs describe recordings, not diagnoses.
          </p>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 14, marginTop: 14 }}>
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <h2 style={{ margin: 0, padding: '14px 16px 8px' }}>Recent recordings</h2>
          <table className="data">
            <thead><tr><th>Recording</th><th>Recorded</th><th>Length</th><th>State</th><th>Quality</th><th>QC</th></tr></thead>
            <tbody>
              {data.recent_recordings.map((r) => (
                <tr key={r.id}>
                  <td className="mono"><Link to={`/recordings/${r.id}`}>{r.recording_code}</Link></td><td>{day(r.start_time)}</td><td>{fmtDur(r.duration_s)}</td>
                  <td>{r.state ?? '—'}</td>
                  <td>{r.quality_grade ? <span className={`pill ${r.quality_grade === 'good' ? 'ok' : r.quality_grade === 'poor' ? 'bad' : 'role'}`}>{r.quality_grade}</span> : <span className="muted">not run</span>}</td>
                  <td><span className="pill role">{r.qc_status}</span></td>
                </tr>
              ))}
              {data.recent_recordings.length === 0 && <tr><td colSpan={6} className="muted" style={{ padding: 20 }}>No recordings yet. Open a patient and upload an EDF file.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <h2 style={{ margin: 0, padding: '14px 16px 8px' }}>Recent reports</h2>
          <table className="data">
            <thead><tr><th>Report</th><th>Patient</th><th>Status</th></tr></thead>
            <tbody>
              {data.recent_reports.map((r) => (
                <tr key={r.id}>
                  <td><Link to={`/reports/${r.id}`}>{r.title}</Link></td>
                  <td><Link to={`/patients/${r.patient_id}`}>{r.display_id}</Link></td>
                  <td><span className={`pill ${r.status === 'signed' ? 'ok' : 'role'}`}>{r.status}</span></td>
                </tr>
              ))}
              {data.recent_reports.length === 0 && <tr><td colSpan={3} className="muted" style={{ padding: 20 }}>No reports yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginTop: 14, padding: 0, overflow: 'hidden' }}>
        <h2 style={{ margin: 0, padding: '14px 16px 8px' }}>Patients with the most recordings</h2>
        <table className="data">
          <thead><tr><th>Patient</th><th>Recordings</th><th>Reports</th><th>Latest recording</th></tr></thead>
          <tbody>
            {data.patients.map((p) => (
              <tr key={p.id}><td><Link to={`/patients/${p.id}`}>{p.display_id}</Link></td><td>{p.n_recordings}</td><td>{p.n_reports}</td><td>{day(p.last)}</td></tr>
            ))}
            {data.patients.length === 0 && <tr><td colSpan={4} className="muted" style={{ padding: 20 }}>No patients with recordings yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

export default function Dashboard() {
  const { user } = useAuth()
  const clinical = user?.roles.some((r) => CLINICAL.includes(r))
  return (
    <>
      <h1>Welcome, {user?.full_name}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        An overview of the patients on your care team. Every access to patient data is recorded in a tamper-evident audit log.
      </p>
      {clinical ? <Clinical /> : (
        <div className="card" style={{ maxWidth: 560, marginTop: 24 }}>
          <h2>Your access</h2>
          <p style={{ marginTop: 0 }}>{user?.roles.map((r) => <span key={r} className="pill role" style={{ marginRight: 8 }}>{r}</span>)}</p>
          <p className="muted" style={{ marginBottom: 0 }}>Patients, EEG recordings, quality checks and features are available from each patient profile.</p>
        </div>
      )}
    </>
  )
}
