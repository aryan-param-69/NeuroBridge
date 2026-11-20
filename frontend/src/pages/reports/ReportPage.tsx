import { Button, Textarea, TextInput } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Printer } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, del, patch, post } from '../../api/client'
import type { ReportDetail } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

const fmt = (d?: string | null) => (d ? new Date(d).toISOString().slice(0, 16).replace('T', ' ') : '—')

export default function ReportPage() {
  const { id } = useParams()
  const { user, hasRole } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: r, error } = useQuery({ queryKey: ['report', id], queryFn: () => api<ReportDetail>(`/reports/${id}`) })
  const preview = useQuery({ queryKey: ['report-html', id, r?.content_sha256], enabled: !!r?.integrity_ok, queryFn: () => api<string>(`/reports/${id}/export.html?disposition=inline`) })
  const [title, setTitle] = useState<string | null>(null)
  const [summary, setSummary] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (error) return <div className="notice error">{(error as Error).message}</div>
  if (!r) return <p className="muted">Loading…</p>
  const isAuthorDraft = r.status === 'draft' && r.author.id === user?.id && hasRole('clinician')
  const latest = r.versions[r.versions.length - 1]
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: ['report', id] }), qc.invalidateQueries({ queryKey: ['report-html', id] }), qc.invalidateQueries({ queryKey: ['reports'] })])
  const run = async (fn: () => Promise<void>) => { setErr(null); setBusy(true); try { await fn() } catch (x) { setErr((x as Error).message) } finally { setBusy(false) } }

  const save = () => run(async () => { await patch(`/reports/${id}`, { title: title ?? r.title, clinical_summary: summary ?? r.clinical_summary }); setTitle(null); setSummary(null); await refresh() })
  const sign = () => run(async () => {
    if (title !== null || summary !== null) await patch(`/reports/${id}`, { title: title ?? r.title, clinical_summary: summary ?? r.clinical_summary })
    await post(`/reports/${id}/sign`, r.version > 1 ? { amendment_reason: reason } : {}); setTitle(null); setSummary(null); await refresh()
  })
  const amend = () => run(async () => { const n = await post<{ id: string }>(`/reports/${id}/amend`); await refresh(); navigate(`/reports/${n.id}`) })
  const refreshSnap = () => run(async () => { await post(`/reports/${id}/refresh`); await refresh() })
  const remove = () => run(async () => { await del(`/reports/${id}`); await qc.invalidateQueries({ queryKey: ['reports'] }); navigate(-1) })
  const download = () => run(async () => {
    const html = await api<string>(`/reports/${id}/export.html`)
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }))
    const a = document.createElement('a'); a.href = url; a.download = `report-v${r.version}.html`; a.click(); URL.revokeObjectURL(url)
  })
  const print = () => { const w = document.getElementById('report-frame') as HTMLIFrameElement | null; w?.contentWindow?.print() }

  return (
    <div style={{ maxWidth: 980 }}>
      <p style={{ margin: 0 }}><a href="#back" onClick={(e) => { e.preventDefault(); navigate(-1) }}>← Back</a></p>
      <h1 style={{ marginBottom: 4 }}>{r.title} <span className={`pill ${r.status === 'signed' ? 'ok' : 'role'}`} style={{ fontSize: 13 }}>{r.status}</span></h1>
      <p className="muted" style={{ marginTop: 0 }}>Version {r.version} · written by {r.author.name} · {fmt(r.created_at)}{r.signed_by && ` · signed by ${r.signed_by} on ${fmt(r.signed_at)}`}</p>
      {!r.integrity_ok && <div className="notice error" role="alert"><strong>Integrity check failed.</strong> This report no longer matches the hash recorded when it was saved. Do not rely on it; tell an administrator.</div>}
      {r.amendment_reason && <div className="notice warn">Amendment reason: {r.amendment_reason}</div>}
      {err && <div className="notice error" role="alert">{err}</div>}

      {isAuthorDraft && (
        <div className="card" style={{ marginBottom: 16 }}>
          <TextInput label="Title" value={title ?? r.title} onChange={(e) => setTitle(e.currentTarget.value)} maxLength={120} />
          <Textarea mt={10} label="Clinical summary (required; written by you)" autosize minRows={4} maxLength={4000} value={summary ?? r.clinical_summary} onChange={(e) => setSummary(e.currentTarget.value)}
            description="The platform lists measurements; it does not interpret them. Describe your own reading and plan." />
          <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap', alignItems: 'end' }}>
            <Button variant="default" disabled={busy || (title === null && summary === null)} onClick={() => void save()}>Save draft</Button>
            <Button variant="default" disabled={busy} onClick={() => void refreshSnap()}>Refresh data snapshot</Button>
            {r.version > 1 && <TextInput label="Reason for this amendment" placeholder="min. 5 characters" value={reason} onChange={(e) => setReason(e.currentTarget.value)} style={{ minWidth: 300 }} />}
            <Button disabled={busy || !(summary ?? r.clinical_summary).trim() || (r.version > 1 && reason.trim().length < 5)} onClick={() => void sign()}>Sign (cannot be edited afterwards)</Button>
            <Button color="red" variant="subtle" disabled={busy} onClick={() => void remove()}>Delete draft</Button>
          </div>
        </div>
      )}
      {r.status === 'draft' && !isAuthorDraft && <div className="notice warn">This is a draft by {r.author.name}.</div>}

      <div style={{ display: 'flex', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <Button variant="default" leftSection={<Download size={16} />} disabled={!r.integrity_ok || busy} onClick={() => void download()}>Download HTML</Button>
        <Button variant="default" leftSection={<Printer size={16} />} disabled={!preview.data} onClick={print}>Print / save as PDF</Button>
        {r.status === 'signed' && hasRole('clinician') && latest?.id === r.id && <Button variant="default" disabled={busy} onClick={() => void amend()}>Amend (new version)</Button>}
      </div>
      {preview.data ? (
        <iframe id="report-frame" title="Report preview" sandbox="allow-same-origin allow-modals" srcDoc={preview.data} style={{ width: '100%', height: 760, border: '1px solid var(--border)', borderRadius: 8, background: '#fff' }} />
      ) : <p className="muted">{r.integrity_ok ? 'Loading preview…' : ''}</p>}

      {r.versions.length > 1 && (
        <>
          <h3>Versions</h3>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data"><thead><tr><th>Version</th><th>Status</th><th>Signed</th><th>Reason</th></tr></thead>
              <tbody>{r.versions.map((v) => <tr key={v.id}><td>{v.id === r.id ? <strong>v{v.version} (this)</strong> : <Link to={`/reports/${v.id}`}>v{v.version}</Link>}</td><td>{v.status}</td><td>{fmt(v.signed_at)}</td><td>{v.amendment_reason ?? '—'}</td></tr>)}</tbody></table>
          </div>
        </>
      )}
      <p className="muted mono" style={{ fontSize: 11 }}>SHA-256 {r.content_sha256}</p>
    </div>
  )
}
