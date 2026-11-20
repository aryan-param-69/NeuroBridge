import { Button } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Upload } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { CONDITIONS, type RecordingSummary } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
const fmtDur = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`

export default function RecordingsTab({ patientId }: { patientId: string }) {
  const { hasRole } = useAuth()
  const navigate = useNavigate()
  const { data, isLoading, error } = useQuery({
    queryKey: ['recordings', patientId], queryFn: () => api<RecordingSummary[]>(`/patients/${patientId}/recordings`),
  })

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <p className="muted" style={{ margin: 0 }}>EEG recordings (EDF / EDF+), newest first.</p>
        {hasRole('clinician', 'eeg_technologist') && (
          <Button leftSection={<Upload size={16} />} onClick={() => navigate(`/patients/${patientId}/recordings/upload`)}>Upload recording</Button>
        )}
      </div>
      {error && <div className="notice error">{(error as Error).message}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead>
            <tr><th>Recording</th><th>Date</th><th>Condition</th><th>State</th><th>Duration</th><th>Channels</th><th>Quality</th><th>Checks</th><th /></tr>
          </thead>
          <tbody>
            {data?.map((r) => (
              <tr key={r.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/recordings/${r.id}`)}>
                <td className="mono"><Link to={`/recordings/${r.id}`}>{r.recording_code}</Link> {r.is_baseline && <span title="Baseline">★</span>}</td>
                <td>{r.start_time ? new Date(r.start_time).toISOString().slice(0, 16).replace('T', ' ') : '—'}</td>
                <td>{r.condition ? CONDITIONS.find((c) => c.value === r.condition?.condition)?.label : <span style={{ color: 'var(--warning)' }}>Context missing</span>}</td>
                <td>{r.condition ? label(r.condition.patient_state) + (r.condition.sedation.length ? ' 💊' : '') : '—'}</td>
                <td>{fmtDur(r.duration_s)}</td>
                <td>{r.n_eeg_channels} @ {r.sfreq_hz} Hz</td>
                <td>{r.quality_grade ? <span className={`pill ${r.quality_grade === 'good' ? 'ok' : r.quality_grade === 'poor' ? 'bad' : 'role'}`}>{r.quality_grade}</span> : <span className="muted">not run</span>}</td>
                <td>
                  {r.bad_channels.length > 0 && <span className="pill bad" style={{ marginRight: 4 }}>{r.bad_channels.length} flagged</span>}
                  {r.identity_check === 'mismatch_confirmed' && <span className="pill bad">identity override</span>}
                  {r.bad_channels.length === 0 && r.identity_check !== 'mismatch_confirmed' && <span className="pill ok">ok</span>}
                </td>
                <td><span className="pill role">QC {r.qc_status}</span></td>
              </tr>
            ))}
            {isLoading && <tr><td colSpan={9} className="muted">Loading…</td></tr>}
            {data?.length === 0 && (
              <tr><td colSpan={9} className="muted" style={{ textAlign: 'center', padding: 28 }}>
                No recordings yet. Upload a baseline recording first; later recordings can then be compared with it.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}
