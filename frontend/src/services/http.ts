import { API } from '@/config'

const DEFAULT_TIMEOUT_MS = 30000

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(`Request failed: ${status} ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS)

  try {
    const res = await fetch(`${API}${path}`, {
      ...init,
      credentials: 'include',
      signal: controller.signal,
    })

    if (res.status === 401 && !path.startsWith('/api/auth/')) {
      window.location.href = '/login'
      throw new ApiError(401, 'Unauthorized')
    }

    if (!res.ok) {
      let detail = res.statusText
      try {
        const body = await res.json()
        if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } catch {
        // Response body wasn't JSON — use statusText
      }
      throw new ApiError(res.status, detail)
    }

    if (res.status === 204) {
      return undefined as T
    }

    return (await res.json()) as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(0, 'Request timed out')
    }
    throw error
  } finally {
    clearTimeout(timeout)
  }
}

export function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}
