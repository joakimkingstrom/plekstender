import { describe, expect, it } from 'vitest'
import { ApiError, errorMessage, unwrap } from './client'

const response = (status: number) => new Response(null, { status })

describe('unwrap', () => {
  it('returns data for successful responses', async () => {
    await expect(unwrap(Promise.resolve({ data: { ok: 1 }, response: response(200) }))).resolves.toEqual({ ok: 1 })
  })

  it('throws ApiError with the server detail', async () => {
    const err = await unwrap(
      Promise.resolve({ error: { detail: 'Nope' }, response: response(403) }),
    ).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(403)
    expect(errorMessage(err)).toBe('Nope')
  })
})
