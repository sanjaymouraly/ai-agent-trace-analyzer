/**
 * Typed API client.
 *
 * The bearer token is kept in sessionStorage only: it is never written to a
 * build artifact, a log, or a persisted profile.
 */

const TOKEN_KEY = 'tracelens.token'

export class ApiError extends Error {
  status: number
  code: string
  payload: unknown

  constructor(message: string, status: number, code: string, payload: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.payload = payload
  }
}

export function getToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable — the token simply is not persisted */
  }
}

export function hasToken(): boolean {
  return getToken().length > 0
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
  raw?: boolean
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  if (!query) return path
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    params.set(key, String(value))
  })
  const search = params.toString()
  return search ? `${path}?${search}` : path
}

export async function api<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, raw, headers, ...rest } = options
  const requestHeaders = new Headers(headers || {})
  const token = getToken()
  if (token) requestHeaders.set('Authorization', `Bearer ${token}`)
  let payloadBody: BodyInit | undefined
  if (body instanceof FormData) {
    payloadBody = body
  } else if (body !== undefined) {
    requestHeaders.set('Content-Type', 'application/json')
    payloadBody = JSON.stringify(body)
  }
  const response = await fetch(buildUrl(path, query), { ...rest, headers: requestHeaders, body: payloadBody })
  if (raw) {
    if (!response.ok) {
      throw new ApiError(`Request failed (${response.status})`, response.status, 'http_error', null)
    }
    return (await response.blob()) as T
  }
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    const detail = (data as { error?: { code?: string; message?: string } })?.error
    throw new ApiError(
      detail?.message || `Request failed (${response.status})`,
      response.status,
      detail?.code || 'http_error',
      data,
    )
  }
  return data as T
}

export function downloadFile(path: string, filename: string): Promise<void> {
  return api<Blob>(path, { raw: true }).then((blob) => {
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  })
}
