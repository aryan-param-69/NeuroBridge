import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { AperiodicFit } from '../../api/types'
import { fmtNum } from './common'
import SvgImage from './SvgImage'

export default function AperiodicTab({ runId }: { runId: string }) {
  const { data } = useQuery({ queryKey: ['aperiodic', runId], queryFn: () => api<{ fits: AperiodicFit[]; fit_range_hz: number[]; note: string }>(`/runs/${runId}/aperiodic`) })
  const fits = data?.fits ?? []
  const order = { global: 0, region: 1, channel: 2 } as Record<string, number>
  const sorted = [...fits].sort((a, b) => (order[a.level] ?? 3) - (order[b.level] ?? 3) || a.name.localeCompare(b.name))
  const poor = fits.filter((f) => !f.good_fit).length
  return (
    <div className="stack">
      <div className="notice warn" style={{ margin: 0 }}>
        <b>Research feature.</b> The aperiodic exponent is the slope of the background “1/f” part of the spectrum, separated from rhythm peaks (fit {data?.fit_range_hz?.[0]}–{data?.fit_range_hz?.[1]} Hz).
        Steeper means a more slow-dominated background. It is not a diagnostic measure.{poor > 0 && <> {poor} of {fits.length} fits are unreliable (R² below 0.9) and are marked.</>}
      </div>
      <div className="card" style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div><div style={{ fontWeight: 500, fontSize: 13, textAlign: 'center' }}>Exponent</div><SvgImage path={`/runs/${runId}/topomap?feature=aperiodic_exponent`} alt="Aperiodic exponent map" width={260} /></div>
        <div><div style={{ fontWeight: 500, fontSize: 13, textAlign: 'center' }}>Fit quality (R²)</div><SvgImage path={`/runs/${runId}/topomap?feature=aperiodic_r2`} alt="Fit quality map" width={260} /></div>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'auto', maxHeight: 480 }}>
        <table className="data">
          <thead style={{ position: 'sticky', top: 0 }}><tr><th>Where</th><th>Exponent</th><th>Offset</th><th>R²</th><th>Error</th><th>Fit</th></tr></thead>
          <tbody>{sorted.map((f) => (
            <tr key={`${f.level}-${f.name}`}>
              <td style={{ fontWeight: f.level === 'channel' ? 400 : 600 }}>{f.name.replace('_', ' ')} <span className="muted">· {f.level}</span></td>
              <td>{fmtNum(f.exponent, 2)}</td><td>{fmtNum(f.offset, 2)}</td><td>{fmtNum(f.r2, 3)}</td><td>{fmtNum(f.error, 3)}</td>
              <td><span className={`pill ${f.good_fit ? 'ok' : 'bad'}`}>{f.good_fit ? 'good' : 'unreliable'}</span></td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  )
}
