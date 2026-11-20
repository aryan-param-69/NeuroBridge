import { Button, Select, TextInput } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { Plus, Search } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { ETIOLOGIES, etiologyLabel, type PatientPage } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const PAGE_SIZE = 25

export default function PatientList() {
  const { hasRole } = useAuth()
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<string | null>('active')
  const [etiology, setEtiology] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [debouncedQ] = useDebouncedValue(q, 250)

  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
  if (debouncedQ.trim()) params.set('q', debouncedQ.trim())
  if (status) params.set('status', status)
  if (etiology) params.set('etiology', etiology)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['patients', params.toString()],
    queryFn: () => api<PatientPage>(`/patients?${params}`),
    placeholderData: (prev) => prev,
  })

  const pseudonymised = data?.items[0]?.view === 'research'
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1>Patients</h1>
          <p className="muted" style={{ margin: 0 }}>
            {pseudonymised
              ? 'Pseudonymised research view — consented patients only, no identifying information.'
              : 'Patients on your care team.'}
          </p>
        </div>
        {hasRole('clinician') && (
          <Button leftSection={<Plus size={16} />} onClick={() => navigate('/patients/new')}>
            Register patient
          </Button>
        )}
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        {!hasRole('clinician', 'eeg_technologist', 'viewer') ? null : (
          <TextInput style={{ flex: '1 1 320px', maxWidth: 420 }} leftSection={<Search size={16} />}
            placeholder="Search name (3+ letters), MRN or NB-ID" value={q}
            onChange={(e) => { setQ(e.currentTarget.value); setPage(1) }} aria-label="Search patients" />
        )}
        <Select aria-label="Status" value={status} onChange={(v) => { setStatus(v); setPage(1) }}
          data={[{ value: 'active', label: 'Active' }, { value: 'discharged', label: 'Discharged' },
            { value: 'archived', label: 'Archived' }]} placeholder="All statuses" clearable w={170} />
        <Select aria-label="Etiology" value={etiology} onChange={(v) => { setEtiology(v); setPage(1) }}
          data={ETIOLOGIES} placeholder="All etiologies" clearable w={260} />
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data">
          <thead>
            <tr>
              <th>ID</th>{!pseudonymised && <th>Name</th>}<th>{pseudonymised ? 'Birth year' : 'Age'}</th>
              <th>Sex</th><th>Etiology</th><th>Since injury</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((p) => (
              <tr key={p.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/patients/${p.id}`)}>
                <td className="mono"><Link to={`/patients/${p.id}`}>{p.display_id}</Link></td>
                {!pseudonymised && <td style={{ fontWeight: 500 }}>{p.full_name}</td>}
                <td>{pseudonymised ? p.birth_year : p.age}</td>
                <td style={{ textTransform: 'capitalize' }}>{p.sex}</td>
                <td>{etiologyLabel(p.etiology)}</td>
                <td>{p.days_since_injury != null ? `D+${p.days_since_injury}` : '—'}</td>
                <td><span className={`pill ${p.status === 'active' ? 'ok' : 'role'}`}>{p.status}</span></td>
              </tr>
            ))}
            {isLoading && <tr><td colSpan={7} className="muted">Loading…</td></tr>}
            {isError && <tr><td colSpan={7} className="muted">{(error as Error).message}</td></tr>}
            {data && data.items.length === 0 && (
              <tr><td colSpan={7} className="muted" style={{ padding: 32, textAlign: 'center' }}>
                No patients found.{hasRole('clinician') && ' Register a patient to get started.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {data && data.total > PAGE_SIZE && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginTop: 16 }}>
          <span className="muted">{data.total} patients · page {page} of {pages}</span>
          <Button variant="default" size="xs" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
          <Button variant="default" size="xs" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
        </div>
      )}
    </>
  )
}
