import { Button, Select, Textarea } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, post } from '../../api/client'
import type { Note } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const TYPES = [{ value: 'observation', label: 'Observation' }, { value: 'interpretation', label: 'Interpretation' }, { value: 'plan', label: 'Plan' }]
const fmt = (d: string) => new Date(d).toISOString().replace('T', ' ').slice(0, 16)

function NoteCard({ n, canEdit, onChanged }: { n: Note; canEdit: boolean; onChanged: () => Promise<unknown> }) {
  const [editing, setEditing] = useState(false)
  const [body, setBody] = useState(n.body)
  const [showHist, setShowHist] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const hist = useQuery({ queryKey: ['note-versions', n.note_id, n.version], enabled: showHist, queryFn: () => api<Note[]>(`/notes/${n.note_id}/versions`) })
  const save = async () => {
    setErr(null)
    try { await post(`/notes/${n.note_id}/versions`, { body }); setEditing(false); await onChanged() } catch (x) { setErr((x as Error).message) }
  }
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <div><span className="pill role">{n.note_type}</span> <span className="muted">{n.author.name} · {fmt(n.first_written_at)}{n.version > 1 && ` · edited ${fmt(n.created_at)} (v${n.version})`}</span></div>
        <div style={{ display: 'flex', gap: 8 }}>
          {canEdit && !editing && <Button size="compact-xs" variant="subtle" onClick={() => setEditing(true)}>Edit</Button>}
          {(n.n_versions ?? 1) > 1 && <Button size="compact-xs" variant="subtle" onClick={() => setShowHist(!showHist)}>{showHist ? 'Hide' : 'Show'} history</Button>}
        </div>
      </div>
      {editing ? (
        <>
          <Textarea mt={8} autosize minRows={2} maxLength={5000} value={body} onChange={(e) => setBody(e.currentTarget.value)} />
          {err && <div className="notice error" style={{ marginTop: 8 }}>{err}</div>}
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <Button size="xs" disabled={!body.trim() || body === n.body} onClick={() => void save()}>Save as new version</Button>
            <Button size="xs" variant="default" onClick={() => { setEditing(false); setBody(n.body) }}>Cancel</Button>
          </div>
        </>
      ) : <p style={{ whiteSpace: 'pre-wrap', margin: '8px 0 0' }}>{n.body}</p>}
      {showHist && hist.data && (
        <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
          {hist.data.map((v) => (
            <div key={v.id} style={{ marginBottom: 6 }}>
              <span className="muted" style={{ fontSize: 12 }}>v{v.version} · {fmt(v.created_at)}</span>
              <div style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>{v.body}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function NotesTab({ patientId }: { patientId: string }) {
  const { user, hasRole } = useAuth()
  const qc = useQueryClient()
  const key = ['notes', patientId]
  const { data } = useQuery({ queryKey: key, queryFn: () => api<Note[]>(`/patients/${patientId}/notes`) })
  const [kind, setKind] = useState<string | null>('observation')
  const [body, setBody] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: key }), qc.invalidateQueries({ queryKey: ['timeline'] })])
  const add = async () => {
    setErr(null)
    try { await post(`/patients/${patientId}/notes`, { note_type: kind, body }); setBody(''); await refresh() } catch (x) { setErr((x as Error).message) }
  }
  return (
    <>
      {hasRole('clinician') && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 12 }}>
            <Select label="Type" allowDeselect={false} data={TYPES} value={kind} onChange={setKind} />
            <Textarea label="Note" autosize minRows={2} maxLength={5000} value={body} onChange={(e) => setBody(e.currentTarget.value)}
              description="Keep observations (what was seen) separate from interpretations (what you think it means). Edits are saved as new versions; nothing is overwritten." />
          </div>
          {err && <div className="notice error" style={{ marginTop: 8 }}>{err}</div>}
          <Button mt={10} disabled={!body.trim()} onClick={() => void add()}>Add note</Button>
        </div>
      )}
      {data?.map((n) => <NoteCard key={n.note_id} n={n} canEdit={hasRole('clinician') && n.author.id === user?.id} onChanged={refresh} />)}
      {data?.length === 0 && <p className="muted">No notes yet.</p>}
    </>
  )
}
