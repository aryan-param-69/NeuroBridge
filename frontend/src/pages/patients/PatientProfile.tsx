import { Button, Select, Tabs, Textarea, TextInput } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, del, patch, post, put } from '../../api/client'
import {
  etiologyLabel, type AccessMember, type ClinicalEvent, type DirectoryUser, type FieldChange,
  type HistoryItem, type Patient, type PatientStatus,
} from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import AiTab from '../ai/AiTab'
import AssessmentsTab from '../assessments/AssessmentsTab'
import NotesTab from './NotesTab'
import ReportsTab from '../reports/ReportsTab'
import RecordingsTab from './RecordingsTab'
import TimelineTab from './TimelineTab'

const fmtDate = (d?: string | null) => (d ? new Date(d).toISOString().slice(0, 10) : '—')
const fmtTime = (d: string) => new Date(d).toISOString().replace('T', ' ').slice(0, 16)
const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

const NEXT_STATUS: Record<PatientStatus, { to: PatientStatus; text: string }[]> = {
  active: [{ to: 'discharged', text: 'Mark discharged' }],
  discharged: [{ to: 'active', text: 'Reactivate' }, { to: 'archived', text: 'Archive' }],
  archived: [],
}

function Fact({ k, v }: { k: string; v?: React.ReactNode }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 12 }}>{k}</div>
      <div style={{ fontWeight: 500 }}>{v ?? '—'}</div>
    </div>
  )
}

function Overview({ p }: { p: Patient }) {
  const research = p.view === 'research'
  return (
    <div className="card" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 20 }}>
      {!research && <Fact k="Date of birth" v={`${fmtDate(p.date_of_birth)} (${p.age} y)`} />}
      {research && <Fact k="Birth year" v={p.birth_year} />}
      <Fact k="Sex" v={label(p.sex)} />
      {!research && <Fact k="MRN" v={p.mrn} />}
      <Fact k="Etiology" v={etiologyLabel(p.etiology) + (p.etiology_other ? ` — ${p.etiology_other}` : '')} />
      {!research && <Fact k="Date of injury" v={fmtDate(p.date_of_injury)} />}
      <Fact k="Days since injury" v={p.days_since_injury != null ? `D+${p.days_since_injury}` : undefined} />
      {!research && <Fact k="Admission" v={fmtDate(p.admission_date)} />}
      <Fact k="Craniectomy / skull defect" v={p.craniectomy ? 'Yes — affects EEG amplitude' : 'No'} />
      {!research && <Fact k="Clinical status (clinician-entered)" v={p.clinical_status_label} />}
      {!research && <Fact k="Clinical-care consent" v={p.consent_clinical ? 'Given' : 'Not recorded'} />}
      <Fact k="Research consent" v={p.consent_research ? `Given${p.consent_research_date ? ` (${fmtDate(p.consent_research_date)})` : ''}` : 'Not given'} />
      {p.view === 'full' && <Fact k="Guardian / contact" v={p.guardian_contact} />}
      {!research && <Fact k="Tags" v={p.tags?.length ? p.tags.join(', ') : undefined} />}
    </div>
  )
}

function MedicalHistory({ id, canEdit }: { id: string; canEdit: boolean }) {
  const qc = useQueryClient()
  const key = ['history', id]
  const { data } = useQuery({ queryKey: key, queryFn: () => api<HistoryItem[]>(`/patients/${id}/medical-history`) })
  const [cat, setCat] = useState<string | null>('diagnosis')
  const [desc, setDesc] = useState('')
  const [start, setStart] = useState('')
  const [err, setErr] = useState<string | null>(null)

  const add = async (e: FormEvent) => {
    e.preventDefault(); setErr(null)
    try {
      await post(`/patients/${id}/medical-history`, { category: cat, description: desc, start_date: start || null })
      setDesc(''); setStart(''); await qc.invalidateQueries({ queryKey: key })
    } catch (x) { setErr((x as Error).message) }
  }
  const act = async (fn: () => Promise<unknown>) => { await fn(); await qc.invalidateQueries({ queryKey: key }) }

  return (
    <>
      {canEdit && (
        <form className="card" onSubmit={(e) => void add(e)} style={{ marginBottom: 16, display: 'grid', gridTemplateColumns: '180px 1fr 170px auto', gap: 12, alignItems: 'end' }}>
          <Select label="Category" allowDeselect={false} value={cat} onChange={setCat}
            data={['diagnosis', 'comorbidity', 'medication', 'surgery', 'seizure'].map((v) => ({ value: v, label: label(v) }))} />
          <TextInput label="Description (e.g. propofol infusion, anti-seizure drug)" required value={desc} onChange={(e) => setDesc(e.currentTarget.value)} />
          <TextInput label="Start date" type="date" value={start} onChange={(e) => setStart(e.currentTarget.value)} />
          <Button type="submit">Add</Button>
          {err && <div className="notice error" style={{ gridColumn: '1 / -1', margin: 0 }}>{err}</div>}
        </form>
      )}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>Category</th><th>Description</th><th>Start</th><th>End</th><th>State</th>{canEdit && <th />}</tr></thead>
          <tbody>
            {data?.map((h) => (
              <tr key={h.id}>
                <td>{label(h.category)}</td><td>{h.description}</td><td>{fmtDate(h.start_date)}</td><td>{fmtDate(h.end_date)}</td>
                <td><span className={`pill ${h.is_active ? 'ok' : 'role'}`}>{h.is_active ? 'Active' : 'Ended'}</span></td>
                {canEdit && (
                  <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
                    <Button size="compact-xs" variant="subtle"
                      onClick={() => void act(() => patch(`/patients/${id}/medical-history/${h.id}`, { is_active: !h.is_active }))}>
                      {h.is_active ? 'Mark ended' : 'Reopen'}
                    </Button>
                    <Button size="compact-xs" variant="subtle" color="red"
                      onClick={() => { if (confirm('Remove this entry? It is retained in the audit trail.')) void act(() => del(`/patients/${id}/medical-history/${h.id}`)) }}>
                      Remove
                    </Button>
                  </td>
                )}
              </tr>
            ))}
            {data?.length === 0 && <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 24 }}>No entries yet. Record medications — especially sedatives and anti-seizure drugs — because they change the EEG.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Events({ id, canEdit }: { id: string; canEdit: boolean }) {
  const qc = useQueryClient()
  const key = ['events', id]
  const { data } = useQuery({ queryKey: key, queryFn: () => api<ClinicalEvent[]>(`/patients/${id}/events`) })
  const [type, setType] = useState<string | null>('medication_change')
  const [when, setWhen] = useState('')
  const [desc, setDesc] = useState('')
  const [err, setErr] = useState<string | null>(null)

  const add = async (e: FormEvent) => {
    e.preventDefault(); setErr(null)
    try {
      await post(`/patients/${id}/events`, { event_type: type, occurred_at: new Date(when).toISOString(), description: desc })
      setDesc(''); setWhen(''); await qc.invalidateQueries({ queryKey: key })
    } catch (x) { setErr((x as Error).message) }
  }

  return (
    <>
      {canEdit && (
        <form className="card" onSubmit={(e) => void add(e)} style={{ marginBottom: 16, display: 'grid', gridTemplateColumns: '210px 210px 1fr auto', gap: 12, alignItems: 'end' }}>
          <Select label="Event type" allowDeselect={false} value={type} onChange={setType}
            data={['medication_change', 'surgery', 'infection', 'transfer', 'seizure', 'other'].map((v) => ({ value: v, label: label(v) }))} />
          <TextInput label="Date and time" type="datetime-local" required value={when} onChange={(e) => setWhen(e.currentTarget.value)} />
          <Textarea label="Description" required autosize minRows={1} value={desc} onChange={(e) => setDesc(e.currentTarget.value)} />
          <Button type="submit">Add</Button>
          {err && <div className="notice error" style={{ gridColumn: '1 / -1', margin: 0 }}>{err}</div>}
        </form>
      )}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead><tr><th>When (UTC)</th><th>Type</th><th>Description</th></tr></thead>
          <tbody>
            {data?.map((e) => <tr key={e.id}><td>{fmtTime(e.occurred_at)}</td><td>{label(e.event_type)}</td><td>{e.description}</td></tr>)}
            {data?.length === 0 && <tr><td colSpan={3} className="muted" style={{ textAlign: 'center', padding: 24 }}>No events recorded. Events such as medication changes or infections help explain later EEG changes.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

function CareTeam({ id }: { id: string }) {
  const qc = useQueryClient()
  const key = ['access', id]
  const { data: members } = useQuery({ queryKey: key, queryFn: () => api<AccessMember[]>(`/patients/${id}/access`) })
  const { data: directory } = useQuery({ queryKey: ['directory'], queryFn: () => api<DirectoryUser[]>('/users/directory') })
  const [adding, setAdding] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const save = async (next: { user_id: string; access_level: string }[]) => {
    setErr(null)
    try { await put(`/patients/${id}/access`, { members: next }); await qc.invalidateQueries({ queryKey: key }) }
    catch (x) { setErr((x as Error).message) }
  }
  const current = (members ?? []).map((m) => ({ user_id: m.user_id, access_level: m.access_level }))
  const available = (directory ?? []).filter((u) => !members?.some((m) => m.user_id === u.id))

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>Only people on the care team can open this patient's record.</p>
      {err && <div className="notice error" role="alert">{err}</div>}
      <div className="card" style={{ padding: 0, overflow: 'hidden', marginBottom: 16 }}>
        <table className="data">
          <thead><tr><th>Name</th><th>Role</th><th>Access</th><th /></tr></thead>
          <tbody>
            {members?.map((m) => (
              <tr key={m.user_id}>
                <td>{m.full_name}</td><td>{m.roles.map(label).join(', ')}</td>
                <td><span className="pill role">{m.access_level}</span></td>
                <td style={{ textAlign: 'right' }}>
                  <Button size="compact-xs" variant="subtle" color="red"
                    onClick={() => void save(current.filter((c) => c.user_id !== m.user_id))}>Remove</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'end' }}>
        <Select label="Add team member" placeholder="Select staff member" value={adding} onChange={setAdding} w={320}
          data={available.map((u) => ({ value: u.id, label: `${u.full_name} — ${u.roles.map(label).join(', ')}` }))} />
        <Button disabled={!adding}
          onClick={() => { if (adding) void save([...current, { user_id: adding, access_level: 'member' }]).then(() => setAdding(null)) }}>
          Add
        </Button>
      </div>
    </>
  )
}

function Changes({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ['changes', id], queryFn: () => api<FieldChange[]>(`/patients/${id}/history`) })
  const show = (v: unknown) =>
    v && typeof v === 'object' && 'changed' in (v as object) ? '(identifying value — not stored in history)' : JSON.stringify(v) ?? '—'
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="data">
        <thead><tr><th>When (UTC)</th><th>Field</th><th>From</th><th>To</th></tr></thead>
        <tbody>
          {data?.map((c) => <tr key={c.id}><td>{fmtTime(c.changed_at)}</td><td>{label(c.field)}</td><td>{show(c.old_value)}</td><td>{show(c.new_value)}</td></tr>)}
          {data?.length === 0 && <tr><td colSpan={4} className="muted" style={{ textAlign: 'center', padding: 24 }}>No edits since registration.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

export default function PatientProfile() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { hasRole } = useAuth()
  const { data: p, isLoading, error } = useQuery({ queryKey: ['patient', id], queryFn: () => api<Patient>(`/patients/${id}`) })
  const [err, setErr] = useState<string | null>(null)

  if (isLoading) return <p className="muted">Loading…</p>
  if (error || !p) return <div className="notice error">{(error as Error)?.message ?? 'Patient not found'}</div>

  const clinician = hasRole('clinician')
  const clinical = p.view !== 'research'
  const changeStatus = async (to: PatientStatus) => {
    setErr(null)
    try { await patch(`/patients/${id}`, { status: to }); await qc.invalidateQueries({ queryKey: ['patient', id] }) }
    catch (x) { setErr((x as Error).message) }
  }

  return (
    <>
      <p style={{ margin: '0 0 12px' }}><Link to="/patients">← Patients</Link></p>
      <div className="card" style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <div className="mono muted">{p.display_id}</div>
          <h1 style={{ margin: '2px 0 6px' }}>{clinical ? p.full_name : 'Pseudonymised patient'}</h1>
          <div className="muted">
            {clinical ? `${p.age} y` : `Born ${p.birth_year}`} · {label(p.sex)} · {etiologyLabel(p.etiology)}
            {p.days_since_injury != null && ` · D+${p.days_since_injury}`}{' '}
            <span className={`pill ${p.status === 'active' ? 'ok' : 'role'}`} style={{ marginLeft: 6 }}>{p.status}</span>
          </div>
        </div>
        {clinician && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            {NEXT_STATUS[p.status].map((s) => (
              <Button key={s.to} variant="default" onClick={() => void changeStatus(s.to)}>{s.text}</Button>
            ))}
            <Button onClick={() => navigate(`/patients/${id}/edit`)}>Edit</Button>
          </div>
        )}
      </div>
      {err && <div className="notice error" role="alert">{err}</div>}

      <Tabs defaultValue="overview" keepMounted={false}>
        <Tabs.List mb="md">
          <Tabs.Tab value="overview">Overview</Tabs.Tab>
          {clinical && <Tabs.Tab value="recordings">Recordings</Tabs.Tab>}
          <Tabs.Tab value="assessments">Assessments</Tabs.Tab>
          {clinical && <Tabs.Tab value="timeline">Timeline</Tabs.Tab>}
          {clinical && <Tabs.Tab value="compare" onClick={() => navigate(`/patients/${id}/compare`)}>Compare</Tabs.Tab>}
          {clinical && <Tabs.Tab value="ai">AI analysis</Tabs.Tab>}
          {clinical && <Tabs.Tab value="reports">Reports</Tabs.Tab>}
          {clinical && <Tabs.Tab value="notes">Notes</Tabs.Tab>}
          {clinical && <Tabs.Tab value="history">Medical history</Tabs.Tab>}
          {clinical && <Tabs.Tab value="events">Clinical events</Tabs.Tab>}
          {clinician && <Tabs.Tab value="team">Care team</Tabs.Tab>}
          {clinician && <Tabs.Tab value="changes">Change history</Tabs.Tab>}
        </Tabs.List>
        <Tabs.Panel value="overview"><Overview p={p} /></Tabs.Panel>
        <Tabs.Panel value="recordings"><RecordingsTab patientId={id} /></Tabs.Panel>
        <Tabs.Panel value="assessments"><AssessmentsTab patientId={id} /></Tabs.Panel>
        <Tabs.Panel value="timeline"><TimelineTab patientId={id} /></Tabs.Panel>
        <Tabs.Panel value="ai"><AiTab patientId={id} /></Tabs.Panel>
        <Tabs.Panel value="reports"><ReportsTab patientId={id} /></Tabs.Panel>
        <Tabs.Panel value="notes"><NotesTab patientId={id} /></Tabs.Panel>
        <Tabs.Panel value="history"><MedicalHistory id={id} canEdit={clinician} /></Tabs.Panel>
        <Tabs.Panel value="events"><Events id={id} canEdit={clinician} /></Tabs.Panel>
        <Tabs.Panel value="team"><CareTeam id={id} /></Tabs.Panel>
        <Tabs.Panel value="changes"><Changes id={id} /></Tabs.Panel>
      </Tabs>
      <p className="muted" style={{ marginTop: 24, fontSize: 12 }}>
        Hardening, calibration on clinical recordings and external validation are still to come (see the README).
      </p>
    </>
  )
}
