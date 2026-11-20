import { Button, Progress, Tabs } from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, downloadFile, post } from '../../api/client'
import type { FeatureRunStatus, JobInfo, RecordingDetail, RunDetail } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'
import AperiodicTab from './AperiodicTab'
import BandPowerTab from './BandPowerTab'
import FeaturesTab from './FeaturesTab'
import SpectrogramTab from './SpectrogramTab'
import SpectrumTab from './SpectrumTab'
import TopomapsTab from './TopomapsTab'

export default function AnalysisPage() {
  const { id: runId = '' } = useParams()
  const qc = useQueryClient()
  const { hasRole } = useAuth()
  const canCompute = hasRole('clinician', 'eeg_technologist')
  const [error, setError] = useState<string | null>(null)

  const run = useQuery({ queryKey: ['run', runId], queryFn: () => api<RunDetail>(`/runs/${runId}`) })
  const rec = useQuery({ queryKey: ['recording', run.data?.recording_id], enabled: !!run.data, queryFn: () => api<RecordingDetail>(`/recordings/${run.data!.recording_id}`) })
  const fr = useQuery({
    queryKey: ['feature-run', runId], queryFn: () => api<FeatureRunStatus>(`/runs/${runId}/feature-run`),
    refetchInterval: (q) => (q.state.data && ['queued', 'running'].includes(q.state.data.status) ? 1000 : false),
  })
  const job = useQuery({
    queryKey: ['feature-job', fr.data?.job_id], enabled: !!fr.data?.job_id && ['queued', 'running'].includes(fr.data.status),
    queryFn: () => api<JobInfo>(`/jobs/${fr.data!.job_id}`), refetchInterval: 1000,
  })

  const compute = async () => {
    setError(null)
    try { await post(`/runs/${runId}/features`); await qc.invalidateQueries({ queryKey: ['feature-run', runId] }) }
    catch (e) { setError((e as Error).message) }
  }

  if (run.error) return <div className="notice error">{(run.error as Error).message}</div>
  const r = run.data, s = fr.data
  const busy = s && ['queued', 'running'].includes(s.status)

  return (
    <>
      <p style={{ margin: '0 0 12px' }}>{r && <Link to={`/recordings/${r.recording_id}`}>← Recording</Link>}</p>
      <div className="card" style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <div className="mono muted">{rec.data?.recording_code}</div>
          <h1 style={{ margin: '2px 0 6px' }}>EEG analysis</h1>
          <div className="muted">
            Processing run {r ? new Date(r.created_at).toISOString().slice(0, 16).replace('T', ' ') : '…'} · {r && `${r.pipeline_name} v${r.pipeline_version}`}
            {s?.n_epochs != null && <> · {s.n_epochs} clean epochs</>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          {r?.grade && <span className={`pill ${r.grade === 'good' ? 'ok' : r.grade === 'poor' ? 'bad' : 'role'}`}>Quality: {r.grade}</span>}
          {r && <span className={`pill ${r.qc_status === 'approved' ? 'ok' : r.qc_status === 'rejected' ? 'bad' : 'role'}`}>QC: {r.qc_status === 'pending' ? 'unreviewed' : r.qc_status}</span>}
          {s?.status === 'succeeded' && (
            <Button size="compact-sm" variant="default" leftSection={<Download size={14} />}
              onClick={() => void downloadFile(`/runs/${runId}/features/export.csv`, `${rec.data?.recording_code ?? 'features'}_features.csv`).catch((e) => setError((e as Error).message))}>
              Export CSV
            </Button>)}
          {canCompute && s?.status === 'succeeded' && <Button size="compact-sm" variant="subtle" onClick={() => void compute()}>Recompute</Button>}
        </div>
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}

      {s?.status === 'succeeded' && r?.qc_status !== 'approved' && (
        <div className="notice warn"><b>Unreviewed data.</b> These results come from a processing run that has not been approved. Only approved runs feed trends and comparisons.</div>
      )}
      {s?.status === 'succeeded' && s.low_confidence && (
        <div className="notice warn"><b>Low confidence.</b> Fewer than 60 clean epochs were available, so every value here is less reliable than the intervals suggest.</div>
      )}

      {(!s || s.status === 'none' || s.status === 'failed' || busy) && (
        <div className="card stack" style={{ maxWidth: 640 }}>
          <h2 style={{ margin: 0 }}>{busy ? 'Computing features…' : 'Features not computed yet'}</h2>
          <p className="muted" style={{ margin: 0 }}>
            Time-domain, frequency-band, aperiodic, wavelet and complexity measures for every channel, region and the whole head, each with a 95 % confidence interval.
          </p>
          {s?.status === 'failed' && <div className="notice error" style={{ margin: 0 }}>{s.error}</div>}
          {busy && <><div className="muted" style={{ fontSize: 13 }}>{job.data?.message ?? 'Starting'}</div><Progress value={job.data?.progress ?? 0} aria-label="Feature extraction progress" /></>}
          {!busy && canCompute && <div><Button onClick={() => void compute()}>Compute features</Button></div>}
          {!busy && !canCompute && <p className="muted">A clinician or EEG technologist can compute them.</p>}
        </div>
      )}

      {s?.status === 'succeeded' && (
        <Tabs defaultValue="spectrum" keepMounted={false}>
          <Tabs.List mb="md">
            <Tabs.Tab value="spectrum">Spectrum</Tabs.Tab>
            <Tabs.Tab value="bands">Band power</Tabs.Tab>
            <Tabs.Tab value="spectrogram">Spectrogram</Tabs.Tab>
            <Tabs.Tab value="topomaps">Topographic maps</Tabs.Tab>
            <Tabs.Tab value="features">All features</Tabs.Tab>
            <Tabs.Tab value="aperiodic">Aperiodic</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="spectrum"><SpectrumTab runId={runId} /></Tabs.Panel>
          <Tabs.Panel value="bands"><BandPowerTab runId={runId} /></Tabs.Panel>
          <Tabs.Panel value="spectrogram"><SpectrogramTab runId={runId} /></Tabs.Panel>
          <Tabs.Panel value="topomaps"><TopomapsTab runId={runId} bands={Object.keys(s.bands ?? {})} /></Tabs.Panel>
          <Tabs.Panel value="features"><FeaturesTab runId={runId} /></Tabs.Panel>
          <Tabs.Panel value="aperiodic"><AperiodicTab runId={runId} /></Tabs.Panel>
        </Tabs>
      )}
      <p className="muted" style={{ marginTop: 24, fontSize: 12 }}>
        Quantitative signal measures only. They are not a diagnosis and do not by themselves indicate a clinical or consciousness state.
      </p>
    </>
  )
}
