import { useCallback, useEffect, useRef, useState } from 'react'

import { removeVoteRequest, voteIdeaRequest } from '../api/ideasApi'
import type { Idea } from '../api/ideasApi'

/**
 * One idea's vote control: how many votes, whether this reader cast one, and
 * whether a request is in flight.
 *
 * Keyed by idea id rather than held as a single value, and that is the whole
 * race-safety story. A page of ideas has many controls at once, so a response
 * that arrives late writes the key of the idea it was *for* - it cannot land on
 * whichever idea the reader is looking at now. A single `voted` boolean would
 * be wrong the moment two ideas are in flight together.
 *
 * **No optimistic arithmetic.** The count this client renders after a vote is
 * the number the mutation returned, not `previous + 1`, because the server's
 * answer already accounts for concurrent votes from other people, the
 * idempotency rule, and anything this client does not know. A failure leaves
 * the displayed state exactly as it was, which is also why there is nothing to
 * roll back: nothing was guessed.
 */
export interface IdeaVoteControl {
  voteCount: number
  viewerHasVoted: boolean
  pending: boolean
  error: string | null
}

const IDLE = { pending: false, error: null }

export function useIdeaVotes(ideas: Idea[]): {
  votes: Record<string, IdeaVoteControl>
  toggle: (ideaId: string) => void
  dismissError: (ideaId: string) => void
} {
  const [overrides, setOverrides] = useState<Record<string, Partial<IdeaVoteControl>>>({})

  // A new `ideas` array is a new answer from the server, and the server is
  // authoritative (S2-008). The numbers a vote response wrote are the server's
  // too, but they are only the *latest* server word until the list is fetched
  // again - after that, keeping them would pin a count other people have since
  // changed for as long as the list is mounted. So a fresh answer drops every
  // override's numbers and keeps only what is still true of this client: a
  // request in flight, and an error the reader has not dismissed yet.
  //
  // Adjusted during render, React's pattern for state derived from a prop
  // change, so there is never a paint that shows the stale numbers against
  // the fresh list. This relies on the caller keeping one array identity per
  // answer - `useIdeaDiscovery` does, including for its empty fallback.
  const [seededFrom, setSeededFrom] = useState(ideas)
  if (seededFrom !== ideas) {
    setSeededFrom(ideas)
    setOverrides((previous) => {
      const kept: Record<string, Partial<IdeaVoteControl>> = {}
      for (const [ideaId, { pending, error }] of Object.entries(previous)) {
        if (pending || error) kept[ideaId] = { pending, error }
      }
      return kept
    })
  }

  // Seeded from the ideas the list was given, so the control renders the
  // server's numbers from the first paint and there is no request per idea.
  // Declared before `toggle`, which reads it to know what a click means.
  const votes: Record<string, IdeaVoteControl> = {}
  for (const idea of ideas) {
    votes[idea.id] = {
      voteCount: idea.voteCount,
      viewerHasVoted: idea.viewerHasVoted,
      ...IDLE,
      ...overrides[idea.id],
    }
  }

  // In-flight idea ids, checked synchronously.
  //
  // A ref, and not the `pending` state, because state cannot be read
  // synchronously inside an event handler: two clicks in one tick would both
  // read `pending === false` and both send a request. The server is idempotent
  // so the second would be harmless, but "harmless" is a property of the server
  // and this client should not lean on it to avoid sending one.
  //
  // `useRef` and not a module-level `Set`: a module-level one would be shared
  // by every mounted list on the page, and would outlive the component that
  // wrote to it - the cross-test leak the test setup already has to reset
  // elsewhere in this app.
  const inFlight = useRef<Set<string>>(new Set())

  // The latest effective state, for the click handler.
  //
  // `toggle` has to know what a click *means* - vote or withdraw - and reading
  // `votes` directly would put an object that is rebuilt every render into the
  // callback's dependencies. A ref keeps the callback stable without making it
  // stale: it is written after every render, so a click always sees the
  // current state, including one that arrived with the list rather than from a
  // response. Same pattern as `useDebouncedCallback` in this app.
  const latest = useRef(votes)
  useEffect(() => {
    latest.current = votes
  })

  const update = useCallback((ideaId: string, patch: Partial<IdeaVoteControl>) => {
    setOverrides((previous) => ({
      ...previous,
      [ideaId]: { ...previous[ideaId], ...patch },
    }))
  }, [])

  const toggle = useCallback(
    (ideaId: string) => {
      if (inFlight.current.has(ideaId)) return

      // Read the *effective* state - the idea's own numbers, then whatever the
      // last response said - rather than the overrides map alone. The overrides
      // are empty before the first click on an idea, so reading them there
      // would mean every first click sends a vote, including on an idea the
      // reader has already voted for.
      const current = latest.current[ideaId]
      if (current === undefined || current.pending) return

      const wasVoted = current.viewerHasVoted
      inFlight.current.add(ideaId)
      update(ideaId, { pending: true, error: null })

      // Read `wasVoted` at click time, so two rapid clicks on opposite states
      // cannot both send the same request.
      const request = wasVoted === true ? removeVoteRequest(ideaId) : voteIdeaRequest(ideaId)

      void request
        .then((result) => {
          inFlight.current.delete(ideaId)

          if (!result.success || result.voteState === null) {
            // The server's own words. A refusal leaves the count and the flag
            // exactly as they were, because this client never guessed at them.
            update(ideaId, { pending: false, error: result.message })
            return
          }

          update(ideaId, {
            pending: false,
            error: null,
            voteCount: result.voteState.voteCount,
            viewerHasVoted: result.voteState.viewerHasVoted,
          })
        })
        .catch(() => {
          inFlight.current.delete(ideaId)
          // A thrown error means the request never got a decision, which is a
          // different fact from a refusal and is reported as itself.
          update(ideaId, {
            pending: false,
            error: 'We could not reach the server. Please try again.',
          })
        })
    },
    [update],
  )

  const dismissError = useCallback((ideaId: string) => update(ideaId, { error: null }), [update])

  return { votes, toggle, dismissError }
}
