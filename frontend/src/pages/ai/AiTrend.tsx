import Chart from '../../components/Chart'
import type { AiPatient } from '../../api/types'

export default function AiTrend({ data }: { data: AiPatient }) {
  const pts = data.items.filter((p) => p.recording_start ?? p.created_at)
  return (
    <Chart height={280} ariaLabel="Model output over time with CRS-R totals" option={{
      grid: { left: 52, right: 52, top: 36, bottom: 30 }, legend: { top: 0 }, tooltip: { trigger: 'axis' }, xAxis: { type: 'time' },
      yAxis: [{ type: 'value', min: 0, max: 1, name: 'P(sedated-like)', nameTextStyle: { fontSize: 10 } },
        { type: 'value', min: 0, max: 23, name: 'CRS-R', splitLine: { show: false } }],
      series: [
        { name: 'P(sedated-like)', type: 'line', symbolSize: 9, lineStyle: { width: 2 },
          data: pts.map((p) => ({ value: [p.recording_start ?? p.created_at, p.p_sedated], symbol: p.unfamiliar ? 'emptyCircle' : 'circle' })) },
        { name: 'CRS-R total', type: 'line', yAxisIndex: 1, step: 'end', symbol: 'rect', symbolSize: 6, itemStyle: { color: '#0F8C8C' }, lineStyle: { type: 'dashed', width: 1.5 },
          data: data.crsr.map((c) => [c.assessed_at, c.total]) },
      ],
    }} />
  )
}
