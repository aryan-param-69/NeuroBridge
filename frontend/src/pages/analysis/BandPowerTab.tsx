import { SegmentedControl } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../../api/client'
import type { FeatureRow } from '../../api/types'
import Chart, { type EChartsOption } from '../../components/Chart'
import { BAND_LABEL, BANDS, fmtNum } from './common'

type Resp = { rows: FeatureRow[] }

export default function BandPowerTab({ runId }: { runId: string }) {
  const [kind, setKind] = useState<'rel_power' | 'abs_power'>('rel_power')
  const regions = useQuery({ queryKey: ['bp-region', runId, kind], queryFn: () => api<Resp>(`/runs/${runId}/features?level=region&names=${kind}`) })
  const channels = useQuery({ queryKey: ['bp-channel', runId, kind], queryFn: () => api<Resp>(`/runs/${runId}/features?level=channel&names=${kind}`) })
  const abs = kind === 'abs_power'

  const names = useMemo(() => [...new Set((regions.data?.rows ?? []).map((r) => r.name))], [regions.data])
  const bands = useMemo(() => BANDS.filter((b) => regions.data?.rows.some((r) => r.band === b)), [regions.data])
  const option = useMemo<EChartsOption | null>(() => {
    if (!regions.data) return null
    const get = (n: string, b: string) => regions.data.rows.find((r) => r.name === n && r.band === b)
    return {
      grid: { left: 70, right: 16, top: 36, bottom: 80 }, legend: { top: 0 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (ps: { axisValue: string; marker: string; seriesName: string; dataIndex: number }[]) =>
        `<b>${ps[0].axisValue.replace('_', ' ')}</b><br/>` + ps.map((p) => { const r = get(names[p.dataIndex], bands.find((b) => BAND_LABEL[b] === p.seriesName)!); return `${p.marker}${p.seriesName}: ${fmtNum(r?.mean)} <span style="color:#5B6B7F">(95 % CI ${fmtNum(r?.ci_low)}–${fmtNum(r?.ci_high)})</span>` }).join('<br/>') },
      xAxis: { type: 'category', data: names.map((n) => n.replace('_', ' ')), axisLabel: { rotate: 35, interval: 0, fontSize: 11 } },
      yAxis: { type: abs ? 'log' : 'value', name: abs ? 'Power (µV², log scale)' : 'Share of total power', nameLocation: 'middle', nameGap: 52, splitLine: { lineStyle: { color: '#EEF2F6' } } },
      series: bands.map((b) => ({ name: BAND_LABEL[b], type: 'bar', data: names.map((n) => get(n, b)?.mean ?? null) })),
    }
  }, [regions.data, names, bands, abs])

  const chNames = useMemo(() => [...new Set((channels.data?.rows ?? []).map((r) => r.name))], [channels.data])
  const cell = (n: string, b: string) => channels.data?.rows.find((r) => r.name === n && r.band === b)
  const maxByBand = useMemo(() => Object.fromEntries(bands.map((b) => [b, Math.max(...(channels.data?.rows ?? []).filter((r) => r.band === b).map((r) => r.mean ?? 0))])), [bands, channels.data])

  return (
    <div className="stack">
      <SegmentedControl w={300} value={kind} onChange={(v) => setKind(v as typeof kind)} data={[{ value: 'rel_power', label: 'Relative power' }, { value: 'abs_power', label: 'Absolute power' }]} />
      <div className="card" style={{ padding: 16 }}>
        {option ? <Chart option={option} height={380} ariaLabel="Band power by brain region" /> : <p className="muted">Loading…</p>}
      </div>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        {abs ? 'Absolute power depends on electrode contact and skull thickness: compare the same patient only, never across people.'
          : 'Relative power is the share of 1–45 Hz power in each band (the five bands add up to 1).'} Hover a bar for its 95 % confidence interval.
      </p>
      <div className="card" style={{ padding: 0, overflow: 'auto', maxHeight: 420 }}>
        <table className="data">
          <thead><tr><th>Channel</th>{bands.map((b) => <th key={b}>{BAND_LABEL[b]}</th>)}</tr></thead>
          <tbody>
            {chNames.map((n) => (
              <tr key={n}>
                <td style={{ fontWeight: 500 }}>{n}</td>
                {bands.map((b) => {
                  const r = cell(n, b)
                  const t = r?.mean != null && maxByBand[b] ? r.mean / maxByBand[b] : 0
                  return <td key={b} title={`95 % CI ${fmtNum(r?.ci_low)}–${fmtNum(r?.ci_high)} · ${r?.n_epochs} epochs`} style={{ background: `rgba(15,140,140,${(0.04 + 0.22 * t).toFixed(2)})` }}>{fmtNum(r?.mean, abs ? 1 : 3)}</td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
