import { Button, Progress } from '@mantine/core'
import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, api, post } from '../../api/client'
import type { RecordingDetail } from '../../api/types'
import { Sha256 } from '../../lib/sha256'
import ConditionForm from './ConditionForm'

type Stage = 'choose' | 'uploading' | 'review' | 'context'
type Mismatch = { message: string; reasons: string[] }

const mb = (n: number) => `${(n / 1024 ** 2).toFixed(1)} MB`

export default function UploadRecording() {
  const { id: patientId = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [stage, setStage] = useState<Stage>('choose')
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState(0)
  const [phase, setPhase] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [mismatch, setMismatch] = useState<Mismatch | null>(null)
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [digest, setDigest] = useState<string | null>(null)
  const [rec, setRec] = useState<RecordingDetail | null>(null)

  const complete = async (id: string, sha: string, confirm: boolean) => {
    setPhase('Checking the file…')
    try {
      const r = await post<RecordingDetail>(`/uploads/${id}/complete`, { sha256: sha, confirm_identity_mismatch: confirm })
      setRec(r); setMismatch(null); setStage('review')
      await qc.invalidateQueries({ queryKey: ['recordings', patientId] })
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const d = e.detail as { identity_check?: string; reasons?: string[]; message?: string; missing?: number[] }
        if (d?.identity_check === 'mismatch') {
          setMismatch({ message: d.message ?? e.message, reasons: d.reasons ?? [] }); setStage('uploading'); return
        }
        setError(typeof d?.message === 'string' ? d.message : e.message)
      } else setError((e as Error).message)
      setStage('choose')
    }
  }

  const run = async () => {
    if (!file) return
    setError(null); setMismatch(null); setStage('uploading'); setProgress(0)
    try {
      setPhase('Starting upload…')
      const init = await post<{ upload_id: string; chunk_size: number; n_chunks: number }>(
        `/patients/${patientId}/recordings/uploads`, { filename: file.name, size: file.size })
      setUploadId(init.upload_id)
      const hash = new Sha256()
      for (let i = 0; i < init.n_chunks; i++) {
        setPhase(`Uploading… (part ${i + 1} of ${init.n_chunks})`)
        const buf = new Uint8Array(await file.slice(i * init.chunk_size, (i + 1) * init.chunk_size).arrayBuffer())
        hash.update(buf)
        await api(`/uploads/${init.upload_id}/chunks/${i}`, {
          method: 'PUT', body: buf as BodyInit, headers: { 'Content-Type': 'application/octet-stream' } })
        setProgress(Math.round(((i + 1) / init.n_chunks) * 100))
      }
      const sha = hash.hex()
      setDigest(sha)
      await complete(init.upload_id, sha, false)
    } catch (e) { setError((e as Error).message); setStage('choose') }
  }

  const eegWarnings = rec?.warnings ?? []

  return (
    <div style={{ maxWidth: 820 }}>
      <p style={{ margin: '0 0 12px' }}><Link to={`/patients/${patientId}`}>← Patient</Link></p>
      <h1>Upload EEG recording</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        EDF / EDF+ files. The original is stored encrypted; a copy with identifying header fields removed is used for viewing.
      </p>
      {error && <div className="notice error" role="alert">{error}</div>}

      {stage === 'choose' && (
        <div className="card">
          <input ref={fileInput} type="file" accept=".edf" hidden onChange={(e) => setFile(e.currentTarget.files?.[0] ?? null)} />
          <div style={{ border: '1px dashed var(--border)', borderRadius: 12, padding: 32, textAlign: 'center', background: 'var(--surface)' }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); setFile(e.dataTransfer.files?.[0] ?? null) }}>
            {file ? <><strong>{file.name}</strong> <span className="muted">· {mb(file.size)}</span></> : <span className="muted">Drag an .edf file here, or</span>}
            <div style={{ marginTop: 12 }}>
              <Button variant="default" onClick={() => fileInput.current?.click()}>{file ? 'Choose a different file' : 'Choose file'}</Button>
            </div>
          </div>
          <div style={{ marginTop: 16 }}><Button disabled={!file} onClick={() => void run()}>Upload and check</Button></div>
        </div>
      )}

      {stage === 'uploading' && (
        <div className="card">
          {!mismatch ? (
            <>
              <div style={{ marginBottom: 8 }}>{phase}</div>
              <Progress value={progress} aria-label="Upload progress" />
            </>
          ) : (
            <div className="notice warn" role="alert" style={{ margin: 0 }}>
              <strong>This file may belong to someone else.</strong>
              <p style={{ margin: '6px 0' }}>{mismatch.message}</p>
              <ul style={{ margin: '0 0 10px', paddingLeft: 18 }}>{mismatch.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
              <Button color="orange" onClick={() => uploadId && digest && void complete(uploadId, digest, true)}>
                I have checked — this is the right patient
              </Button>{' '}
              <Button variant="default" onClick={() => { setMismatch(null); setStage('choose') }}>Cancel</Button>
            </div>
          )}
        </div>
      )}

      {stage === 'review' && rec && (
        <div className="card stack">
          <h2 style={{ margin: 0 }}>{rec.recording_code} — file accepted</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 16 }}>
            <div><div className="muted" style={{ fontSize: 12 }}>Format</div><b>{rec.file_format}</b></div>
            <div><div className="muted" style={{ fontSize: 12 }}>Duration</div><b>{Math.floor(rec.duration_s / 60)} min {Math.round(rec.duration_s % 60)} s</b></div>
            <div><div className="muted" style={{ fontSize: 12 }}>Sampling rate</div><b>{rec.sfreq_hz} Hz</b></div>
            <div><div className="muted" style={{ fontSize: 12 }}>EEG channels</div><b>{rec.n_eeg_channels} / {rec.n_channels}</b></div>
          </div>
          <div className="muted" style={{ fontSize: 13 }}>
            Patient identity in file: <b>{rec.identity_check.replace('_', ' ')}</b>
          </div>
          {eegWarnings.length > 0 && (
            <div className="notice warn" style={{ margin: 0 }}>
              <strong>Please review</strong>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{eegWarnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          )}
          <div><Button onClick={() => setStage('context')}>Continue — recording context</Button></div>
        </div>
      )}

      {stage === 'context' && rec && (
        <div className="card">
          <h2>Recording context</h2>
          <ConditionForm recordingId={rec.id} submitLabel="Save and open recording"
            onSaved={() => navigate(`/recordings/${rec.id}`)} />
          <div style={{ marginTop: 12 }}>
            <Button variant="subtle" onClick={() => navigate(`/recordings/${rec.id}`)}>Skip for now</Button>
          </div>
        </div>
      )}
    </div>
  )
}
