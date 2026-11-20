import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { AssessmentOverview } from '../api/types'

export default function AssessmentsOverview() {
  const { data, error, isLoading } = useQuery({ queryKey: ['overview-assessments'], queryFn: () => api<AssessmentOverview>('/overview/assessments') })
  return (
    <>
      <h1>Assessments</h1>
      <p className="muted" style={{ marginTop: 0 }}>CRS-R status of your active patients. Patients with fewer than {data?.required ?? 5} signed assessments in the last {data?.window_days ?? 10} days are listed first.</p>
      {data?.due_count ? <div className="notice warn">{data.due_count} patient(s) may be due for a repeat assessment. <span style={{ fontSize: 12 }}>{data.basis}</span></div> : null}
      {error && <div className="notice error">{(error as Error).message}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>Patient</th><th>Latest CRS-R</th><th>Category (clinician)</th><th>Last assessed</th><th>In last {data?.window_days ?? 10} days</th><th>Repeat</th><th>My drafts</th></tr></thead>
          <tbody>
            {data?.items.map((r) => (
              <tr key={r.patient_id}>
                <td><Link to={`/patients/${r.patient_id}`}>{r.display_id}</Link></td>
                <td className="mono">{r.last ? <Link to={`/assessments/${r.last.id}`}>{r.last.total}/23</Link> : '—'}</td>
                <td>{r.last?.category ?? '—'}</td>
                <td>{r.last ? new Date(r.last.assessed_at).toISOString().slice(0, 10) : 'never'}</td>
                <td>{r.count_in_window} / {data.required}</td>
                <td>{r.due ? <span className="pill bad">suggested</span> : <span className="pill ok">ok</span>}</td>
                <td>{r.my_drafts || '—'}</td>
              </tr>
            ))}
            {isLoading && <tr><td colSpan={7} className="muted">Loading…</td></tr>}
            {data?.items.length === 0 && <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 28 }}>No active patients on your care team.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}
