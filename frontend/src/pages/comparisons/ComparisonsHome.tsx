import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

type PatientRow = { id: string; display_id: string; status: string; days_since_injury?: number | null }
type Paged = { items?: PatientRow[]; results?: PatientRow[] } | PatientRow[]

export default function ComparisonsHome() {
  const { data, error } = useQuery({ queryKey: ['patients-for-compare'], queryFn: () => api<Paged>('/patients?page_size=100&status=active') })
  const rows: PatientRow[] = Array.isArray(data) ? data : (data?.items ?? data?.results ?? [])
  return (
    <>
      <h1>Comparisons</h1>
      <p className="muted" style={{ marginTop: 0 }}>Compare a patient's EEG between recordings and follow features over time next to the CRS-R. Pick a patient.</p>
      {error && <div className="notice error">{(error as Error).message}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>Patient</th><th>Status</th><th /></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}><td><Link to={`/patients/${p.id}`}>{p.display_id}</Link></td><td>{p.status}</td>
                <td><Link to={`/patients/${p.id}/compare`}>Open comparison →</Link></td></tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={3} className="muted" style={{ textAlign: 'center', padding: 28 }}>No active patients.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}
