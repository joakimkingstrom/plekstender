import createClient, { type Middleware } from 'openapi-fetch'
import type { components, paths } from './schema'

export type Schemas = components['schemas']

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// State-changing requests must carry this header (see backend CSRFMiddleware).
const csrf: Middleware = {
  onRequest({ request }) {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
      request.headers.set('X-RoadieFM', '1')
    }
    return request
  },
}

export const api = createClient<paths>({ baseUrl: '', credentials: 'same-origin' })
api.use(csrf)

function detailOf(error: unknown): string | undefined {
  if (error && typeof error === 'object' && 'detail' in error) {
    const { detail } = error as { detail: unknown }
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return 'The request was invalid.'
  }
  return undefined
}

/** Turns an openapi-fetch result into data or a thrown ApiError. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise
  if (!response.ok) {
    throw new ApiError(response.status, detailOf(error) ?? `Request failed (${response.status})`)
  }
  return data as T
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return 'Something went wrong. Check your connection and try again.'
  return 'Something went wrong.'
}
