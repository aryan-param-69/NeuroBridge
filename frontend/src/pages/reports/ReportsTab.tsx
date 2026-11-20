import { Button, Checkbox, Select, Textarea, TextInput } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, post } from '../../api/client'
import type { CmpCandidates, ReportSummary } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const fmt = (d?: string | null) => (d ? new Date(d).toISOString().slice(0, 10) : '—')

export default function ReportsTab({ patientId }: { patientId: string }) {
  const { hasRole } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const list = useQuery({ queryKey: ['reports', patientId], queryFn: () => api<ReportSummary[]>(`/patients/${patientId}/reports`) })
  const cand = useQuery({ queryKey: ['cmp-cand', patientId], enabled: hasRole('clinician'), queryFn: () => api<CmpCandidates>(`/patients/${patientId}/comparison-candidates`) })
  const [title, setTitle] = useState('EEG and assessment summary')
  const [summary, setSummary] = useState('')
  const [inc, setInc] = useState({ assessments: true, recordings: true, ai: false, notes: false })
  const [base, setBase] = useState<string | null>(null)
  const [target, setTarget] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const opts = (cand.data?.runs ?? []).map((r) => ({ value: r.run_id, label: `${r.recording_code} · ${(r.start ?? '').slice(0, 10)}` }))

  const create = async () => {
    setErr(null)
    try {
      const r = await post<ReportSummary>(`/patients/${patientId}/reports`, {
        title, clinical_summary: summary, include_assessments: inc.assessments, include_recordings: inc.recordings, include_ai: inc.ai, include_notes: inc.notes,
        ...(base && target ? { base_run: base, target_run: target } : {}),
      })
      await qc.invalidateQueries({ queryKey: ['reports'] })
      navigate(`/reports/${r.id}`)
    } catch (x) { setErr((x as Error).message) }
  }

  return (
    <>
      {hasRole('clinician') && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>New report</h3>
          <p className="muted" style={{ marginTop: 0 }}>A report is a snapshot of the platform's data plus your own written summary. It starts as a draft; once signed it can no longer be edited (an amendment becomes a new version).</p>
          <TextInput label="Title" value={title} onChange={(e) => setTitle(e.currentTarget.value)} maxLength={120} />
          <Textarea mt={10} label="Clinical summary (you can also write it after previewing)" autosize minRows={2} maxLength={4000} value={summary} onChange={(e) => setSummary(e.currentTarget.value)} />
          <div style={{ display: 'flex', gap: 18, margin: '12px 0', flexWrap: 'wrap' }}>
            <Checkbox label="CRS-R assessments (signed)" checked={inc.assessments} onChange={(e) => setInc({ ...inc, assessments: e.currentTarget.checked })} />
            <Checkbox label="Approved EEG recordings" checked={inc.recordings} onChange={(e) => setInc({ ...inc, recordings: e.currentTarget.checked })} />
            <Checkbox label="Sedation-model outputs (research)" checked={inc.ai} onChange={(e) => setInc({ ...inc, ai: e.currentTarget.checked })} />
            <Checkbox label="Clinician notes (free text)" checked={inc.notes} onChange={(e) => setInc({ ...inc, notes: e.currentTarget.checked })} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Select label="Comparison: base (optional)" clearable data={opts} value={base} onChange={setBase} />
            <Select label="Comparison: target" clearable data={opts} value={target} onChange={setTarget} />
          </div>
          {err && <div className="notice error" style={{ marginTop: 10 }}>{err}</div>}
          <Button mt={12} onClick={() => void create()}>Create draft report</Button>
        </div>
      )}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>Title</th><th>Version</th><th>Status</th><th>Author</th><th>Created</th><th>Signed</th></tr></thead>
          <tbody>
            {list.data?.map((r) => <tr key={r.id}><td><Link to={`/reports/${r.id}`}>{r.title}</Link></td><td>v{r.version}</td><td><span className={`pill ${r.status === 'signed' ? 'ok' : 'role'}`}>{r.status}</span></td><td>{r.author.name}</td><td>{fmt(r.created_at)}</td><td>{fmt(r.signed_at)}</td></tr>)}
            {list.data?.length === 0 && <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 24 }}>No reports yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}
