import { Button, Checkbox, MultiSelect, Radio, Select, Textarea, TextInput } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, del, patch, post } from '../../api/client'
import type { AssessmentDetail, CrsrDefinition, CrsrScores } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
const fmt = (d: string) => new Date(d).toISOString().replace('T', ' ').slice(0, 16)
const toLocalInput = (iso: string) => { const d = new Date(iso); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16) }
const OBS = ['auditory', 'visual', 'motor', 'communication'] as const
const SETTINGS = ['icu', 'ward', 'lab', 'home', 'other']

type Form = {
  assessed_at: string; setting: string | null; scores: Record<string, number | null>; arousal_facilitation_used: boolean
  sedation_status: string; sedation_note: string; confounders: string[]; observations: Record<string, string>
  clinician_category: string | null; category_note: string
}
const blank = (): Form => ({
  assessed_at: toLocalInput(new Date().toISOString()), setting: null, scores: {}, arousal_facilitation_used: false, sedation_status: 'unknown',
  sedation_note: '', confounders: [], observations: {}, clinician_category: null, category_note: '',
})

export default function AssessmentPage() {
  const { id, pid } = useParams()
  const defQ = useQuery({ queryKey: ['crsr-def'], queryFn: () => api<CrsrDefinition>('/assessments/crsr-definition'), staleTime: Infinity })
  const det = useQuery({ queryKey: ['assessment', id], enabled: !!id, queryFn: () => api<AssessmentDetail>(`/assessments/${id}`) })
  if (!defQ.data || (id && !det.data)) return <p className="muted">{det.error ? (det.error as Error).message : 'Loading…'}</p>
  return <Editor key={det.data?.id ?? 'new'} def={defQ.data} a={det.data} id={id} pid={pid} />
}

const fromRecord = (a: AssessmentDetail): Form => ({
  assessed_at: toLocalInput(a.assessed_at), setting: a.setting, scores: { ...a.scores }, arousal_facilitation_used: a.arousal_facilitation_used,
  sedation_status: a.sedation_status, sedation_note: a.sedation_note ?? '', confounders: a.confounders,
  observations: Object.fromEntries(Object.entries(a.observations ?? {}).map(([k, v]) => [k, v ?? ''])),
  clinician_category: a.clinician_category, category_note: a.category_note ?? '',
})

function Editor({ def, a, id, pid }: { def: CrsrDefinition; a?: AssessmentDetail; id?: string; pid?: string }) {
  const { user, hasRole } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [f, setF] = useState<Form>(() => (a ? fromRecord(a) : blank()))
  const [reason, setReason] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const patientId = a ? undefined : pid
  const editable = !id || (a?.status === 'draft' && a.assessor.id === user?.id && hasRole('clinician'))
  const latest = a ? a.versions[a.versions.length - 1] : null
  const canAmend = !!a && a.status === 'signed' && hasRole('clinician') && latest?.id === a.id
  const complete = def?.subscales.every((s) => f.scores[s.key] != null) ?? false
  const total = def ? def.subscales.reduce((n, s) => n + (f.scores[s.key] ?? 0), 0) : 0

  const body = () => ({
    assessed_at: new Date(f.assessed_at).toISOString(), setting: f.setting, scores: f.scores as CrsrScores,
    arousal_facilitation_used: f.arousal_facilitation_used, sedation_status: f.sedation_status, sedation_note: f.sedation_note || null,
    confounders: f.confounders, observations: Object.fromEntries(OBS.map((k) => [k, f.observations[k] || null])),
    clinician_category: f.clinician_category, category_note: f.category_note || null,
  })

  const save = async (): Promise<AssessmentDetail> => {
    const saved = id ? await patch<AssessmentDetail>(`/assessments/${id}`, body()) : await post<AssessmentDetail>(`/patients/${patientId}/assessments`, body())
    await qc.invalidateQueries({ queryKey: ['assessment', saved.id] })
    return saved
  }
  const run = async (fn: () => Promise<void>) => {
    setErr(null); setBusy(true)
    try { await fn() } catch (x) { setErr((x as Error).message) } finally { setBusy(false) }
  }
  const afterWrite = () => Promise.all(['assessments', 'trend', 'reminder', 'pairs', 'timeline'].map((k) => qc.invalidateQueries({ queryKey: [k] })))
  const onSave = () => run(async () => {
    const s = await save()
    await afterWrite()
    if (!id) navigate(`/assessments/${s.id}`, { replace: true })
  })
  const onSign = () => run(async () => {
    const s = await save()
    const signed = await post<AssessmentDetail>(`/assessments/${s.id}/sign`, a && a.version > 1 ? { amendment_reason: reason } : {})
    await qc.invalidateQueries({ queryKey: ['assessment', signed.id] })
    await afterWrite()
    navigate(-1)
  })
  const onDelete = () => run(async () => { await del(`/assessments/${id}`); await afterWrite(); navigate(-1) })
  const onAmend = () => run(async () => { const n = await post<AssessmentDetail>(`/assessments/${id}/amend`); await afterWrite(); navigate(`/assessments/${n.id}`) })

  const pat = a?.pattern

  return (
    <div style={{ maxWidth: 980 }}>
      <p style={{ margin: 0 }}><a href="#back" onClick={(e) => { e.preventDefault(); navigate(-1) }}>← Back</a></p>
      <h1 style={{ marginBottom: 4 }}>{id ? `CRS-R assessment${a!.version > 1 ? ` (version ${a!.version})` : ''}` : 'New CRS-R assessment'}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {def.version}. {def.rule} {a && <>Status: <strong>{a.status}</strong>{a.signed_by && <> · signed by {a.signed_by} on {fmt(a.signed_at!)}</>}.</>}
      </p>
      <div className="notice warn">{def.verify} The total is computed by the server; the category is always your own judgement.</div>
      {err && <div className="notice error" role="alert">{err}</div>}
      {a && !editable && a.status === 'draft' && <div className="notice warn">This draft belongs to {a.assessor.name}; only the author can edit or sign it.</div>}
      {a?.amendment_reason && <div className="notice warn">Amendment reason: {a.amendment_reason}</div>}

      <fieldset disabled={!editable || busy} style={{ border: 0, padding: 0, margin: 0 }}>
        <div className="card" style={{ display: 'grid', gridTemplateColumns: '240px 160px 1fr', gap: 16, marginBottom: 16 }}>
          <TextInput label="Assessment date and time (your local time)" type="datetime-local" required value={f.assessed_at} onChange={(e) => setF({ ...f, assessed_at: e.currentTarget.value })} />
          <Select label="Setting" clearable data={SETTINGS.map((v) => ({ value: v, label: label(v) }))} value={f.setting} onChange={(v) => setF({ ...f, setting: v })} />
          <Checkbox mt={26} label="Arousal-facilitation protocol used before testing" checked={f.arousal_facilitation_used} onChange={(e) => setF({ ...f, arousal_facilitation_used: e.currentTarget.checked })} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
          {def.subscales.map((s) => (
            <div className="card" key={s.key}>
              <Radio.Group label={`${s.label} (0–${s.max})`} value={f.scores[s.key] != null ? String(f.scores[s.key]) : ''}
                onChange={(v) => setF({ ...f, scores: { ...f.scores, [s.key]: Number(v) } })}>
                <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
                  {s.items.map((i) => (
                    <Radio key={i.level} value={String(i.level)} label={<>{i.level} — {i.label}{i.marker && <span title={def.markers[i.marker]} className="muted"> {i.marker === 'MCS' ? '*' : '†'}</span>}</>} />
                  ))}
                </div>
              </Radio.Group>
              {(OBS as readonly string[]).includes(s.key) && (
                <Textarea mt={10} size="xs" autosize minRows={1} maxLength={2000} placeholder="Observations (optional): what exactly was seen?" value={f.observations[s.key] ?? ''}
                  onChange={(e) => setF({ ...f, observations: { ...f.observations, [s.key]: e.currentTarget.value } })} />
              )}
            </div>
          ))}
        </div>
        <p className="muted" style={{ fontSize: 12 }}>* behaviour consistent with MCS · † emergence from MCS (markers as in the CRS-R)</p>

        <div className="card" style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 16, margin: '16px 0' }}>
          <Select label="Sedation / sedating drugs" allowDeselect={false} data={def.sedation.map((v) => ({ value: v, label: label(v) }))} value={f.sedation_status} onChange={(v) => setF({ ...f, sedation_status: v ?? 'unknown' })} />
          <TextInput label="Sedation note (drug, dose, time of last dose)" maxLength={300} value={f.sedation_note} onChange={(e) => setF({ ...f, sedation_note: e.currentTarget.value })} />
          <div style={{ gridColumn: '1 / -1' }}>
            <MultiSelect label="Possible confounders (may lower the score)" data={def.confounders.map((v) => ({ value: v, label: label(v) }))} value={f.confounders} onChange={(v) => setF({ ...f, confounders: v })} />
          </div>
        </div>

        <div className="card" style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 16, marginBottom: 16 }}>
          <Select label="Clinician category (your judgement)" clearable data={def.categories.map((v) => ({ value: v, label: v }))} value={f.clinician_category} onChange={(v) => setF({ ...f, clinician_category: v })} />
          <TextInput label={f.clinician_category === 'other' ? 'Describe the category (required)' : 'Category note (optional)'} maxLength={500} value={f.category_note} onChange={(e) => setF({ ...f, category_note: e.currentTarget.value })} />
          <div style={{ gridColumn: '1 / -1' }}>
            <strong>Total: {complete ? total : '—'} / {def.max_total}</strong> <span className="muted">(sum of the six subscale scores)</span>
          </div>
        </div>
      </fieldset>

      {pat && (
        <div className={`notice ${pat.differs_from_entered ? 'warn' : ''}`} style={pat.differs_from_entered ? undefined : { background: 'var(--primary-soft)', color: 'var(--primary)' }}>
          <strong>Pattern check (saved scores): {pat.pattern}.</strong> {pat.message} {pat.scoring_note} <span style={{ fontSize: 12 }}>{pat.disclaimer}</span>
        </div>
      )}

      {editable && (
        <div style={{ display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
          <Button variant="default" disabled={!complete || busy} onClick={() => void onSave()}>Save draft</Button>
          {a && a.version > 1 && <TextInput label="Reason for this amendment" placeholder="min. 5 characters" value={reason} onChange={(e) => setReason(e.currentTarget.value)} style={{ minWidth: 320 }} />}
          <Button disabled={!complete || !f.clinician_category || busy || (!!a && a.version > 1 && reason.trim().length < 5)} onClick={() => void onSign()}>Sign (cannot be edited afterwards)</Button>
          {id && <Button color="red" variant="subtle" disabled={busy} onClick={() => void onDelete()}>Delete draft</Button>}
        </div>
      )}
      {canAmend && <Button variant="default" disabled={busy} onClick={() => void onAmend()}>Amend (creates a new version; the signed one stays unchanged)</Button>}

      {a && a.versions.length > 0 && (
        <>
          <h3 style={{ marginTop: 28 }}>Version history</h3>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data">
              <thead><tr><th>Version</th><th>Status</th><th>Total</th><th>Signed</th><th>Reason</th></tr></thead>
              <tbody>
                {a.versions.map((v) => (
                  <tr key={v.id}>
                    <td>{v.id === a.id ? <strong>v{v.version} (this)</strong> : <Link to={`/assessments/${v.id}`}>v{v.version}</Link>}</td>
                    <td>{v.status}</td><td className="mono">{v.total}/23</td><td>{v.signed_at ? fmt(v.signed_at) : '—'}</td><td>{v.amendment_reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
