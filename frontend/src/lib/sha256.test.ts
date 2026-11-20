import { describe, expect, it } from 'vitest'
import { Sha256 } from './sha256'

const subtle = async (bytes: Uint8Array) =>
  Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as BufferSource)),
    (b) => b.toString(16).padStart(2, '0')).join('')

describe('streaming SHA-256', () => {
  it('matches the standard test vectors', () => {
    expect(new Sha256().update(new TextEncoder().encode('abc')).hex())
      .toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    expect(new Sha256().hex())
      .toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
  })

  it('equals WebCrypto for many sizes, including block-boundary cases, however the data is split', async () => {
    for (const n of [1, 55, 56, 57, 63, 64, 65, 119, 120, 1000, 65537, 300000]) {
      const data = new Uint8Array(n).map((_, i) => (i * 31 + n) & 255)
      const expected = await subtle(data)
      expect(new Sha256().update(data).hex(), `one piece, n=${n}`).toBe(expected)
      const s = new Sha256()
      for (let i = 0; i < n; i += 7919) s.update(data.subarray(i, Math.min(n, i + 7919)))
      expect(s.hex(), `chunked, n=${n}`).toBe(expected)
    }
  })
})
