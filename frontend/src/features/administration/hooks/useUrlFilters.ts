import { useCallback, useMemo } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'

/**
 * Filters and the page offset kept in the URL, so a filtered view can be
 * linked to (the dashboard's status counts, "this organization's ideas") and
 * survives a reload. Changing any filter returns to the first page.
 *
 * Also returns `here`: this exact view - path, filters and page - as the link
 * state a row passes to the page it opens. That is what lets a detail page's
 * "back" return to the filtered, paged list the reader actually came from
 * rather than to the list's front door (see `useBackTarget`). It lives here
 * because this hook already knows the URL, and a second place asking for it is a
 * second place to forget the query string.
 */
export function useUrlFilters() {
  const [params, setParams] = useSearchParams()
  const { pathname, search } = useLocation()

  const get = useCallback((name: string): string => params.get(name) ?? '', [params])
  const offset = Number.parseInt(params.get('offset') ?? '0', 10) || 0

  const set = useCallback(
    (name: string, value: string) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current)
          if (value) next.set(name, value)
          else next.delete(name)
          next.delete('offset')
          return next
        },
        { replace: true },
      )
    },
    [setParams],
  )

  const setOffset = useCallback(
    (value: number) => {
      setParams((current) => {
        const next = new URLSearchParams(current)
        if (value > 0) next.set('offset', String(value))
        else next.delete('offset')
        return next
      })
    },
    [setParams],
  )

  /*
    One object, built once per URL: passing a fresh `{ from }` literal at twenty
    row links would make every row's props a new object on every render, and the
    point of putting it here is that the list and the row agree on one value.
  */
  const here = useMemo(() => ({ from: `${pathname}${search}` }), [pathname, search])

  return { get, set, offset, setOffset, key: params.toString(), here }
}
