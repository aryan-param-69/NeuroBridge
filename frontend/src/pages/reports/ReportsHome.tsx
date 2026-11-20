import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import type { ReportRow } from '../../api/types'

export default function ReportsHome() {
  const { data, error } = useQuery({ queryKey: ['reports', 'all'], queryFn: () => api<{ items: ReportRow[] }>('/overview/reports') })
  return (
    <>
      <h1>Reports</h1>
      <p className="muted" style={{ marginTop: 0 }}>Clinical summaries of your patients. Create a report from a patient's <strong>Reports</strong> tab. Signed reports are immutable; an amendment is a new version.</p>
      {error && <div className="notice error">{(error as Error).message}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>Report</th><th>Patient</th><th>Version</th><th>Status</th><th>Created</th><th>Signed</th></tr></thead>
          <tbody>
            {data?.items.map((r) => (
              <tr key={r.id}><td><Link to={`/reports/${r.id}`}>{r.title}</Link></td><td><Link to={`/patients/${r.patient_id}`}>{r.display_id}</Link></td><td>v{r.version}</td>
                <td><span className={`pill ${r.status === 'signed' ? 'ok' : 'role'}`}>{r.status}</span></td><td>{r.created_at.slice(0, 10)}</td><td>{r.signed_at?.slice(0, 10) ?? '—'}</td></tr>
            ))}
            {data?.items.length === 0 && <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 28 }}>No reports yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}
