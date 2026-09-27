import { SpinnerIcon } from '../../identity/components/icons'
import type { IdeaVoteControl } from '../hooks/useIdeaVotes'

/**
 * One idea's vote control: a toggle that shows the count.
 *
 * A single button rather than a vote button and an "un-vote" button, because
 * the two states are the same action in opposite directions and a reader
 * changing their mind is ordinary. `aria-pressed` carries the state, so it is
 * a toggle to assistive technology and not two buttons wearing a trenchcoat.
 *
 * The count and the filled state both come from the server — see
 * `useIdeaVotes` for why there is no optimistic arithmetic here — and a failed
 * request leaves both exactly as they were, so a reader who cannot vote (an
 * idea that has just become unreadable, a network failure) is told so without
 * the number moving under them.
 */
export function IdeaVoteButton({
  ideaId,
  control,
  onToggle,
  onDismissError,
}: {
  ideaId: string
  control: IdeaVoteControl
  onToggle: (ideaId: string) => void
  onDismissError: (ideaId: string) => void
}) {
  const { voteCount, viewerHasVoted, pending, error } = control

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        aria-pressed={viewerHasVoted}
        disabled={pending}
        onClick={() => onToggle(ideaId)}
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60 ${
          viewerHasVoted
            ? 'border-brand-300 bg-brand-50 text-brand-800'
            : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
        }`}
      >
        {pending && <SpinnerIcon className="h-3.5 w-3.5 motion-safe:animate-spin" />}
        <svg
          aria-hidden="true"
          viewBox="0 0 12 12"
          className={`h-3 w-3 ${viewerHasVoted ? 'fill-current' : 'fill-none stroke-current'}`}
        >
          <path
            d="M6 1.5 L10.5 10.5 L1.5 10.5 Z"
            strokeWidth="1.5"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
        {/* Spelled out for a screen reader: "3" alone is not a vote count. */}
        <span className="sr-only">
          {viewerHasVoted ? 'Remove your vote' : 'Vote for this idea'}
        </span>
        <span aria-hidden="true">{voteCount}</span>
      </button>
      {error !== null && (
        <span role="alert" className="flex items-center gap-2 text-xs text-red-700">
          {error}
          <button
            type="button"
            onClick={() => onDismissError(ideaId)}
            className="rounded px-1 font-semibold underline hover:no-underline focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
          >
            Dismiss
          </button>
        </span>
      )}
    </span>
  )
}
