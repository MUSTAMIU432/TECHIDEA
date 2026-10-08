import { parse, type FieldNode, type SelectionSetNode } from 'graphql'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { graphqlClient } from '../../../graphql/client'
import {
  createOrganizationRequest,
  organizationsRequest,
  type CreateOrganizationResult,
  type OrganizationMembership,
} from './organizationApi'

/**
 * The contract these tests pin the request documents to.
 *
 * The GraphQL schema is defined in the Django backend (Python), so there is
 * no way for this suite to import it and validate a document against it
 * directly. What *is* practical is to pin both ends of the contract from
 * each side: this file asserts the frontend selects exactly these field
 * names, and `backend/tests/test_frontend_schema_contract.py` asserts the
 * backend's schema declares exactly these field names. A rename on either
 * side therefore fails one of the two suites, rather than surfacing at
 * runtime as a GraphQL `errors` array and an empty screen.
 *
 * All names are camelCase: Strawberry converts the Python snake_case field
 * names on the way out, so asking for `created_at` is a query error.
 */
const CONTRACT = {
  OrganizationType: ['createdAt', 'id', 'name', 'slug', 'updatedAt'],
  MembershipType: ['createdAt', 'id', 'organization', 'roles', 'status', 'updatedAt', 'user'],
  UserType: ['email', 'firstName', 'id', 'lastName'],
  RoleType: [
    'createdAt',
    'description',
    'id',
    'isSystem',
    'name',
    'organization',
    'permissions',
    'slug',
    'updatedAt',
  ],
  PermissionType: ['code', 'createdAt', 'description', 'id', 'name', 'updatedAt'],
  CreateOrganizationPayload: ['field', 'membership', 'message', 'organization', 'success'],
  OrganizationMembershipType: ['membership', 'organization'],
} as const

function stubFetch(data: unknown) {
  const fetchMock = vi.fn(async () =>
    Response.json({ data }, { headers: { 'content-type': 'application/json' } }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function lastRequest(fetchMock: ReturnType<typeof stubFetch>) {
  const [url, init] = fetchMock.mock.calls.at(-1) as unknown as [URL | string, RequestInit]
  return {
    url: String(url),
    body: JSON.parse(String(init.body)) as {
      query: string
      operationName?: string
      variables?: unknown
    },
  }
}

// No backend is contacted: fetch is stubbed, so these stay unit tests that
// still exercise the real documents and the real shared client.
describe('organizationApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const ORGANIZATION = {
    id: '1',
    name: 'Acme',
    slug: 'acme',
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
  const USER = {
    id: '1',
    email: 'ada@example.com',
    firstName: 'Ada',
    lastName: 'Lovelace',
  }
  const MEMBERSHIP = {
    id: '10',
    status: 'active' as const,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    user: USER,
    organization: ORGANIZATION,
    roles: [],
  }

  // --- the query the rest of the app depends on ----------------------------

  it('returns the organizations the backend reports', async () => {
    const membership: OrganizationMembership[] = [
      { organization: ORGANIZATION, membership: MEMBERSHIP },
    ]
    stubFetch({ meOrganizations: membership })

    const result = await organizationsRequest()

    expect(result).toEqual(membership)
  })

  it('requests exactly the operation it means to', async () => {
    const fetchMock = stubFetch({ meOrganizations: [] })

    await organizationsRequest()

    const { url, body } = lastRequest(fetchMock)
    expect(url).toBe('http://localhost:8000/graphql/')
    // graphql-request adds `operationName` when the document names one; a
    // named operation is what makes a server-side trace readable, so it is
    // worth asserting rather than tolerating.
    expect(Object.keys(body).sort()).toEqual(['operationName', 'query'])
    expect(body.operationName).toBe('MeOrganizations')
    expect(body.query).toContain('query MeOrganizations')
    expect(body.query).toContain('meOrganizations')
  })

  // --- the schema contract -------------------------------------------------

  it('selects exactly the fields the backend schema declares', async () => {
    const fetchMock = stubFetch({ meOrganizations: [] })

    await organizationsRequest()

    expect(fieldsPerResponseKey(bodyQuery(fetchMock))).toEqual({
      OrganizationMembershipType: [...CONTRACT.OrganizationMembershipType].sort(),
      OrganizationType: [...CONTRACT.OrganizationType].sort(),
      MembershipType: [...CONTRACT.MembershipType].sort(),
      RoleType: [...CONTRACT.RoleType].sort(),
      UserType: [...CONTRACT.UserType].sort(),
      PermissionType: [...CONTRACT.PermissionType].sort(),
    })
  })

  it('selects exactly the fields the backend schema declares for createOrganization', async () => {
    const fetchMock = stubFetch({
      createOrganization: {
        success: true,
        message: 'ok',
        field: null,
        organization: ORGANIZATION,
        membership: MEMBERSHIP,
      } satisfies CreateOrganizationResult,
    })

    await createOrganizationRequest({ name: 'Acme' })

    const selected = fieldsPerResponseKey(bodyQuery(fetchMock))
    expect(selected.CreateOrganizationPayload).toEqual(
      [...CONTRACT.CreateOrganizationPayload].sort(),
    )
  })

  it('uses the camelCase field names the schema exposes', async () => {
    const fetchMock = stubFetch({ meOrganizations: [] })

    await organizationsRequest()

    const query = bodyQuery(fetchMock)
    // Strawberry converts snake_case Python fields to camelCase on the way
    // out; asking for `created_at` would be a query error at runtime.
    expect(query).toContain('createdAt')
    expect(query).not.toMatch(/\bcreated_at\b/)
    expect(query).not.toMatch(/\bfirst_name\b/)
    expect(query).not.toMatch(/\bis_system\b/)
    expect(query).not.toMatch(/\bprovider_subject\b/)
  })

  it('sends a document GraphQL can parse', async () => {
    const fetchMock = stubFetch({ meOrganizations: [] })

    await organizationsRequest()

    // A syntax error would otherwise only be found at runtime, as an opaque
    // transport error rather than a contract mismatch.
    expect(() => parse(bodyQuery(fetchMock))).not.toThrow()
  })

  it('sends CreateOrganizationInput under the name the schema declares', async () => {
    const fetchMock = stubFetch({
      createOrganization: {
        success: true,
        message: 'ok',
        field: null,
        organization: ORGANIZATION,
        membership: MEMBERSHIP,
      } satisfies CreateOrganizationResult,
    })

    await createOrganizationRequest({ name: 'Acme', slug: 'acme' })

    const { body } = lastRequest(fetchMock)
    expect(body.variables).toEqual({ input: { name: 'Acme', slug: 'acme' } })
    expect(body.query).toContain('$input: CreateOrganizationInput!')
    expect(body.query).toContain('createOrganization')
  })

  it('passes a GraphQL error through rather than pretending the result is empty', async () => {
    // The whole reason the field-level assertions above exist: a schema
    // mismatch surfaces as an `errors` array, and swallowing it as "no
    // organizations" would show the user an empty list instead of a bug.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json(
          {
            errors: [
              {
                message: 'Cannot query field "bogus" on type "OrganizationType".',
              },
            ],
          },
          { headers: { 'content-type': 'application/json' } },
        ),
      ),
    )

    await expect(organizationsRequest()).rejects.toThrow(/Cannot query field/)
  })

  it('never stores organization or membership data in browser storage', async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    const membership: OrganizationMembership[] = [
      { organization: ORGANIZATION, membership: MEMBERSHIP },
    ]
    stubFetch({ meOrganizations: membership })

    await organizationsRequest()

    // Authorization decisions are the backend's; caching them in the browser
    // would let a stale or tampered copy outlive the decision.
    expect(setItemSpy).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    setItemSpy.mockRestore()
  })

  it('goes through the shared client, so credentials and auth are applied', async () => {
    const fetchMock = stubFetch({ meOrganizations: [] })

    await organizationsRequest()

    // Proves organizationApi has no client of its own to forget to
    // configure: the refresh cookie and the bearer token are the shared
    // client's business, applied because the request came through it.
    const [, init] = fetchMock.mock.calls[0] as unknown as [URL | string, RequestInit]
    expect(init.credentials).toBe('include')
    expect(graphqlClient).toBeDefined()
  })
})

function bodyQuery(fetchMock: ReturnType<typeof stubFetch>): string {
  return lastRequest(fetchMock).body.query
}

/**
 * Every field selected beneath each response key in a GraphQL document,
 * flattened across nested selection sets and keyed by the response key whose
 * type those fields are selected on.
 *
 * The response key is mapped to the type the backend declares for it
 * (OrganizationType for `organization`, and so on) using the same mapping the
 * contract above is written in - that mapping is the part the backend suite
 * pins from the other end.
 */
const RESPONSE_KEY_TYPES: Record<string, keyof typeof CONTRACT> = {
  organization: 'OrganizationType',
  membership: 'MembershipType',
  user: 'UserType',
  roles: 'RoleType',
  role: 'RoleType',
  permissions: 'PermissionType',
  meOrganizations: 'OrganizationMembershipType',
  createOrganization: 'CreateOrganizationPayload',
}

function fieldsPerResponseKey(document: string): Record<string, string[]> {
  const parsed = parse(document)
  const collected: Record<string, Set<string>> = {}

  const visit = (selectionSet: SelectionSetNode) => {
    for (const selection of selectionSet.selections) {
      if (selection.kind !== 'Field') continue
      const field = selection as FieldNode
      const type = RESPONSE_KEY_TYPES[field.name.value]
      if (type) {
        collected[type] ??= new Set()
        for (const child of field.selectionSet?.selections ?? []) {
          if (child.kind === 'Field') {
            collected[type].add((child as FieldNode).name.value)
          }
        }
      }
      if (field.selectionSet) visit(field.selectionSet)
    }
  }

  for (const definition of parsed.definitions) {
    if (definition.kind === 'OperationDefinition' && definition.selectionSet) {
      visit(definition.selectionSet)
    }
  }

  return Object.fromEntries(
    Object.entries(collected)
      .filter(([, names]) => names.size > 0)
      .map(([key, names]) => [key, [...names].sort()]),
  )
}
