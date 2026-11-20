import { Button, MultiSelect, SegmentedControl, Select, Tabs } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, del, patch, post } from '../../api/client'
import type { RecordingDetail, RunDetail, RunSummary, SegmentAnnotation, SignalWindow } from '../../api/types'
import { CONDITIONS } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import ConditionForm from './ConditionForm'
import EegCanvas from './EegCanvas'
import ProcessingPanel from './ProcessingPanel'

const STANDARD_1020 = ['Fp1', 'Fp2', 'F7', 'F3', 'Fz', 'F4', 'F8', 'T7', 'C3', 'Cz', 'C4', 'T8', 'P7', 'P3', 'Pz', 'P4', 'P8', 'O1', 'O2']
const SENSITIVITIES = [3, 5, 7, 10, 15, 20, 30, 50, 70]
const PAGES = [5, 10, 20, 30]
const fmtTime = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
const ARTEFACT_TYPES = [
  { value: 'artifact_eye', label: 'Eye movement / blink' }, { value: 'artifact_muscle', label: 'Muscle' },
  { value: 'artifact_movement', label: 'Movement' }, { value: 'electrode_pop', label: 'Electrode pop' }, { value: 'excluded', label: 'Other (exclude)' },
]

function Overview({ rec, start, page, onJump }: { rec: RecordingDetail; start: number; page: number; onJump: (t: number) => void }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [w, setW] = useState(900)
  const wrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setW(Math.max(320, el.clientWidth)))
    ro.observe(el); setW(Math.max(320, el.clientWidth))
    return () => ro.disconnect()
  }, [])
  useEffect(() => {
    const c = ref.current, ov = rec.overview
    if (!c) return
    const H = 56, dpr = window.devicePixelRatio || 1
    c.width = w * dpr; c.height = H * dpr
    const ctx = c.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.fillStyle = '#F7F9FB'; ctx.fillRect(0, 0, w, H)
    const bins = ov?.global_sd_uv ?? []
    if (bins.length) {
      const sorted = [...bins].sort((a, b) => a - b)
      const top = Math.max(sorted[Math.floor(sorted.length * 0.95)] || 1, 1)
      const bw = w / bins.length
      bins.forEach((v, i) => {
        const h = Math.min(1, v / top) * (H - 8)
        ctx.fillStyle = v > top * 1.8 ? '#D9A441' : '#9DB9DB'
        ctx.fillRect(i * bw, H - 2 - h, Math.max(bw - 0.5, 0.5), h)
      })
    }
    const x0 = (start / rec.duration_s) * w, wid = Math.max(3, (page / rec.duration_s) * w)
    ctx.fillStyle = 'rgba(31,95,174,0.14)'; ctx.fillRect(x0, 0, wid, H)
    ctx.strokeStyle = '#1F5FAE'; ctx.lineWidth = 1.5; ctx.strokeRect(x0 + 0.5, 0.5, wid, H - 1)
  }, [rec, start, page, w])
  return (
    <div ref={wrap}>
      <canvas ref={ref} style={{ width: w, height: 56, display: 'block', cursor: 'pointer', borderRadius: 8, border: '1px solid var(--border)' }}
        role="slider" aria-label="Recording overview: click to jump" aria-valuemin={0} aria-valuemax={Math.round(rec.duration_s)} aria-valuenow={Math.round(start)}
        onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); onJump(((e.clientX - r.left) / r.width) * rec.duration_s - page / 2) }} />
    </div>
  )
}

export default function RecordingViewer() {
  const { id = '' } = useParams()
  const qc = useQueryClient()
  const { hasRole } = useAuth()
  const canWrite = hasRole('clinician', 'eeg_technologist')
  const { data: rec, error } = useQuery({ queryKey: ['recording', id], queryFn: () => api<RecordingDetail>(`/recordings/${id}`) })
  const runs = useQuery({ queryKey: ['runs', id], queryFn: () => api<RunSummary[]>(`/recordings/${id}/runs`) })
  const annotations = useQuery({ queryKey: ['annotations', id], queryFn: () => api<SegmentAnnotation[]>(`/recordings/${id}/annotations`) })

  const [start, setStart] = useState(0)
  const [page, setPage] = useState(10)
  const [montage, setMontage] = useState('average')
  const [sens, setSens] = useState(7)
  const [pickedState, setPickedState] = useState<{ key: string; names: string[] } | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [stage, setStage] = useState<'raw' | 'processed'>('raw')
  const [runId, setRunId] = useState<string | null>(null)
  const [sel, setSel] = useState<{ a: number; b: number } | null>(null)
  const [artefact, setArtefact] = useState<string | null>('artifact_eye')

  const done = useMemo(() => (runs.data ?? []).filter((r) => r.status === 'succeeded'), [runs.data])
  const activeRunId = stage === 'processed' ? (runId && done.some((r) => r.id === runId) ? runId : (done.find((r) => r.is_current) ?? done[0])?.id ?? null) : null
  const runDetail = useQuery({ queryKey: ['run', activeRunId], enabled: !!activeRunId, queryFn: () => api<RunDetail>(`/runs/${activeRunId}`) })

  const eegNames = useMemo(() => {
    if (stage === 'processed') return (runDetail.data?.channels ?? []).map((c) => c.name)
    return (rec?.channels ?? []).filter((c) => c.ch_type === 'eeg').map((c) => c.name)
  }, [rec, stage, runDetail.data])
  const defaultSel = useMemo(() => {
    const std = STANDARD_1020.filter((n) => eegNames.includes(n))
    return std.length >= 8 ? std : eegNames.slice(0, 24)
  }, [eegNames])
  // The channel choice belongs to one data stage/run: changing either resets it (derived, no effect needed).
  const pickKey = `${stage}:${activeRunId}`
  const picked = pickedState?.key === pickKey ? pickedState.names : null
  const setPicked = (names: string[] | null) => setPickedState(names ? { key: pickKey, names } : null)
  const selected = picked ?? defaultSel

  const clampStart = useCallback((t: number) => (rec ? Math.max(0, Math.min(t, Math.max(0, rec.duration_s - page))) : 0), [rec, page])
  const channelsParam = montage === 'bipolar_longitudinal' ? '' : selected.join(',')
  const stageParams = stage === 'processed' && activeRunId ? `&stage=processed&run_id=${activeRunId}` : ''
  const sigUrl = useCallback((s: number) =>
    `/recordings/${id}/signal?start=${s}&duration=${page}&montage=${montage}&max_points=3000${stageParams}${channelsParam ? `&channels=${encodeURIComponent(channelsParam)}` : ''}`,
  [id, page, montage, stageParams, channelsParam])
  const ready = !!rec && (stage === 'raw' || !!activeRunId) && (montage === 'bipolar_longitudinal' || selected.length > 0)
  const signal = useQuery({ queryKey: ['signal', id, start, page, montage, channelsParam, stageParams], enabled: ready,
    queryFn: () => api<SignalWindow>(sigUrl(start)), placeholderData: (prev) => prev, retry: false })

  useEffect(() => {
    if (!rec || !ready || start + page >= rec.duration_s) return
    const ns = clampStart(start + page)
    void qc.prefetchQuery({ queryKey: ['signal', id, ns, page, montage, channelsParam, stageParams], queryFn: () => api<SignalWindow>(sigUrl(ns)) })
  }, [rec, ready, start, page, montage, channelsParam, stageParams, id, qc, clampStart, sigUrl])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName?.match(/INPUT|TEXTAREA|SELECT/)) return
      if (e.key === 'ArrowRight') setStart((s) => clampStart(s + page))
      if (e.key === 'ArrowLeft') setStart((s) => clampStart(s - page))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [page, clampStart])

  if (error) return <div className="notice error">{(error as Error).message}</div>
  if (!rec) return <p className="muted">Loading…</p>

  const toggleBad = async (index: number, bad: boolean) => {
    await patch(`/recordings/${id}/channels`, { channels: [bad ? { index, user_flag: 'bad' } : { index, clear_flag: true }] })
    await qc.invalidateQueries({ queryKey: ['recording', id] })
    await qc.invalidateQueries({ queryKey: ['signal', id] })
  }
  const setBaseline = async () => {
    const reason = window.prompt('Why is this the baseline recording? (required)')
    if (!reason) return
    try { await post(`/recordings/${id}/baseline`, { is_baseline: true, reason }); await qc.invalidateQueries({ queryKey: ['recording', id] }) }
    catch (e) { setMsg((e as Error).message) }
  }
  const addSegment = async (labelValue: string) => {
    if (!sel) return
    setMsg(null)
    try {
      await post(`/recordings/${id}/annotations`, { onset_s: Math.max(0, sel.a), duration_s: Math.max(0.1, sel.b - sel.a), label: labelValue })
      setSel(null); await qc.invalidateQueries({ queryKey: ['annotations', id] })
    } catch (e) { setMsg((e as Error).message) }
  }
  const removeSegment = async (aid: string) => { await del(`/recordings/${id}/annotations/${aid}`); await qc.invalidateQueries({ queryKey: ['annotations', id] }) }

  const cond = rec.condition
  const win = signal.data
  const unreviewed = stage === 'processed' && win?.unreviewed
  return (
    <>
      <p style={{ margin: '0 0 12px' }}><Link to={`/patients/${rec.patient_id}`}>← Patient</Link></p>
      <div className="card" style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <div className="mono muted">{rec.recording_code}</div>
          <h1 style={{ margin: '2px 0 6px' }}>{rec.start_time ? new Date(rec.start_time).toISOString().slice(0, 16).replace('T', ' ') : 'Recording'}</h1>
          <div className="muted">
            {cond ? <>{CONDITIONS.find((c) => c.value === cond.condition)?.label} · {label(cond.patient_state)}
              {cond.sedation.length > 0 && <> · sedation: {cond.sedation.map((s) => s.agent).join(', ')}</>}</>
              : <span style={{ color: 'var(--warning)' }}>Recording context not entered yet</span>}
            {' · '}{rec.sfreq_hz} Hz · {rec.n_eeg_channels} EEG channels · {fmtTime(rec.duration_s)}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          {rec.is_baseline && <span className="pill ok">★ Baseline</span>}
          {rec.quality_grade && <span className={`pill ${rec.quality_grade === 'good' ? 'ok' : rec.quality_grade === 'poor' ? 'bad' : 'role'}`}>Quality: {rec.quality_grade}</span>}
          <span className={`pill ${rec.qc_status === 'approved' ? 'ok' : rec.qc_status === 'rejected' ? 'bad' : 'role'}`}>QC: {rec.qc_status}</span>
          {rec.bad_channels.length > 0 && <span className="pill bad">{rec.bad_channels.length} flagged channels</span>}
          {hasRole('clinician') && !rec.is_baseline && <Button size="compact-sm" variant="default" onClick={() => void setBaseline()}>Set as baseline</Button>}
        </div>
      </div>
      {msg && <div className="notice error">{msg}</div>}

      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'end', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>Data</div>
            <SegmentedControl value={stage} onChange={(v) => setStage(v as 'raw' | 'processed')}
              data={[{ value: 'raw', label: 'Raw' }, { value: 'processed', label: 'Processed', disabled: done.length === 0 }]} />
          </div>
          {stage === 'processed' && done.length > 0 && (
            <Select label="Run" w={250} allowDeselect={false} value={activeRunId} onChange={setRunId}
              data={done.map((r) => ({ value: r.id, label: `${new Date(r.created_at).toISOString().slice(5, 16).replace('T', ' ')} · ${r.grade} · ${r.qc_status === 'pending' ? 'unreviewed' : r.qc_status}${r.is_current ? ' ★' : ''}` }))} />
          )}
          <Select label="Montage" w={190} allowDeselect={false} value={montage} onChange={(v) => setMontage(v ?? 'average')}
            data={[{ value: 'average', label: 'Average reference' }, { value: 'referential', label: stage === 'processed' ? 'As processed' : 'As recorded' }, { value: 'bipolar_longitudinal', label: 'Bipolar (double banana)' }]} />
          <Select label="Page" w={90} allowDeselect={false} value={String(page)} data={PAGES.map((p) => ({ value: String(p), label: `${p} s` }))}
            onChange={(v) => { const p = Number(v); setPage(p); setStart((s) => Math.max(0, Math.min(s, rec.duration_s - p))) }} />
          <Select label="Sensitivity" w={130} allowDeselect={false} value={String(sens)} data={SENSITIVITIES.map((s) => ({ value: String(s), label: `${s} µV/mm` }))}
            onChange={(v) => setSens(Number(v))} />
          {montage !== 'bipolar_longitudinal' && (
            <MultiSelect label="Channels" style={{ flex: '1 1 260px' }} searchable value={selected} onChange={setPicked} data={eegNames} maxDropdownHeight={260}
              placeholder="Select channels" />
          )}
          {montage !== 'bipolar_longitudinal' && (
            <div style={{ display: 'flex', gap: 6 }}>
              <Button size="xs" variant="default" onClick={() => setPicked(null)}>10-20 set</Button>
              <Button size="xs" variant="default" onClick={() => setPicked(eegNames)}>All</Button>
            </div>
          )}
        </div>

        {unreviewed && (
          <div className="notice warn" style={{ margin: '0 0 8px' }}>
            <b>Unreviewed data.</b> This processed run has not been approved, so it is not used for trends or comparisons. Red tint marks rejected epochs
            {win?.interpolated && win.interpolated.length > 0 && <>; channels marked ≈ ({win.interpolated.join(', ')}) were interpolated</>}.
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <Button size="xs" variant="default" aria-label="Previous page" disabled={start <= 0} onClick={() => setStart(clampStart(start - page))}><ChevronLeft size={16} /></Button>
          <Button size="xs" variant="default" aria-label="Next page" disabled={start + page >= rec.duration_s} onClick={() => setStart(clampStart(start + page))}><ChevronRight size={16} /></Button>
          <span className="muted">{fmtTime(start)} – {fmtTime(Math.min(start + page, rec.duration_s))} of {fmtTime(rec.duration_s)} · use ← → keys{canWrite && ' · drag across the traces to mark a segment'}</span>
          {signal.isFetching && <span className="muted" style={{ marginLeft: 'auto' }}>Loading…</span>}
        </div>

        {signal.isError && <div className="notice error">{(signal.error as Error).message}</div>}
        {win ? <EegCanvas win={win} sensitivity={sens} userAnnotations={annotations.data ?? []}
          onSelect={canWrite ? (a, b) => setSel({ a, b }) : undefined}
          height={Math.max(360, Math.min(780, win.channels.length * 30 + 40))} />
          : <p className="muted" style={{ padding: 40, textAlign: 'center' }}>{signal.isLoading ? 'Loading signal…' : 'Select at least one channel.'}</p>}

        {sel && canWrite && (
          <div className="notice" style={{ margin: '10px 0 0', background: 'var(--primary-soft)', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <b>{sel.a.toFixed(1)}–{sel.b.toFixed(1)} s</b>
            <Select w={190} aria-label="Artefact type" allowDeselect={false} value={artefact} onChange={setArtefact} data={ARTEFACT_TYPES} />
            <Button size="xs" color="red" variant="light" onClick={() => void addSegment(artefact ?? 'excluded')}>Exclude from analysis</Button>
            <Button size="xs" color="teal" variant="light" onClick={() => void addSegment('selected_for_analysis')}>Use only this segment</Button>
            <Button size="xs" variant="subtle" onClick={() => setSel(null)}>Cancel</Button>
            <span className="muted" style={{ fontSize: 12 }}>Applies the next time the pipeline is run.</span>
          </div>
        )}
        <div style={{ marginTop: 12 }}><Overview rec={rec} start={start} page={page} onJump={(t) => setStart(clampStart(t))} /></div>
      </div>

      <Tabs defaultValue="processing" mt="lg">
        <Tabs.List mb="md">
          <Tabs.Tab value="processing">Processing &amp; QC</Tabs.Tab>
          <Tabs.Tab value="info">File warnings</Tabs.Tab>
          <Tabs.Tab value="channels">Channels</Tabs.Tab>
          <Tabs.Tab value="segments">Segments ({annotations.data?.length ?? 0})</Tabs.Tab>
          <Tabs.Tab value="context">Recording context</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="processing">
          <ProcessingPanel recordingId={id} selectedRunId={activeRunId ?? runId} onSelectRun={(r) => { setRunId(r) }} />
        </Tabs.Panel>
        <Tabs.Panel value="info">
          <div className="card stack">
            {rec.warnings.length ? (
              <div className="notice warn" style={{ margin: 0 }}><ul style={{ margin: 0, paddingLeft: 18 }}>{rec.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
            ) : <div className="muted">No warnings.</div>}
            <div className="muted" style={{ fontSize: 13 }}>
              Patient identity in file: <b>{rec.identity_check.replace('_', ' ')}</b>. Annotations in the file:{' '}
              {Object.keys(rec.annotations).length ? Object.entries(rec.annotations).map(([k, v]) => `${k} ×${v}`).join(', ') : 'none'}.
            </div>
            <div className="mono muted">sha256 {rec.sha256}</div>
          </div>
        </Tabs.Panel>
        <Tabs.Panel value="channels">
          <div className="card" style={{ padding: 0, overflow: 'hidden', maxHeight: 420, overflowY: 'auto' }}>
            <table className="data">
              <thead><tr><th>Name</th><th>In file</th><th>Type</th><th>Positions</th><th>Auto check</th><th>Marked bad</th></tr></thead>
              <tbody>
                {rec.channels.map((c) => (
                  <tr key={c.index}>
                    <td style={{ fontWeight: 500 }}>{c.name}</td><td className="mono">{c.original_name}</td><td>{c.ch_type}</td>
                    <td>{c.has_position ? 'yes' : 'no'}</td>
                    <td><span className={`pill ${c.auto_flag === 'ok' ? 'ok' : c.auto_flag === 'unchecked' ? 'role' : 'bad'}`}>{c.auto_flag}</span></td>
                    <td>{canWrite && c.ch_type === 'eeg'
                      ? <Button size="compact-xs" variant={c.user_flag === 'bad' ? 'filled' : 'default'} color={c.user_flag === 'bad' ? 'red' : undefined}
                          onClick={() => void toggleBad(c.index, c.user_flag !== 'bad')}>{c.user_flag === 'bad' ? 'Bad — undo' : 'Mark bad'}</Button>
                      : c.user_flag ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Tabs.Panel>
        <Tabs.Panel value="segments">
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data">
              <thead><tr><th>From</th><th>To</th><th>Label</th><th>Source</th><th /></tr></thead>
              <tbody>
                {annotations.data?.map((a) => (
                  <tr key={a.id}>
                    <td>{a.onset_s.toFixed(1)} s</td><td>{(a.onset_s + a.duration_s).toFixed(1)} s</td>
                    <td><span className={`pill ${a.label === 'selected_for_analysis' ? 'ok' : 'bad'}`}>{label(a.label)}</span></td>
                    <td>{a.source === 'user' ? 'You' : 'Accepted proposal'}</td>
                    <td style={{ textAlign: 'right' }}>{canWrite && <Button size="compact-xs" variant="subtle" color="red" onClick={() => void removeSegment(a.id)}>Remove</Button>}</td>
                  </tr>
                ))}
                {annotations.data?.length === 0 && <tr><td colSpan={5} className="muted" style={{ textAlign: 'center', padding: 24 }}>No segments marked. Drag across the traces to exclude artefacts or select the part to analyse.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 13 }}>Segments take effect the next time the pipeline is run: excluded segments remove the epochs they overlap; if any segment is selected for analysis, only epochs inside selected segments are kept.</p>
        </Tabs.Panel>
        <Tabs.Panel value="context">
          <div className="card">
            {canWrite
              ? <ConditionForm recordingId={id} initial={cond} onSaved={() => void qc.invalidateQueries({ queryKey: ['recording', id] })} />
              : <div className="muted">{cond ? 'Recording context entered.' : 'Not entered.'}</div>}
          </div>
        </Tabs.Panel>
      </Tabs>
    </>
  )
}
