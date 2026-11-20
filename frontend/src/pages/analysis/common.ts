import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { Catalog } from '../../api/types'

export const BANDS = ['delta', 'theta', 'alpha', 'beta', 'gamma_low']
export const BAND_LABEL: Record<string, string> = { delta: 'Delta 1–4', theta: 'Theta 4–8', alpha: 'Alpha 8–13', beta: 'Beta 13–30', gamma_low: 'Gamma-low 30–45' }

export function useCatalog() {
  return useQuery({ queryKey: ['feature-catalog'], queryFn: () => api<Catalog>('/features/catalog'), staleTime: Infinity })
}

export const fmtNum = (v: number | null | undefined, digits = 3) => {
  if (v == null || !Number.isFinite(v)) return '—'
  const a = Math.abs(v)
  return a !== 0 && (a >= 1e4 || a < 1e-3) ? v.toExponential(2) : v.toFixed(digits)
}

// A compact viridis-like colour ramp for heat maps
const STOPS: [number, number, number][] = [[68, 1, 84], [59, 82, 139], [33, 144, 141], [93, 201, 99], [253, 231, 37]]
export function viridis(t: number): string {
  const x = Math.max(0, Math.min(1, t)) * (STOPS.length - 1)
  const i = Math.min(STOPS.length - 2, Math.floor(x)), f = x - i
  const c = STOPS[i].map((v, k) => Math.round(v + (STOPS[i + 1][k] - v) * f))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}
