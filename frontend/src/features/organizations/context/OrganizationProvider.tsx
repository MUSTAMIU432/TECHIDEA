import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { useAuth } from '../../identity/auth/AuthContext'
import {
  createOrganizationRequest,
  organizationsRequest,
  type CreateOrganizationResult,
  type OrganizationMembership,
} from '../api/organizationApi'
import {
  OrganizationContext,
  type CreateOrganizationOutcome,
  type OrganizationContextValue,
  type OrganizationStatus,
} from './OrganizationContext'

interface OrganizationProviderProps {
  children: ReactNode
}

function failureResult(message: string, field: string | null = null): CreateOrganizationOutcome {
  return { success: false, message, field }
}

function successfulResult(result: CreateOrganizationResult): CreateOrganizationOutcome {
  return { success: true, message: result.message, field: null }
}

export function OrganizationProvider({ children }: OrganizationProviderProps) {
  const { status: authStatus, user: authUser } = useAuth()
  const authUserId = authUser?.id ?? null
  const authSessionRef = useRef({ status: authStatus, userId: authUserId })
  const [status, setStatus] = useState<OrganizationStatus>('idle')
  const [memberships, setMemberships] = useState<OrganizationMembership[]>([])
  const [loadedUserId, setLoadedUserId] = useState<string | null>(null)
  const [activeOrganizationId, setActiveOrganizationId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const requestVersionRef = useRef(0)
  const loadPromiseRef = useRef<Promise<OrganizationMembership[]> | null>(null)
  const loadUserIdRef = useRef<string | null>(null)

  useEffect(() => {
    authSessionRef.current = { status: authStatus, userId: authUserId }
  }, [authStatus, authUserId])

  const loadOrganizations = useCallback((): Promise<OrganizationMembership[]> => {
    if (authStatus !== 'authenticated' || !authUserId) return Promise.resolve([])
    if (loadPromiseRef.current && loadUserIdRef.current === authUserId)
      return loadPromiseRef.current

    const requestVersion = requestVersionRef.current + 1
    requestVersionRef.current = requestVersion
    setStatus('loading')
    setLoadedUserId(authUserId)
    setMemberships([])
    setActiveOrganizationId(null)
    setError(null)

    const request = organizationsRequest()
      .then((nextMemberships) => {
        if (requestVersionRef.current === requestVersion) {
          setMemberships(nextMemberships)
          setActiveOrganizationId((currentId) => {
            if (currentId && nextMemberships.some((item) => item.organization.id === currentId)) {
              return currentId
            }
            return nextMemberships[0]?.organization.id ?? null
          })
          setStatus('ready')
        }
        return nextMemberships
      })
      .catch((requestError: unknown) => {
        if (requestVersionRef.current === requestVersion) {
          setStatus('error')
          setError('We could not load your organizations. Please try again.')
        }
        throw requestError
      })
      .finally(() => {
        if (loadPromiseRef.current === request) {
          loadPromiseRef.current = null
          loadUserIdRef.current = null
        }
      })

    loadPromiseRef.current = request
    loadUserIdRef.current = authUserId
    return request
  }, [authStatus, authUserId])

  useEffect(() => {
    if (authStatus !== 'authenticated') {
      requestVersionRef.current += 1
      loadPromiseRef.current = null
      loadUserIdRef.current = null
      return
    }

    void loadOrganizations().catch(() => undefined)
  }, [authStatus, loadOrganizations])

  const setActiveOrganization = useCallback(
    (organizationId: string) => {
      if (memberships.some((item) => item.organization.id === organizationId)) {
        setActiveOrganizationId(organizationId)
      }
    },
    [memberships],
  )

  const createOrganization = useCallback(
    async (name: string, slug?: string): Promise<CreateOrganizationOutcome> => {
      const requestUserId = authUserId
      try {
        const result = await createOrganizationRequest({
          name,
          ...(slug ? { slug } : {}),
        })
        if (
          authSessionRef.current.status !== 'authenticated' ||
          authSessionRef.current.userId !== requestUserId
        ) {
          return failureResult('Your session changed. Please try again.')
        }
        if (!result.success || !result.organization || !result.membership) {
          return failureResult(result.message, result.field)
        }

        requestVersionRef.current += 1
        loadPromiseRef.current = null
        loadUserIdRef.current = null

        const newMembership: OrganizationMembership = {
          organization: result.organization,
          membership: result.membership,
        }
        setMemberships((current) => [
          newMembership,
          ...current.filter((item) => item.organization.id !== newMembership.organization.id),
        ])
        setActiveOrganizationId(newMembership.organization.id)
        setError(null)
        setStatus('ready')
        return successfulResult(result)
      } catch {
        return failureResult('We could not create the organization. Please try again.')
      }
    },
    [authUserId],
  )

  const reload = useCallback(async () => {
    try {
      await loadOrganizations()
    } catch {
      return
    }
  }, [loadOrganizations])

  const hasCurrentSession =
    authStatus === 'authenticated' && authUserId !== null && loadedUserId === authUserId
  const visibleMemberships = useMemo(
    () => (hasCurrentSession ? memberships : []),
    [hasCurrentSession, memberships],
  )
  const visibleStatus =
    authStatus !== 'authenticated' ? 'idle' : hasCurrentSession ? status : 'loading'
  const activeMembership = useMemo(
    () => visibleMemberships.find((item) => item.organization.id === activeOrganizationId) ?? null,
    [activeOrganizationId, visibleMemberships],
  )
  const hasPermission = useCallback(
    (permissionCode: string) =>
      activeMembership?.membership.roles.some((role) =>
        role.permissions.some((permission) => permission.code === permissionCode),
      ) ?? false,
    [activeMembership],
  )

  const value = useMemo<OrganizationContextValue>(
    () => ({
      status: visibleStatus,
      memberships: visibleMemberships,
      activeOrganization: activeMembership?.organization ?? null,
      activeMembership: activeMembership?.membership ?? null,
      error: hasCurrentSession ? error : null,
      hasPermission,
      createOrganization,
      setActiveOrganization,
      reload,
    }),
    [
      activeMembership,
      createOrganization,
      error,
      hasCurrentSession,
      hasPermission,
      reload,
      setActiveOrganization,
      visibleMemberships,
      visibleStatus,
    ],
  )

  return <OrganizationContext.Provider value={value}>{children}</OrganizationContext.Provider>
}
