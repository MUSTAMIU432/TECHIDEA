import { useEffect, useState } from 'react'

import { categoriesRequest, type IdeaCategory } from '../api/ideasApi'

/**
 * The categories offered by the discovery filters.
 *
 * Fetched once per mount rather than on every list fetch, because the category
 * list is reference data: it does not change when an idea is saved, and
 * re-requesting it after each write would be a second request per write to
 * keep a constant current.
 *
 * `loadError` is reported but not raised. A failure here leaves the filter
 * usable — "All categories" is still a valid choice and ideas still list — so
 * turning it into an error panel would tell a reader who never asked for a
 * category that the server was down. The control stays rendered and simply
 * offers fewer options.
 */
export function useCategories(): { categories: IdeaCategory[]; loadError: boolean } {
  const [categories, setCategories] = useState<IdeaCategory[]>([])
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    let cancelled = false
    categoriesRequest()
      .then((loaded) => {
        if (!cancelled) setCategories(loaded)
      })
      .catch(() => {
        if (!cancelled) setLoadError(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { categories, loadError }
}
