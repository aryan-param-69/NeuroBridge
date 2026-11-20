import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import type { AiListRow, AiModelInfo } from '../../api/types'
import { useAuth } from '../../auth/AuthContext'

export default function AiHome() {
  const { hasRole } = useAuth()
  const models = useQuery({ queryKey: ['ai-models'], queryFn: () => api<{ models: AiModelInfo[]; disclaimer: string }>('/ai/models') })
  const canList = hasRole('clinician', 'eeg_technologist', 'viewer', 'researcher')
  const list = useQuery({ queryKey: ['ai-list'], enabled: canList, queryFn: () => api<{ items: AiListRow[]; pseudonymised: boolean }>('/ai/predictions') })
  return (
    <>
      <h1>AI analysis</h1>
      {models.data && <div className="notice warn">{models.data.disclaimer}</div>}
      <h2>Models</h2>
      {models.data?.models.map((m) => (
        <div className="card" key={m.dir} style={{ marginBottom: 12 }}>
          <strong>{m.name}</strong> <span className="mono">{m.version}</span> <span className="pill role">{m.status}</span> <span className="muted">· {m.model}</span>
          <p style={{ margin: '6px 0' }}>{m.intended_use}</p>
          {m.validation && <p className="muted" style={{ margin: '4px 0' }}>Leave-one-person-out balanced accuracy {m.validation.balanced_accuracy.toFixed(2)} (95 % CI {m.validation.ba_ci95[0].toFixed(2)}–{m.validation.ba_ci95[1].toFixed(2)}), AUC {m.validation.auc.toFixed(2)}, permutation p = {m.validation.permutation_p.toFixed(3)}. {m.training_data && `Trained on ${m.training_data.subjects} ${m.training_data.population}.`}</p>}
          <details><summary>Not for / known limits</summary>
            <ul style={{ margin: '4px 0' }}>{m.not_for.map((x) => <li key={x}>Not for: {x}</li>)}{m.known_limits.map((x) => <li key={x}>{x}</li>)}</ul>
          </details>
          <div className="muted mono" style={{ fontSize: 11 }}>SHA-256 {m.model_sha256}</div>
        </div>
      ))}
      {models.data?.models.length === 0 && <p className="muted">No trained model is installed.</p>}
      {canList && (
        <>
          <h2>Predictions {list.data?.pseudonymised && <span className="muted" style={{ fontSize: 14 }}>(pseudonymised, consented patients only)</span>}</h2>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data">
              <thead><tr><th>Patient</th>{list.data?.pseudonymised ? <th>Days since injury</th> : <th>Created</th>}<th>Condition</th><th>Output</th><th>P(sedated-like)</th><th>Flags</th></tr></thead>
              <tbody>
                {list.data?.items.map((r) => (
                  <tr key={r.id}>
                    <td>{r.patient_id ? <Link to={`/patients/${r.patient_id}`}>{r.patient}</Link> : r.patient}</td>
                    <td>{list.data?.pseudonymised ? (r.days_since_injury != null ? `D+${r.days_since_injury}` : '—') : (r.created_at ?? '').slice(0, 10)}</td>
                    <td>{(r.condition ?? '—').replace(/_/g, ' ')}</td><td>{r.verdict}</td><td className="mono">{Math.round(100 * r.p_sedated)} %</td>
                    <td>{r.unfamiliar && <span className="pill bad" style={{ marginRight: 4 }}>unfamiliar EEG</span>}{r.unreviewed && <span className="pill role">QC pending</span>}</td>
                  </tr>
                ))}
                {list.data?.items.length === 0 && <tr><td colSpan={6} className="muted" style={{ textAlign: 'center', padding: 24 }}>No predictions yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}
