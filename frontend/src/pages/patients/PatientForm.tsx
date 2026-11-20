import { Button, Checkbox, Select, TextInput } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, api, patch, post } from '../../api/client'
import { ETIOLOGIES, type Patient } from '../../api/types'

type Form = {
  full_name: string; date_of_birth: string; sex: string; mrn: string; guardian_contact: string
  etiology: string; etiology_other: string; date_of_injury: string; admission_date: string
  clinical_status_label: string; craniectomy: boolean; consent_clinical: boolean
  consent_research: boolean; tags: string
}

const EMPTY: Form = {
  full_name: '', date_of_birth: '', sex: 'unknown', mrn: '', guardian_contact: '', etiology: 'unknown',
  etiology_other: '', date_of_injury: '', admission_date: '', clinical_status_label: '',
  craniectomy: false, consent_clinical: false, consent_research: false, tags: '',
}

const toBody = (x: Form) => ({
  full_name: x.full_name.trim(), date_of_birth: x.date_of_birth, sex: x.sex, mrn: blankToNull(x.mrn),
  guardian_contact: blankToNull(x.guardian_contact), etiology: x.etiology,
  etiology_other: blankToNull(x.etiology_other), date_of_injury: blankToNull(x.date_of_injury),
  admission_date: blankToNull(x.admission_date), clinical_status_label: blankToNull(x.clinical_status_label),
  craniectomy: x.craniectomy, consent_clinical: x.consent_clinical, consent_research: x.consent_research,
  tags: x.tags.split(',').map((t) => t.trim()).filter(Boolean),
})

type Dup = { display_id: string; match: 'name_dob' | 'mrn'; id: string | null }

const blankToNull = (v: string) => (v.trim() === '' ? null : v.trim())

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="card" style={{ marginBottom: 20 }}>
      <h2>{title}</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>{children}</div>
    </div>
  )
}

export default function PatientForm() {
  const { id } = useParams()
  const editing = !!id
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [f, setF] = useState<Form>(EMPTY)
  const [orig, setOrig] = useState<Form | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dups, setDups] = useState<Dup[] | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!id) return
    api<Patient>(`/patients/${id}`).then((p) => {
      const v: Form = {
        full_name: p.full_name ?? '', date_of_birth: p.date_of_birth ?? '', sex: p.sex,
        mrn: p.mrn ?? '', guardian_contact: p.guardian_contact ?? '', etiology: p.etiology,
        etiology_other: p.etiology_other ?? '', date_of_injury: p.date_of_injury ?? '',
        admission_date: p.admission_date ?? '', clinical_status_label: p.clinical_status_label ?? '',
        craniectomy: p.craniectomy, consent_clinical: !!p.consent_clinical,
        consent_research: p.consent_research, tags: (p.tags ?? []).join(', '),
      }
      setF(v); setOrig(v)
    }).catch((e) => setError((e as Error).message))
  }, [id])

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }))

  const body = () => toBody(f)

  const submit = async (e?: FormEvent, confirmDuplicate = false) => {
    e?.preventDefault()
    setBusy(true); setError(null)
    try {
      if (editing && orig) {
        const now = toBody(f)
        const before = toBody(orig) as Record<string, unknown>
        const changed = Object.fromEntries(
          Object.entries(now).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(before[k])))
        if (Object.keys(changed).length) await patch(`/patients/${id}`, changed)
        await qc.invalidateQueries({ queryKey: ['patient', id] })
        navigate(`/patients/${id}`)
      } else {
        const p = await post<Patient>('/patients', { ...body(), confirm_duplicate: confirmDuplicate })
        await qc.invalidateQueries({ queryKey: ['patients'] })
        navigate(`/patients/${p.id}`)
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && !editing) {
        setDups((err.detail as { duplicates: Dup[] }).duplicates)
      } else {
        setError((err as Error).message)
      }
    } finally { setBusy(false) }
  }

  return (
    <form onSubmit={(e) => void submit(e)} style={{ maxWidth: 900 }}>
      <h1>{editing ? 'Edit patient' : 'Register patient'}</h1>
      <p className="muted" style={{ marginTop: 0, marginBottom: 24 }}>
        Identifying details are stored encrypted and are never shown to researchers.
      </p>

      {error && <div className="notice error" role="alert">{error}</div>}
      {dups && (
        <div className="notice warn" role="alert">
          <strong>Possible duplicate.</strong> A patient with matching {dups.some((d) => d.match === 'mrn') ? 'MRN or ' : ''}
          name and date of birth is already registered:{' '}
          {dups.map((d, i) => (
            <span key={d.display_id}>{i > 0 && ', '}
              {d.id ? <Link to={`/patients/${d.id}`}>{d.display_id}</Link> : <span className="mono">{d.display_id}</span>}
              {' '}({d.match === 'mrn' ? 'same MRN' : 'same name and birth date'})
            </span>
          ))}
          .{' '}
          <Button size="compact-sm" variant="default" mt={8} loading={busy}
            onClick={() => void submit(undefined, true)}>Register anyway</Button>
        </div>
      )}

      <Section title="Identity">
        <TextInput label="Full name" required value={f.full_name} onChange={(e) => set('full_name', e.currentTarget.value)} />
        <TextInput label="Date of birth" type="date" required value={f.date_of_birth}
          onChange={(e) => set('date_of_birth', e.currentTarget.value)} />
        <Select label="Sex" required allowDeselect={false} value={f.sex} onChange={(v) => set('sex', v ?? 'unknown')}
          data={[{ value: 'female', label: 'Female' }, { value: 'male', label: 'Male' },
            { value: 'other', label: 'Other' }, { value: 'unknown', label: 'Unknown' }]} />
        <TextInput label="Hospital MRN" value={f.mrn} onChange={(e) => set('mrn', e.currentTarget.value)} />
        <TextInput label="Guardian / contact" style={{ gridColumn: '1 / -1' }} value={f.guardian_contact}
          onChange={(e) => set('guardian_contact', e.currentTarget.value)} />
      </Section>

      <Section title="Clinical background">
        <Select label="Etiology" required allowDeselect={false} data={ETIOLOGIES} value={f.etiology}
          onChange={(v) => set('etiology', v ?? 'unknown')} />
        {f.etiology === 'other'
          ? <TextInput label="Describe etiology" required value={f.etiology_other}
              onChange={(e) => set('etiology_other', e.currentTarget.value)} />
          : <div />}
        <TextInput label="Date of injury" type="date" value={f.date_of_injury}
          onChange={(e) => set('date_of_injury', e.currentTarget.value)} />
        <TextInput label="Admission date" type="date" value={f.admission_date}
          onChange={(e) => set('admission_date', e.currentTarget.value)} />
        <TextInput label="Current clinical status (entered by clinician)" style={{ gridColumn: '1 / -1' }}
          value={f.clinical_status_label} onChange={(e) => set('clinical_status_label', e.currentTarget.value)} />
        <Checkbox label="Craniectomy / skull defect (affects EEG amplitude)" checked={f.craniectomy}
          onChange={(e) => set('craniectomy', e.currentTarget.checked)} />
        <TextInput label="Tags (comma separated)" value={f.tags} onChange={(e) => set('tags', e.currentTarget.value)} />
      </Section>

      <Section title="Consent">
        <Checkbox label="Consent for clinical care" checked={f.consent_clinical}
          onChange={(e) => set('consent_clinical', e.currentTarget.checked)} />
        <Checkbox label="Consent for research use (pseudonymised data)" checked={f.consent_research}
          onChange={(e) => set('consent_research', e.currentTarget.checked)} />
      </Section>

      <div style={{ display: 'flex', gap: 12 }}>
        <Button type="submit" loading={busy}>{editing ? 'Save changes' : 'Register patient'}</Button>
        <Button variant="default" onClick={() => navigate(editing ? `/patients/${id}` : '/patients')}>Cancel</Button>
      </div>
    </form>
  )
}
