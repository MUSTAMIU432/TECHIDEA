import { SpinnerIcon } from '../../identity/components/icons'
import type { IdeaVoteControl } from '../hooks/useIdeaVotes'
import { VoteIcon } from './IdeaCardActions'

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
  layout = 'pill',
}: {
  ideaId: string
  control: IdeaVoteControl
  onToggle: (ideaId: string) => void
  onDismissError: (ideaId: string) => void
  /**
   * `bar` draws the vote as one cell of `IdeaCardActions`, matching the three
   * disclosures beside it; `pill` is the standalone button for the pages that
   * are not a card in a list. Both carry the same state and the same names, so
   * a vote reads the same wherever it is drawn.
   */
  layout?: 'pill' | 'bar'
}) {
  const { voteCount, viewerHasVoted, pending, error } = control

  return (
    <span
      className={
        layout === 'bar'
          ? 'flex min-w-0 flex-col items-stretch gap-1'
          : 'inline-flex flex-col items-start gap-1'
      }
    >
      <button
        type="button"
        aria-pressed={viewerHasVoted}
        disabled={pending}
        onClick={() => onToggle(ideaId)}
        className={
          layout === 'bar'
            ? `flex flex-col items-center gap-1.5 rounded-lg px-2 py-2 text-[13px] font-bold tracking-tight transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60 ${
                viewerHasVoted
                  ? 'bg-brand-50 text-brand-800 hover:bg-brand-100'
                  : 'text-gray-700 hover:bg-gray-50 hover:text-gray-900'
              }`
            : `inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60 ${
                viewerHasVoted
                  ? 'border-brand-300 bg-brand-50 text-brand-800'
                  : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
              }`
        }
      >
        {pending && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
        {layout === 'bar' ? (
          <>
            <VoteIcon filled={viewerHasVoted} />
            <span className="flex w-full items-center justify-center gap-1.5">
              {/* Spelled out for a screen reader: "3" alone is not a vote count. */}
              <span className="sr-only">
                {viewerHasVoted ? 'Remove your vote' : 'Vote for this idea'}
              </span>
              <span aria-hidden="true" className="truncate">
                Like
              </span>
              {/* The count is brand-coloured and bolder than the word, so a voted
                  card is readable as "mine" without relying on the fill alone. */}
              <span
                aria-hidden="true"
                className={`shrink-0 tabular-nums ${viewerHasVoted ? 'text-brand-600' : 'text-gray-400'}`}
              >
                {voteCount}
              </span>
            </span>
          </>
        ) : (
          <>
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`h-5 w-5 ${viewerHasVoted ? 'fill-current' : 'fill-none stroke-current'}`}
            >
              <path d="M7 10.5v9H4.5A1.5 1.5 0 0 1 3 18V11.5a1.5 1.5 0 0 1 1.5-1.5H7Zm0 0 3.6-6.2a1.6 1.6 0 0 1 3 .6v3.1h4.2a2 2 0 0 1 1.97 2.4l-1.4 6.6a2 2 0 0 1-1.97 1.6H7" />
            </svg>
            {/* Spelled out for a screen reader: "3" alone is not a vote count. */}
            <span className="sr-only">
              {viewerHasVoted ? 'Remove your vote' : 'Vote for this idea'}
            </span>
            <span aria-hidden="true">{voteCount}</span>
          </>
        )}
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
