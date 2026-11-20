import { SegmentedControl, TextInput } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { RecordingsPage } from '../api/types'

const fmtDur = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`

export default function RecordingsOverview() {
  const navigate = useNavigate()
  const [qc, setQc] = useState('all')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams({ page: String(page), page_size: '25' })
  if (qc !== 'all') qs.set('qc_status', qc)
  if (q.trim()) qs.set('q', q.trim())
  const { data, error, isLoading } = useQuery({ queryKey: ['overview-recordings', qc, q, page], queryFn: () => api<RecordingsPage>(`/overview/recordings?${qs.toString()}`) })
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  return (
    <>
      <h1>Recordings</h1>
      <p className="muted" style={{ marginTop: 0 }}>Every EEG recording of the patients on your care team. Open a recording to view, process and review it.</p>
      <div style={{ display: 'flex', gap: 16, alignItems: 'end', flexWrap: 'wrap', marginBottom: 12 }}>
        <SegmentedControl value={qc} onChange={(v) => { setQc(v); setPage(1) }} data={[
          { value: 'all', label: 'All' },
          { value: 'pending', label: `QC pending${data ? ` (${data.counts.pending})` : ''}` },
          { value: 'approved', label: `Approved${data ? ` (${data.counts.approved})` : ''}` },
          { value: 'rejected', label: `Rejected${data ? ` (${data.counts.rejected})` : ''}` }]} />
        <TextInput placeholder="Patient ID or recording code" value={q} onChange={(e) => { setQ(e.currentTarget.value); setPage(1) }} style={{ minWidth: 260 }} aria-label="Search" />
      </div>
      {error && <div className="notice error">{(error as Error).message}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>Recording</th><th>Patient</th><th>Date</th><th>Duration</th><th>Channels</th><th>Quality</th><th>QC</th></tr></thead>
          <tbody>
            {data?.items.map((r) => (
              <tr key={r.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/recordings/${r.id}`)}>
                <td className="mono"><Link to={`/recordings/${r.id}`}>{r.recording_code}</Link>{r.is_baseline && ' ★'}</td>
                <td><Link to={`/patients/${r.patient_id}`} onClick={(e) => e.stopPropagation()}>{r.display_id}</Link></td>
                <td>{r.start_time ? new Date(r.start_time).toISOString().slice(0, 16).replace('T', ' ') : '—'}</td>
                <td>{fmtDur(r.duration_s)}</td><td>{r.n_eeg_channels} @ {r.sfreq_hz} Hz</td>
                <td>{r.quality_grade ? <span className={`pill ${r.quality_grade === 'good' ? 'ok' : r.quality_grade === 'poor' ? 'bad' : 'role'}`}>{r.quality_grade}</span> : <span className="muted">not run</span>}</td>
                <td><span className="pill role">{r.qc_status}</span></td>
              </tr>
            ))}
            {isLoading && <tr><td colSpan={7} className="muted">Loading…</td></tr>}
            {data?.items.length === 0 && <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: 28 }}>No recordings match.</td></tr>}
          </tbody>
        </table>
      </div>
      {data && data.total > data.page_size && (
        <p className="muted">Page {page} of {pages} · <a href="#prev" onClick={(e) => { e.preventDefault(); setPage(Math.max(1, page - 1)) }}>Previous</a> · <a href="#next" onClick={(e) => { e.preventDefault(); setPage(Math.min(pages, page + 1)) }}>Next</a></p>
      )}
    </>
  )
}
