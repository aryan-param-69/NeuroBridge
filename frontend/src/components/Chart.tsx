import { BarChart, CustomChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkAreaComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'

echarts.use([LineChart, BarChart, CustomChart, GridComponent, TooltipComponent, LegendComponent, MarkAreaComponent, CanvasRenderer])

/** House colours: blue, teal, then muted tones (colour-blind-safe order). Matches the design tokens. */
export const PALETTE = ['#1F5FAE', '#0F8C8C', '#B7791F', '#6B4FA0', '#5B6B7F', '#B42318', '#3F7BC6', '#2FA6A6']

export type EChartsOption = echarts.EChartsCoreOption

export default function Chart({ option, height = 360, ariaLabel }: { option: EChartsOption; height?: number; ariaLabel: string }) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!el.current) return
    const c = echarts.init(el.current, undefined, { renderer: 'canvas' })
    chart.current = c
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(el.current)
    return () => { ro.disconnect(); c.dispose(); chart.current = null }
  }, [])

  useEffect(() => {
    chart.current?.setOption({
      color: PALETTE, animation: false, textStyle: { fontFamily: 'Inter, system-ui, sans-serif', color: '#5B6B7F' },
      ...option,
    }, true)
  }, [option])

  return <div ref={el} role="img" aria-label={ariaLabel} style={{ width: '100%', height }} />
}
