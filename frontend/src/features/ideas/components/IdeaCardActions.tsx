import type { ReactNode } from 'react'

import type { IdeaVoteControl } from '../hooks/useIdeaVotes'
import type { Idea } from '../api/ideasApi'
import { reviewHistoryVisible } from '../utils/reviewVisibility'
import { IdeaVoteButton } from './IdeaVoteButton'

/**
 * The four things a reader can do with an idea, in one evenly spaced row at
 * the foot of its card: like it, comment, open its supporting evidence, and
 * open its review history.
 *
 * They used to be three stacked text links under a vote pill, each on its own
 * rule - a card's footer was taller than its content, and the things a reader
 * comes to do sat at three different visual weights. One row, four equal
 * cells, icon over label: the same footing for every action, and the footer
 * is now one band rather than four.
 *
 * **Two rules about what the bar may promise.** It draws what the server said
 * this viewer may see - `reviewHistoryVisible` for the review cell, exactly the
 * condition `IdeaReviewSection` itself uses - and it never draws an action the
 * backend would refuse. "Like" is the vote control (`IdeaVoteButton`), not a
 * second opinion about the same idea: there is one vote per reader per idea in
 * the database, and two buttons for it would be one too many promises.
 *
 * The cells are the disclosures for the three sections below the bar, so the
 * toggles stay here and the panels stay content-only. That is also why the
 * panels render nothing when closed rather than rendering an empty region:
 * there is nothing to see, and an empty box is a thing to see.
 */
export type IdeaCardSection = 'discussion' | 'evidence' | 'review'

export interface IdeaCardSectionsOpen {
  discussion: boolean
  evidence: boolean
  review: boolean
}

export function IdeaCardActions({
  idea,
  viewerId,
  vote,
  onToggleVote,
  onDismissVoteError,
  open,
  onToggleSection,
}: {
  idea: Idea
  /** Null for a signed-out reader, which is what `useAuth` reports. */
  viewerId: string | null
  vote: IdeaVoteControl
  onToggleVote: (ideaId: string) => void
  onDismissVoteError: (ideaId: string) => void
  open: IdeaCardSectionsOpen
  onToggleSection: (section: IdeaCardSection) => void
}) {
  // The same question `IdeaReviewSection` asks, asked here too so a reader who
  // cannot open the history is not offered a cell that leads nowhere.
  const reviewVisible = reviewHistoryVisible(idea, viewerId)
  // Review is the one cell that can mean "there is something for you to do
  // here", so it is the one cell allowed to look different from its neighbours.
  const reviewWantsAttention = idea.viewerCanStartReview || idea.viewerActiveReviewId !== null

  // Four cells divide the card evenly; three divide the same card evenly too,
  // so the row stays full rather than leaving a hole where review is not.
  const columns = reviewVisible ? 'grid-cols-4' : 'grid-cols-3'

  return (
    /*
      A `fieldset` because the cells are one group of related controls, and a
      legend names that group for assistive technology without putting a visible
      heading between the reader and the card's content.
    */
    <fieldset className="mt-4 border-0 border-t border-gray-100 p-0 pt-2">
      <legend className="sr-only">Actions on {idea.title}</legend>
      <div className={`grid ${columns} divide-x divide-gray-100`}>
        <IdeaVoteButton
          ideaId={idea.id}
          control={vote}
          onToggle={onToggleVote}
          onDismissError={onDismissVoteError}
          layout="bar"
        />
        <CardCell
          icon={<CommentIcon />}
          label="Comment"
          // Named for the section it opens rather than for the action: the
          // composer's own submit button is already called "Comment", and a card
          // holding two controls with the same name helps nobody.
          name="Discussion"
          open={open.discussion}
          onClick={() => onToggleSection('discussion')}
        />
        <CardCell
          icon={<PaperclipIcon />}
          label="Evidence"
          name="Supporting evidence"
          open={open.evidence}
          onClick={() => onToggleSection('evidence')}
        />
        {reviewVisible && (
          <CardCell
            icon={<ReviewIcon />}
            label="Review"
            name="Review history"
            open={open.review}
            attention={reviewWantsAttention}
            onClick={() => onToggleSection('review')}
          />
        )}
      </div>
    </fieldset>
  )
}

/**
 * One cell of the bar: an icon over its label, sized so the cells divide a
 * card's width evenly without any of them needing to know how wide that is.
 *
 * `label` is the short word that fits a cell - "Evidence", not "Supporting
 * evidence" - and `name` is the accessible name where the section's own name is
 * the clearer one to announce, or where the short word is already taken by
 * another control on the card. The two never describe different *actions*: a
 * reader who says what they see gets the cell that shows it.
 *
 * **Weight is the whole legibility argument here.** Four cells of thin 1.75px
 * strokes over 12px grey words are hard to read at a glance, which is the only
 * glance a card's footer gets - so the icon is 2px at 22px and the label is
 * 13px bold, and the open state is drawn as a filled surface rather than left to
 * `aria-expanded`. Every cell is the same size and the same weight; the one
 * exception is a cell that wants the reader's attention, which is amber.
 */
function CardCell({
  icon,
  label,
  name,
  open,
  attention = false,
  onClick,
}: {
  icon: ReactNode
  label: string
  /** Defaults to `label`; set where the section's name is the better name. */
  name?: string
  open: boolean
  /** Draws the amber "this one is about you" state. See `IdeaCardActions`. */
  attention?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-label={name}
      aria-expanded={open}
      onClick={onClick}
      className={`flex flex-col items-center gap-1.5 rounded-lg px-2 py-2 text-[13px] font-bold tracking-tight transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 ${
        open
          ? 'bg-gray-100 text-gray-900'
          : attention
            ? 'text-amber-800 hover:bg-amber-50'
            : 'text-gray-700 hover:bg-gray-50 hover:text-gray-900'
      }`}
    >
      {icon}
      <span className="flex w-full items-center justify-center gap-1.5 truncate">
        <span className="truncate">{label}</span>
        {attention && (
          <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
        )}
      </span>
    </button>
  )
}

/**
 * The one stroke weight and the one icon size, for every glyph in this file and
 * for `IdeaVoteButton`'s.
 *
 * It was tempting to let each icon choose. That is how a row ends up with four
 * glyphs that are four slightly different sizes, and the eye reads the
 * inconsistency as noise before it reads any of them - so the weight and the box
 * are decided once, here, and a new icon inherits them by taking this component
 * instead of an `<svg>`.
 *
 * `size` is a named step rather than a class the caller appends, deliberately:
 * two Tailwind size utilities in one `class` string do not compose - whichever
 * the build happens to emit last wins, so a caller asking for a smaller icon
 * would get the default and never know why.
 */
export function CellIcon({
  children,
  size = 'md',
  className = '',
}: {
  children: ReactNode
  size?: 'sm' | 'md'
  className?: string
}) {
  return (
    <svg
      className={`shrink-0 ${size === 'sm' ? 'h-4 w-4' : 'h-[22px] w-[22px]'} ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

/**
 * The same disclosure, on one line, for the pages that are not a card in a
 * list - the review workspace's evidence panel, where there is no row of
 * siblings to be a cell of.
 */
export function CardDisclosureButton({
  icon,
  label,
  hideLabel,
  open,
  onToggle,
}: {
  icon: ReactNode
  label: string
  /** The wording while open, which is longer than it sounds. */
  hideLabel: string
  open: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onToggle}
      className="inline-flex items-center gap-2 rounded-lg px-1 py-0.5 text-xs font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
    >
      <span aria-hidden="true" className="text-gray-500 [&>svg]:h-4 [&>svg]:w-4">
        {icon}
      </span>
      {open ? hideLabel : label}
    </button>
  )
}

export function CommentIcon({ size }: { size?: 'sm' | 'md' }) {
  return (
    <CellIcon size={size}>
      {/*
        A rounded speech bubble rather than the previous sharp-cornered one: the
        tail is what tells a reader this opens a conversation rather than a
        file, and a hard corner at this size read as a square.
      */}
      <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
    </CellIcon>
  )
}

export function PaperclipIcon({ size }: { size?: 'sm' | 'md' }) {
  return (
    <CellIcon size={size}>
      {/*
        Two turns, drawn inside the frame it is given. The previous path had
        three and a tail running off the right edge, which at 20px was a grey
        smudge rather than a paperclip - the one glyph in the row nobody could
        name.
      */}
      <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </CellIcon>
  )
}

function ReviewIcon({ size }: { size?: 'sm' | 'md' }) {
  return (
    <CellIcon size={size}>
      {/*
        A clipboard with a rounded tab standing clear of the body, so the two
        parts do not fuse into a rectangle at this size. The tick is the reason
        to reach for a review glyph rather than a document: this cell opens what
        happened to the idea, not the idea's text.
      */}
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <rect width="8" height="4" x="8" y="2" rx="1" />
      <path d="m9 14 2 2 4-4" />
    </CellIcon>
  )
}

/**
 * The thumb, drawn as one shape rather than two so that `filled` is a real
 * state and not a half-painted outline.
 *
 * The outline and the filled version are the *same path*; only `fill` changes.
 * A thumb built from a rectangle and a separate open stroke filled as a solid
 * body with a hollow cuff, which is why the earlier version needed a
 * background tint to say "you have voted". Here the silhouette itself changes.
 */
export function VoteIcon({ filled }: { filled: boolean }) {
  return (
    <CellIcon className={filled ? 'fill-current' : ''}>
      <path d="M7 10.5v9H4.5A1.5 1.5 0 0 1 3 18V11.5a1.5 1.5 0 0 1 1.5-1.5H7Zm0 0 3.6-6.2a1.6 1.6 0 0 1 3 .6v3.1h4.2a2 2 0 0 1 1.97 2.4l-1.4 6.6a2 2 0 0 1-1.97 1.6H7" />
    </CellIcon>
  )
}
