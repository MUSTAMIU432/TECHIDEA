import { useEffect, useState } from 'react'

import { fetchAvatarBlob, type AuthUser } from '../auth/authApi'
import { initialsFor } from './initials'

/**
 * The user's photo, or their initials while there is none or it is loading.
 * The photo is fetched with the bearer token and shown from a blob URL, because
 * an `<img src>` cannot send an `Authorization` header. The URL carries a
 * version that changes with every new photo, so it is refetched exactly then.
 */
export function Avatar({ user, className }: { user: AuthUser | null; className: string }) {
  const avatarUrl = user?.avatarUrl ?? null
  const [loaded, setLoaded] = useState<{ source: string; src: string } | null>(null)

  useEffect(() => {
    if (!avatarUrl) return
    let objectUrl: string | null = null
    let cancelled = false
    fetchAvatarBlob(avatarUrl)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setLoaded({ source: avatarUrl, src: objectUrl })
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [avatarUrl])

  const src = avatarUrl && loaded && loaded.source === avatarUrl ? loaded.src : null

  return (
    <span className={`${className} overflow-hidden`}>
      {src ? <img src={src} alt="" className="h-full w-full object-cover" /> : initialsFor(user)}
    </span>
  )
}
