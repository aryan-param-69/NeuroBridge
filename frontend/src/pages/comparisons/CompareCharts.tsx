import Chart from '../../components/Chart'
import type { CmpItem, FeatureTrend } from '../../api/types'

/** Base vs target with 95 % intervals for the selected items, drawn as paired bars with error whiskers (custom series). */
export function PairedChart({ items, labelOf }: { items: CmpItem[]; labelOf: (i: CmpItem) => string }) {
  const cats = items.map(labelOf)
  const whisk = (key: 'base_ci' | 'target_ci', offset: number) => ({
    type: 'custom', z: 5, silent: true, encode: { x: 0, y: [1, 2] },
    renderItem: (_p: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[] }) => {
      const x = api.value(0), lo = api.coord([x, api.value(1)]), hi = api.coord([x, api.value(2)])
      return { type: 'group', children: [
        { type: 'line', shape: { x1: lo[0] + offset, y1: lo[1], x2: hi[0] + offset, y2: hi[1] }, style: { stroke: '#1B2733', lineWidth: 1.2 } },
        { type: 'line', shape: { x1: lo[0] + offset - 4, y1: lo[1], x2: lo[0] + offset + 4, y2: lo[1] }, style: { stroke: '#1B2733', lineWidth: 1.2 } },
        { type: 'line', shape: { x1: hi[0] + offset - 4, y1: hi[1], x2: hi[0] + offset + 4, y2: hi[1] }, style: { stroke: '#1B2733', lineWidth: 1.2 } },
      ] }
    },
    data: items.map((i, k) => [k, i[key][0] ?? i[key === 'base_ci' ? 'base' : 'target'], i[key][1] ?? i[key === 'base_ci' ? 'base' : 'target']]),
  })
  return (
    <Chart height={Math.max(260, 70 + items.length * 8)} ariaLabel="Base and target values with 95 percent intervals" option={{
      grid: { left: 56, right: 16, top: 30, bottom: 70 }, legend: { top: 0 }, tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: cats, axisLabel: { rotate: 40, fontSize: 10 } }, yAxis: { type: 'value', scale: true },
      series: [
        { name: 'Base', type: 'bar', data: items.map((i) => i.base), barGap: '10%' }, { name: 'Target', type: 'bar', data: items.map((i) => i.target) },
        whisk('base_ci', -9), whisk('target_ci', 9),
      ],
    }} />
  )
}

export function TrendWithCrsr({ t }: { t: FeatureTrend }) {
  const pts = t.points.filter((p) => p.start)
  return (
    <Chart height={320} ariaLabel="Feature over time with CRS-R totals" option={{
      grid: { left: 56, right: 56, top: 40, bottom: 30 }, legend: { top: 0 }, tooltip: { trigger: 'axis' },
      xAxis: { type: 'time' },
      yAxis: [{ type: 'value', scale: true, name: t.points[0]?.unit ?? '', nameTextStyle: { fontSize: 10 } }, { type: 'value', min: 0, max: 23, name: 'CRS-R', splitLine: { show: false } }],
      series: [
        { name: t.feature, type: 'line', symbolSize: 8, data: pts.map((p) => ({ value: [p.start, p.mean], symbol: p.condition === pts[0].condition ? 'circle' : 'diamond' })), lineStyle: { width: 2 } },
        { name: '95 % interval', type: 'custom', silent: true, z: 1, encode: { x: 0, y: [1, 2] },
          renderItem: (_p: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[] }) => {
            const lo = api.coord([api.value(0), api.value(1)]), hi = api.coord([api.value(0), api.value(2)])
            return { type: 'line', shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: { stroke: '#1F5FAE', lineWidth: 2, opacity: 0.45 } }
          },
          data: pts.map((p) => [p.start, p.ci_low ?? p.mean, p.ci_high ?? p.mean]) },
        { name: 'CRS-R total', type: 'line', yAxisIndex: 1, step: 'end', symbol: 'rect', symbolSize: 6, itemStyle: { color: '#0F8C8C' }, lineStyle: { type: 'dashed', width: 1.5 },
          data: t.crsr.map((c) => [c.assessed_at, c.total]) },
      ],
    }} />
  )
}
