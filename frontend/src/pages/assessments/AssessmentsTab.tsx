import { Button, SegmentedControl } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { ClipboardPlus } from 'lucide-react'
import { lazy, Suspense, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import type { Assessment, CrsrDefinition, EegPairs, Reminder, ResearchAssessment, TrendResponse } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const TrendChart = lazy(() => import('./TrendChart'))
const fmt = (d: string) => new Date(d).toISOString().replace('T', ' ').slice(0, 16)

export default function AssessmentsTab({ patientId }: { patientId: string }) {
  const { hasRole } = useAuth()
  const navigate = useNavigate()
  const research = !hasRole('clinician', 'eeg_technologist', 'viewer')
  const [view, setView] = useState('list')
  const list = useQuery({ queryKey: ['assessments', patientId], queryFn: () => api<(Assessment | ResearchAssessment)[]>(`/patients/${patientId}/assessments`) })
  const reminder = useQuery({ queryKey: ['reminder', patientId], enabled: !research, queryFn: () => api<Reminder>(`/patients/${patientId}/assessments/reminder`) })
  const trend = useQuery({ queryKey: ['trend', patientId], enabled: !research, queryFn: () => api<TrendResponse>(`/patients/${patientId}/assessments/trend`) })
  const def = useQuery({ queryKey: ['crsr-def'], enabled: !research, queryFn: () => api<CrsrDefinition>('/assessments/crsr-definition'), staleTime: Infinity })
  const pairs = useQuery({ queryKey: ['pairs', patientId], enabled: !research && view === 'pairs', queryFn: () => api<EegPairs>(`/patients/${patientId}/eeg-assessment-pairs`) })

  return (
    <>
      {reminder.data?.due && (
        <div className="notice warn" role="status">
          <strong>Repeat assessment suggested.</strong> {reminder.data.message}
          <div style={{ fontSize: 12, marginTop: 4 }}>{reminder.data.basis}</div>
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, gap: 12 }}>
        <SegmentedControl value={view} onChange={setView} data={[
          { value: 'list', label: 'Assessments' }, ...(research ? [] : [{ value: 'trend', label: 'Trend' }, { value: 'pairs', label: 'EEG pairing' }])]} />
        {hasRole('clinician') && (
          <Button leftSection={<ClipboardPlus size={16} />} onClick={() => navigate(`/patients/${patientId}/assessments/new`)}>New CRS-R</Button>
        )}
      </div>
      {list.error && <div className="notice error">{(list.error as Error).message}</div>}

      {view === 'list' && (
        <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="data">
            <thead><tr>
              {research ? <><th>Days since injury</th><th>Total</th><th>Category</th></> :
                <><th>Date</th><th>CRS-R total</th><th>Clinician category</th><th>Sedation</th><th>Status</th><th>Assessor</th><th>Nearest EEG</th></>}
            </tr></thead>
            <tbody>
              {list.data?.map((a) => research ? (
                <tr key={a.id}>
                  <td>D+{(a as ResearchAssessment).days_since_injury ?? '—'}</td><td>{a.total}/23</td><td>{a.clinician_category ?? '—'}</td>
                </tr>
              ) : (() => {
                const x = a as Assessment
                return (
                  <tr key={x.id}>
                    <td><Link to={`/assessments/${x.id}`}>{fmt(x.assessed_at)}</Link>{x.version > 1 && <span className="muted"> v{x.version}</span>}</td>
                    <td className="mono">{x.total}/23</td>
                    <td>{x.clinician_category ?? <span className="muted">not entered</span>}</td>
                    <td>{x.sedation_status === 'none' ? 'none' : <span style={{ color: x.sedation_status === 'unknown' ? undefined : 'var(--warning)' }}>{x.sedation_status}</span>}</td>
                    <td><span className={`pill ${x.status === 'signed' ? 'ok' : 'role'}`}>{x.status}</span></td>
                    <td>{x.assessor.name}</td>
                    <td className="muted">{x.nearest_recording ? `${x.nearest_recording.recording_code} (${x.nearest_recording.hours_between} h)` : '—'}</td>
                  </tr>
                )
              })())}
              {list.data?.length === 0 && (
                <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 28 }}>
                  No CRS-R assessments yet. Repeated assessments are recommended, because one assessment can miss signs of consciousness.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {view === 'trend' && (trend.data && trend.data.points.length > 0 && def.data ? (
        <>
          <p className="muted" style={{ marginTop: 0 }}>{trend.data.note} Hollow markers: assessed under reduced or ongoing sedation. Only signed assessments are shown.</p>
          <Suspense fallback={<p className="muted">Loading chart…</p>}>
            <TrendChart trend={trend.data} def={def.data} />
          </Suspense>
        </>
      ) : <p className="muted">{trend.isLoading ? 'Loading…' : 'The trend appears once at least one assessment is signed.'}</p>)}

      {view === 'pairs' && (
        <>
          {pairs.data && <p className="muted" style={{ marginTop: 0 }}>{pairs.data.note}</p>}
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data">
              <thead><tr><th>Recording</th><th>Recorded</th><th>Quality</th><th>Nearest CRS-R</th><th>Total</th><th>Category</th><th>Offset</th></tr></thead>
              <tbody>
                {pairs.data?.pairs.map((p) => (
                  <tr key={p.recording.id}>
                    <td className="mono"><Link to={`/recordings/${p.recording.id}`}>{p.recording.recording_code}</Link>{p.recording.sedated && ' 💊'}</td>
                    <td>{fmt(p.recording.start_utc)}</td><td>{p.recording.quality_grade ?? '—'}</td>
                    <td><Link to={`/assessments/${p.assessment.id}`}>{fmt(p.assessment.assessed_at)}</Link></td>
                    <td className="mono">{p.assessment.total}/23</td><td>{p.assessment.clinician_category ?? '—'}</td>
                    <td>{p.hours_between > 0 ? '+' : ''}{p.hours_between} h</td>
                  </tr>
                ))}
                {pairs.data?.pairs.length === 0 && <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 24 }}>No recording has a signed assessment within {pairs.data.window_hours} h.</td></tr>}
              </tbody>
            </table>
          </div>
          {pairs.data && pairs.data.unpaired_recordings.length > 0 && (
            <p className="muted">Recordings without a nearby assessment: {pairs.data.unpaired_recordings.join(', ')}</p>
          )}
        </>
      )}
    </>
  )
}
