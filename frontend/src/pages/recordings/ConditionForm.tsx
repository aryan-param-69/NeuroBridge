import { Button, Checkbox, Select, Textarea, TextInput } from '@mantine/core'
import { useState, type FormEvent } from 'react'
import { put } from '../../api/client'
import {
  CONDITIONS, ELECTRODE_SYSTEMS, PATIENT_STATES, SETTINGS, type RecordingCondition, type Sedation,
} from '../../api/types'

const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

const EMPTY: RecordingCondition = {
  condition: 'resting_eyes_closed', patient_state: 'awake', sedation: [], anti_seizure_meds: [],
  setting: null, time_of_day: null, device_model: null, electrode_system: 'unknown', reference_label: null, notes: null,
}

/** Recording context. Sedation and state strongly change the EEG, so they are captured with every recording. */
export default function ConditionForm({ recordingId, initial, onSaved, submitLabel = 'Save context' }: {
  recordingId: string; initial?: RecordingCondition | null; onSaved: () => void; submitLabel?: string
}) {
  const [c, setC] = useState<RecordingCondition>({ ...EMPTY, ...(initial ?? {}) })
  const [sed, setSed] = useState<Sedation>({ agent: '', dose: null, unit: '' })
  const [meds, setMeds] = useState((initial?.anti_seizure_meds ?? []).join(', '))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = <K extends keyof RecordingCondition>(k: K, v: RecordingCondition[K]) => setC((s) => ({ ...s, [k]: v }))

  const addSedation = () => {
    if (!sed.agent.trim()) return
    set('sedation', [...c.sedation, { agent: sed.agent.trim(), dose: sed.dose || null, unit: sed.unit?.trim() || null }])
    setSed({ agent: '', dose: null, unit: '' })
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null)
    try {
      await put(`/recordings/${recordingId}/condition`, {
        ...c, anti_seizure_meds: meds.split(',').map((m) => m.trim()).filter(Boolean),
        time_of_day: c.time_of_day || null, device_model: c.device_model || null,
        reference_label: c.reference_label || null, notes: c.notes || null,
      })
      onSaved()
    } catch (x) { setError((x as Error).message) } finally { setBusy(false) }
  }

  return (
    <form className="stack" onSubmit={(e) => void submit(e)}>
      {error && <div className="notice error" role="alert">{error}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 14 }}>
        <Select label="Recording condition" required allowDeselect={false} data={CONDITIONS} value={c.condition}
          onChange={(v) => set('condition', v ?? 'other')} />
        <Select label="Patient state" required allowDeselect={false} value={c.patient_state}
          data={PATIENT_STATES.map((v) => ({ value: v, label: label(v) }))} onChange={(v) => set('patient_state', v ?? 'unknown')} />
        <Select label="Setting" clearable value={c.setting} data={SETTINGS.map((v) => ({ value: v, label: v.toUpperCase() }))}
          onChange={(v) => set('setting', v)} />
        <TextInput label="Time of day" type="time" value={c.time_of_day ?? ''} onChange={(e) => set('time_of_day', e.currentTarget.value || null)} />
        <TextInput label="Amplifier / device" value={c.device_model ?? ''} onChange={(e) => set('device_model', e.currentTarget.value)} />
        <Select label="Electrode system" allowDeselect={false} value={c.electrode_system} data={ELECTRODE_SYSTEMS}
          onChange={(v) => set('electrode_system', v ?? 'unknown')} />
        <TextInput label="Recording reference" placeholder="e.g. linked mastoids, Cz" value={c.reference_label ?? ''}
          onChange={(e) => set('reference_label', e.currentTarget.value)} />
        <TextInput label="Anti-seizure medication (comma separated)" value={meds} onChange={(e) => setMeds(e.currentTarget.value)} />
      </div>

      <div>
        <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 6 }}>
          Sedation given around the recording {c.patient_state === 'sedated' && <span style={{ color: 'var(--danger)' }}>(required)</span>}
        </div>
        {c.sedation.map((s, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
            <span className="pill role">{s.agent}</span>
            <span className="muted">{s.dose != null ? `${s.dose} ${s.unit ?? ''}` : 'dose not recorded'}</span>
            <Button size="compact-xs" variant="subtle" color="red"
              onClick={() => set('sedation', c.sedation.filter((_, j) => j !== i))}>Remove</Button>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 8, alignItems: 'end' }}>
          <TextInput placeholder="Agent (e.g. propofol)" value={sed.agent} onChange={(e) => setSed({ ...sed, agent: e.currentTarget.value })} />
          <TextInput placeholder="Dose" type="number" w={100} value={sed.dose ?? ''}
            onChange={(e) => setSed({ ...sed, dose: e.currentTarget.value ? Number(e.currentTarget.value) : null })} />
          <TextInput placeholder="Unit" w={110} value={sed.unit ?? ''} onChange={(e) => setSed({ ...sed, unit: e.currentTarget.value })} />
          <Button variant="default" onClick={addSedation}>Add</Button>
        </div>
      </div>

      <Textarea label="Notes" autosize minRows={2} maxLength={2000} value={c.notes ?? ''} onChange={(e) => set('notes', e.currentTarget.value)} />
      <Checkbox label="I have recorded all sedation and medication I know of" required />
      <div><Button type="submit" loading={busy}>{submitLabel}</Button></div>
    </form>
  )
}
