import { Select } from '@mantine/core'
import { useState } from 'react'
import { BAND_LABEL, BANDS, useCatalog } from './common'
import SvgImage from './SvgImage'

const BANDED = ['rel_power', 'abs_power']

export default function TopomapsTab({ runId, bands }: { runId: string; bands: string[] }) {
  const cat = useCatalog()
  const [feature, setFeature] = useState<string>('rel_power')
  const options = (cat.data?.features ?? []).filter((f) => f.domain !== 'asymmetry' && f.name !== 'wavelet_rel_energy').map((f) => ({ value: f.name, label: f.name.replace(/_/g, ' ') }))
  const banded = BANDED.includes(feature)
  const def = cat.data?.features.find((f) => f.name === feature)
  const list = bands.length ? BANDS.filter((b) => bands.includes(b)) : BANDS
  return (
    <div className="stack">
      <Select label="Feature to map" w={280} searchable allowDeselect={false} value={feature} onChange={(v) => setFeature(v ?? 'rel_power')} data={options} />
      {def && <p className="muted" style={{ margin: 0, fontSize: 13 }}>{def.description} {def.caution && <em>{def.caution}</em>}</p>}
      <div className="card" style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        {banded
          ? list.map((b) => (
            <div key={`${feature}-${b}`}>
              <div style={{ fontWeight: 500, fontSize: 13, textAlign: 'center' }}>{BAND_LABEL[b]} Hz</div>
              <SvgImage path={`/runs/${runId}/topomap?feature=${feature}&band=${b}`} alt={`${feature} in ${b}`} />
            </div>))
          : <SvgImage path={`/runs/${runId}/topomap?feature=${feature}`} alt={feature} width={300} />}
      </div>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        Values are averages over clean epochs, interpolated between electrodes for display. Each map has its own colour scale, so compare shapes, not colours, between maps.
        Absolute power is shown on a log scale. Only electrodes with standard positions are drawn.
      </p>
    </div>
  )
}
