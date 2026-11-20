import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

type Row = {
  id: number; occurred_at: string; user_id: string | null; action: string
  entity_type: string | null; patient_id: string | null; ip_address: string | null
}
type Verify = { ok: boolean; checked: number; first_bad_id: number | null; reason: string | null }

export default function AuditLog() {
  const rows = useQuery({ queryKey: ['audit'], queryFn: () => api<Row[]>('/audit?limit=200') })
  const verify = useQuery({ queryKey: ['audit-verify'], queryFn: () => api<Verify>('/audit/verify') })

  return (
    <>
      <h1>Audit log</h1>
      <p className="muted" style={{ marginTop: 0 }}>Append-only record of access and changes.</p>

      {verify.data && (
        <div className={`notice ${verify.data.ok ? '' : 'error'}`}
          style={verify.data.ok ? { background: 'var(--accent-soft)', color: 'var(--accent)' } : undefined}>
          {verify.data.ok
            ? `Integrity check passed — ${verify.data.checked} entries verified.`
            : `Integrity check FAILED at entry ${verify.data.first_bad_id}: ${verify.data.reason}`}
        </div>
      )}

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>#</th><th>Time (UTC)</th><th>Action</th><th>Entity</th><th>Patient</th><th>IP</th></tr></thead>
          <tbody>
            {rows.data?.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.id}</td>
                <td>{new Date(r.occurred_at).toISOString().replace('T', ' ').slice(0, 19)}</td>
                <td className="mono">{r.action}</td>
                <td>{r.entity_type ?? '—'}</td>
                <td className="mono">{r.patient_id?.slice(0, 8) ?? '—'}</td>
                <td className="mono">{r.ip_address ?? '—'}</td>
              </tr>
            ))}
            {rows.isLoading && <tr><td colSpan={6} className="muted">Loading…</td></tr>}
            {rows.isError && <tr><td colSpan={6} className="muted">Could not load the audit log.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}
