import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

/** Fetch a server-rendered SVG (the API needs the bearer token) and show it as an image (an <img> never runs scripts). */
export default function SvgImage({ path, alt, width = 220 }: { path: string; alt: string; width?: number }) {
  const { data, error } = useQuery({ queryKey: ['svg', path], queryFn: () => api<string>(path), staleTime: 5 * 60_000, retry: false })
  if (error) return <div className="muted" style={{ width, fontSize: 12 }}>{(error as Error).message}</div>
  if (!data) return <div className="muted" style={{ width, height: width * 1.05, display: 'grid', placeItems: 'center', fontSize: 12 }}>Loading…</div>
  return <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(data)}`} alt={alt} style={{ width, height: 'auto', display: 'block' }} />
}
