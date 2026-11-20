import Chart from '../../components/Chart'
import type { CrsrDefinition, TrendResponse } from '../../api/types'

/** Small multiples: the total and each subscale over time, one panel each, shared time axis. Lazy-loaded (ECharts). */
export default function TrendChart({ trend, def }: { trend: TrendResponse; def: CrsrDefinition }) {
  const panels = [{ key: 'total', label: `Total (0–${trend.max_total})`, max: trend.max_total }, ...def.subscales.map((s) => ({ key: s.key, label: s.label, max: s.max }))]
  const t = trend.points.map((p) => p.assessed_at)
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
      {panels.map((pn) => (
        <div key={pn.key} className="card" style={{ padding: 12 }}>
          <div style={{ fontWeight: 600, fontSize: 13 }}>{pn.label}</div>
          <Chart height={140} ariaLabel={`${pn.label} over time`} option={{
            grid: { left: 28, right: 8, top: 10, bottom: 22 },
            tooltip: { trigger: 'axis', valueFormatter: (v: number) => String(v) },
            xAxis: { type: 'time' },
            yAxis: { type: 'value', min: 0, max: pn.max, minInterval: 1, splitNumber: Math.min(pn.max, 4) },
            series: [{
              type: 'line', symbolSize: 7, lineStyle: { width: 2 },
              // Hollow marker when sedation or confounders may have lowered the score (descriptive, not an adjustment).
              data: trend.points.map((p, i) => ({
                value: [t[i], pn.key === 'total' ? p.total : p.scores[pn.key]],
                symbol: p.sedation_status === 'reduced' || p.sedation_status === 'ongoing' ? 'emptyCircle' : 'circle',
              })),
            }],
          }} />
        </div>
      ))}
    </div>
  )
}
