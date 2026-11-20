import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, setAccessToken } from './client'

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => { vi.restoreAllMocks(); setAccessToken(null) })

describe('api client', () => {
  it('sends the bearer token', async () => {
    setAccessToken('abc')
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(200, { ok: true }))
    await api('/health')
    const headers = spy.mock.calls[0][1]!.headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer abc')
  })

  it('refreshes once on 401 and retries', async () => {
    const spy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json(401, { detail: 'Not authenticated' }))
      .mockResolvedValueOnce(json(200, { access_token: 'new' }))
      .mockResolvedValueOnce(json(200, { hello: 'world' }))
    await expect(api('/version')).resolves.toEqual({ hello: 'world' })
    expect(spy.mock.calls[1][0]).toBe('/api/v1/auth/refresh')
  })

  it('keeps structured error details (duplicate-patient warning)', async () => {
    const detail = { message: 'May already be registered', duplicates: [{ display_id: 'NB-2026-000001' }] }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(409, { detail }))
    await expect(api('/patients')).rejects.toMatchObject({ status: 409, message: 'May already be registered', detail })
  })

  it('surfaces server error messages', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(409, { detail: 'Already exists' }))
    await expect(api('/x')).rejects.toMatchObject({ status: 409, message: 'Already exists' })
    await expect(api('/x')).rejects.toBeInstanceOf(ApiError)
  })
})
