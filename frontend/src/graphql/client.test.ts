import { afterEach, describe, expect, it, vi } from 'vitest'

import { graphqlClient } from './client'
import { setAccessToken } from './tokenStore'

// No backend is contacted: fetch is stubbed, so this stays a unit test.
describe('graphqlClient', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    setAccessToken(null)
  })

  function stubOkResponse(data: unknown = {}) {
    const fetchMock = vi.fn(async () =>
      Response.json({ data }, { headers: { 'content-type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  function requestInit(fetchMock: ReturnType<typeof stubOkResponse>): RequestInit {
    const [, init] = fetchMock.mock.calls[0] as unknown as [URL | string, RequestInit]
    return init
  }

  it('posts operations to the configured GraphQL URL and returns data', async () => {
    const fetchMock = stubOkResponse({ apiStatus: { status: 'ok' } })

    const data = await graphqlClient.request('{ apiStatus { status } }')

    expect(data).toEqual({ apiStatus: { status: 'ok' } })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL | string, RequestInit]
    expect(String(url)).toBe('http://localhost:8000/graphql/')
    expect(init.method).toBe('POST')
  })

  it('sends requests with credentials included, for the refresh-token cookie', async () => {
    const fetchMock = stubOkResponse()

    await graphqlClient.request('{ apiStatus { status } }')

    expect(requestInit(fetchMock).credentials).toBe('include')
  })

  it('includes credentials on every request, not only the first', async () => {
    // The refresh cookie is not a one-time thing at the transport layer: it
    // is re-sent on every call and rotated by the responses. A client that
    // attached it to the first request only would fail on the second.
    const fetchMock = stubOkResponse()

    await graphqlClient.request('{ apiStatus { status } }')
    await graphqlClient.request('{ apiStatus { status } }')

    for (const call of fetchMock.mock.calls) {
      const [, init] = call as unknown as [URL | string, RequestInit]
      expect(init.credentials).toBe('include')
    }
  })

  it('includes credentials while an access token is held too', async () => {
    // The two travel together: the Authorization header identifies the
    // request, the cookie carries the session that can renew it. Dropping the
    // cookie whenever a token is present would make the app unable to
    // refresh without first throwing the token away.
    const fetchMock = stubOkResponse()
    setAccessToken('the-access-token')

    await graphqlClient.request('{ apiStatus { status } }')

    const init = requestInit(fetchMock)
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer the-access-token')
  })

  it('attaches no Authorization header when there is no access token', async () => {
    const fetchMock = stubOkResponse()

    await graphqlClient.request('{ apiStatus { status } }')

    const headers = new Headers(requestInit(fetchMock).headers)
    expect(headers.has('Authorization')).toBe(false)
  })

  it('attaches the current access token as a Bearer Authorization header', async () => {
    const fetchMock = stubOkResponse()
    setAccessToken('the-access-token')

    await graphqlClient.request('{ apiStatus { status } }')

    const headers = new Headers(requestInit(fetchMock).headers)
    expect(headers.get('Authorization')).toBe('Bearer the-access-token')
  })

  it('reads the token fresh per request, so a refresh is picked up immediately', async () => {
    // `headers` is a function, not a value: a value would be evaluated once
    // at client construction and every request after a refresh would go out
    // with the token that happened to be current at that moment.
    const fetchMock = stubOkResponse()
    setAccessToken('first-token')

    await graphqlClient.request('{ apiStatus { status } }')
    setAccessToken('second-token')
    await graphqlClient.request('{ apiStatus { status } }')

    const first = new Headers(
      (fetchMock.mock.calls[0] as unknown as [URL | string, RequestInit])[1].headers,
    )
    const second = new Headers(
      (fetchMock.mock.calls[1] as unknown as [URL | string, RequestInit])[1].headers,
    )
    expect(first.get('Authorization')).toBe('Bearer first-token')
    expect(second.get('Authorization')).toBe('Bearer second-token')
  })

  it('stops attaching the header once the token is cleared', async () => {
    const fetchMock = stubOkResponse()
    setAccessToken('first-token')
    await graphqlClient.request('{ apiStatus { status } }')

    setAccessToken(null)
    await graphqlClient.request('{ apiStatus { status } }')

    const second = new Headers(
      (fetchMock.mock.calls[1] as unknown as [URL | string, RequestInit])[1].headers,
    )
    expect(second.has('Authorization')).toBe(false)
  })

  it('never sends the refresh credential as a header or body field', async () => {
    // The credential is an HttpOnly cookie: JavaScript cannot read it, so
    // this is really an assertion about the design - there is no code path
    // that could forward it, because there is nothing to forward.
    const fetchMock = stubOkResponse()
    setAccessToken('the-access-token', '2099-01-01T00:00:00.000Z')

    await graphqlClient.request('mutation { logout { success } }')

    const init = requestInit(fetchMock)
    const headers = new Headers(init.headers)
    for (const [, value] of headers.entries()) {
      expect(value).not.toMatch(/refresh/i)
    }
    expect(String(init.body)).not.toMatch(/refresh/i)
  })

  it('sends JSON, so a browser must preflight a cross-origin call', async () => {
    // Content-Type: application/json is not a CORS-simple content type, so a
    // browser asks permission before sending. That preflight - not a CSRF
    // token - is what stops a hostile page from getting a response it can
    // read. Asserted here because it is a property of what the client
    // sends, and it is what makes backend's csrf_exempt defensible.
    const fetchMock = stubOkResponse()

    await graphqlClient.request('{ apiStatus { status } }')

    const headers = new Headers(requestInit(fetchMock).headers)
    expect(headers.get('Content-Type')).toBe('application/json')
  })
})
