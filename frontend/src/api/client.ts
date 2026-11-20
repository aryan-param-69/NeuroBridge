// Access token lives in memory only (never localStorage). The refresh token is an httpOnly cookie.
let accessToken: string | null = null
let refreshing: Promise<boolean> | null = null

export const setAccessToken = (t: string | null) => { accessToken = t }

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function messageFrom(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown })?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === 'string' ? d : (d as { msg?: string }).msg ?? '')).join('; ')
  }
  return fallback
}

async function tryRefresh(): Promise<boolean> {
  refreshing ??= fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'include' })
    .then(async (r) => {
      if (!r.ok) return false
      accessToken = (await r.json()).access_token
      return true
    })
    .catch(() => false)
    .finally(() => { refreshing = null })
  return refreshing
}

export async function api<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)

  const res = await fetch(`/api/v1${path}`, { ...init, headers, credentials: 'include' })
  if (res.status === 401 && retry && !path.startsWith('/auth/') && (await tryRefresh())) {
    return api<T>(path, init, false)
  }
  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') ?? ''
  if (res.ok && (ct.includes('image/svg') || ct.includes('text/csv') || ct.includes('text/html'))) return (await res.text()) as T  // SVG maps, CSV text
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, messageFrom(body, res.statusText), (body as { detail?: unknown })?.detail)
  return body as T
}

export const post = <T>(path: string, data?: unknown) =>
  api<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) })

/** Restore a session on page load using the refresh cookie. */
export const restoreSession = () => tryRefresh()

export const patch = <T>(path: string, data: unknown) =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(data) })

export const put = <T>(path: string, data: unknown) =>
  api<T>(path, { method: 'PUT', body: JSON.stringify(data) })

export const del = (path: string) => api<void>(path, { method: 'DELETE' })

/** Download a protected file (the API needs the bearer token, so a plain link cannot be used). */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const text = await api<string>(path)
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}
