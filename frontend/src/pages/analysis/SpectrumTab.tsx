import { MultiSelect } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { PsdResponse } from '../../api/types'
import Chart, { type EChartsOption } from '../../components/Chart'
import { BAND_LABEL } from './common'

const BAND_FILL = ['rgba(31,95,174,0.06)', 'rgba(15,140,140,0.06)']

export default function SpectrumTab({ runId }: { runId: string }) {
  const [groups, setGroups] = useState<string[]>(['global'])
  const [channels, setChannels] = useState<string[]>([])
  const q = `groups=${encodeURIComponent(groups.join(',') || 'global')}${channels.length ? `&channels=${encodeURIComponent(channels.join(','))}` : ''}`
  const { data, isLoading, error } = useQuery({ queryKey: ['psd', runId, q], queryFn: () => api<PsdResponse>(`/runs/${runId}/psd?${q}`), placeholderData: (p) => p })

  const option = useMemo<EChartsOption | null>(() => {
    if (!data) return null
    const f = data.freqs_hz
    const pts = (v: (number | null)[]) => v.map((y, i) => [f[i], y])
    const series: object[] = []
    const single = Object.keys(data.groups).length === 1 && Object.keys(data.channels).length === 0
    Object.entries(data.groups).forEach(([g, d], i) => {
      series.push({ name: g.replace('_', ' '), type: 'line', showSymbol: false, data: pts(d.mean), lineStyle: { width: 2 },
        ...(i === 0 ? { markArea: { silent: true, data: Object.entries(data.bands).map(([b, [lo, hi]], k) => [{ name: BAND_LABEL[b]?.split(' ')[0] ?? b, xAxis: lo, itemStyle: { color: BAND_FILL[k % 2] }, label: { color: '#5B6B7F', fontSize: 11, position: 'insideTop' } }, { xAxis: hi }]) } } : {}) })
      if (single) {
        series.push({ name: '95 % CI', type: 'line', showSymbol: false, data: pts(d.ci_low), lineStyle: { width: 1, type: 'dashed', opacity: 0.6 }, tooltip: { show: false } })
        series.push({ name: '95 % CI (upper)', type: 'line', showSymbol: false, data: pts(d.ci_high), lineStyle: { width: 1, type: 'dashed', opacity: 0.6 }, tooltip: { show: false } })
      }
    })
    Object.entries(data.channels).forEach(([c, v]) => series.push({ name: c, type: 'line', showSymbol: false, data: pts(v), lineStyle: { width: 1.5 } }))
    return {
      grid: { left: 70, right: 24, top: 36, bottom: 56 }, tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v == null ? '—' : v.toExponential(2)) },
      legend: { top: 0, data: series.map((s) => (s as { name: string }).name).filter((n) => !n.startsWith('95 % CI (')) },
      xAxis: { type: 'value', name: 'Frequency (Hz)', nameLocation: 'middle', nameGap: 30, min: 0.5, max: 45, splitLine: { lineStyle: { color: '#EEF2F6' } } },
      yAxis: { type: 'log', name: `Power (${data.unit})`, nameLocation: 'middle', nameGap: 52, splitLine: { lineStyle: { color: '#EEF2F6' } }, axisLabel: { formatter: (v: number) => v.toExponential(0) } },
      series,
    }
  }, [data])

  return (
    <div className="stack">
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <MultiSelect label="Regions / global" style={{ flex: '1 1 260px' }} value={groups} onChange={setGroups} data={(data?.available_groups ?? ['global']).map((g) => ({ value: g, label: g.replace('_', ' ') }))} searchable />
        <MultiSelect label="Individual channels (optional)" style={{ flex: '1 1 260px' }} value={channels} onChange={setChannels} data={data?.available_channels ?? []} searchable />
      </div>
      {error && <div className="notice error">{(error as Error).message}</div>}
      <div className="card" style={{ padding: 16 }}>
        {option ? <Chart option={option} height={400} ariaLabel="Power spectrum on a logarithmic scale with frequency bands shaded" />
          : <p className="muted">{isLoading ? 'Loading…' : 'No spectrum available.'}</p>}
      </div>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        Average power spectrum over clean epochs (log scale). Dashed lines are the 95 % confidence interval of the average, from a block bootstrap over epochs.
        Shaded areas are the frequency bands used for band power.
      </p>
    </div>
  )
}
