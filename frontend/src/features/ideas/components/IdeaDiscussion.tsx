import { useRef, useState } from 'react'

import { useAuth } from '../../identity/auth/AuthContext'
import { SpinnerIcon } from '../../identity/components/icons'
import { MAX_COMMENT_LENGTH, useComments } from '../hooks/useComments'
import { groupCommentThread } from '../utils/commentThread'
import type { Idea, IdeaComment } from '../api/ideasApi'

/**
 * How many comments of a run are drawn before the rest wait behind "See more".
 *
 * One, deliberately: this is a discussion on somebody else's idea, and the reader
 * has not yet asked to read it at all. It is also a disclosure, so the cost of
 * wanting the rest is a press, not a page.
 */
const PREVIEW_COMMENTS = 1

/**
 * One idea's discussion: the comments, the replies under them, and what the
 * signed-in reader may do to either.
 *
 * Rendered inside an idea's card on `/app/ideas`, behind the discussion cell of
 * `IdeaCardActions`, because the list is a *list* - twenty ideas, each with its
 * own thread, and a page that fetched all twenty discussions would be twenty
 * requests for content nobody asked to read. So the discussion is fetched when
 * it is opened and not before, and renders nothing at all when closed.
 *
 * The three states a reader can be in are kept apart, because they are
 * different facts and one message would be a lie twice over:
 *
 * - nothing has been said yet,
 * - the request failed (which is *not* the same as there being nothing, and is
 *   not rendered as though it were),
 * - the idea is readable but the thread is empty because it is closed.
 *
 * **A reply is written inside the comment it answers, and lands under it.**
 * There are two boxes on this screen - one at the foot of the thread, which
 * opens it, and one under whichever comment the reader pressed "Reply" on -
 * and never more than one is open. That is the whole of the composition:
 * answering in the middle of a conversation you are reading is the difference
 * between a thread and a form that happens to sit below a thread.
 *
 * The "Reply" affordance deliberately does not pretend to be threading. The
 * schema carries `authorId` and no display name, so there is nothing to mention
 * by name; the server records only *which* comment is being answered, which is
 * enough to nest a reply and not enough to render "@someone". So the box opens
 * where the answer belongs and the words are the reader's own.
 *
 * What is offered on a comment comes from `comment.authorId` compared with the
 * signed-in user. That decides which buttons to draw and nothing else: the
 * server refuses an edit or a delete it should refuse whether or not a button
 * was rendered, so hiding them is a courtesy rather than a control.
 *
 * **The foot composer lives here rather than in the composer component**,
 * because a comment's "Reply" has to be able to hand its focus target a
 * sibling's text - or, now, to leave that text alone and open its own box
 * instead. Either way the two boxes cannot both own the same state, so it is
 * held here.
 */
/**
 * The discussion, always open.
 *
 * The list's card wraps this in a disclosure so twenty ideas do not fetch
 * twenty discussions; the idea's own page *is* the discussion's page, so there
 * is nothing to open it from. Same component, same fetches, one fewer click -
 * and the `open` prop stays because the card still needs it.
 */
export function IdeaDiscussionPanel({ idea }: { idea: Idea }) {
  return <IdeaDiscussion idea={idea} open />
}

export function IdeaDiscussion({ idea, open }: { idea: Idea; open: boolean }) {
  const { user } = useAuth()
  // Fetched only while open. The list can hold twenty ideas, and a page that
  // asked for twenty discussions up front would be twenty requests for content
  // nobody asked to read.
  const discussion = useComments(open ? idea.id : null, true)
  const [content, setContent] = useState('')
  const [replyToId, setReplyToId] = useState<string | null>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const replyRef = useRef<HTMLTextAreaElement>(null)

  /**
   * "Reply" opens the box *under that comment* and puts the caret in it.
   *
   * Pressing it again closes it, because a toggle is what a reader expects
   * from a control they can see is open - and the alternative is a reader who
   * opened a box by accident having to scroll to the bottom of the thread to
   * dismiss it.
   */
  function toggleReply(commentId: string) {
    if (replyToId === commentId) {
      setReplyToId(null)
      return
    }
    setReplyToId(commentId)
    // The box is rendered by the click that opens it, so the ref is still empty
    // in this tick. Focusing on the next frame is what puts the caret in a
    // field that does not exist yet.
    requestAnimationFrame(() => {
      replyRef.current?.focus()
    })
  }

  if (!open) return null

  return (
    /*
      A labelled region, so the thread is reachable as a unit by assistive
      technology and so a test can ask "what is in the discussion?" of the
      discussion rather than of the whole card - which also holds whatever
      else a card grows.
    */
    <section aria-label={`Discussion: ${idea.title}`} className="mt-3">
      <CommentList
        discussion={discussion}
        userId={user?.id ?? null}
        replyToId={replyToId}
        onReply={toggleReply}
        onCancelReply={() => setReplyToId(null)}
        replyRef={replyRef}
      />
      <CommentComposer
        idea={idea}
        discussion={discussion}
        content={content}
        onContentChange={setContent}
        textareaRef={composerRef}
      />
    </section>
  )
}

function CommentList({
  discussion,
  userId,
  replyToId,
  onReply,
  onCancelReply,
  replyRef,
}: {
  discussion: ReturnType<typeof useComments>
  userId: string | null
  /** The comment whose reply box is open, or null when none is. */
  replyToId: string | null
  onReply: (commentId: string) => void
  onCancelReply: () => void
  replyRef: React.RefObject<HTMLTextAreaElement | null>
}) {
  /*
    Grouped once, here, rather than inside the row: this is a pass over the
    page, and calling it per row would regroup the whole discussion for every
    comment on it.

    Also grouped *before* the three states below short-circuit, because the
    collapse hook has to be called on every render of this component - including
    the renders that go on to draw a loading bar or an error instead.
  */
  const threads = groupCommentThread(discussion.comments)

  /*
    A discussion that has grown forty one-word answers is a wall, and the reader
    came for the idea rather than for the argument - so the first thread is drawn
    and the rest wait behind one "See more". A thread with a box open in it is
    never collapsed away from the reader, and neither is the one this reader has
    just posted to (see `useCollapsedRun`).
  */
  const run = useCollapsedRun(
    threads.length,
    threads.some((thread) =>
      [thread.comment, ...thread.replies].some(
        (comment) =>
          comment.id === replyToId ||
          comment.id === discussion.editingCommentId ||
          comment.id === discussion.lastPostedId,
      ),
    ),
  )

  if (discussion.loading && discussion.pageInfo === null) {
    return (
      <output className="block rounded-lg bg-gray-50 px-3 py-3">
        <span className="sr-only">Loading the discussion…</span>
        <span className="block h-3 w-40 animate-pulse rounded bg-gray-200" />
        <span className="mt-2 block h-3 w-56 animate-pulse rounded bg-gray-100" />
      </output>
    )
  }

  if (discussion.error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-3" role="alert">
        <p className="text-sm text-red-700">{discussion.error}</p>
      </div>
    )
  }

  if (discussion.comments.length === 0) {
    return (
      <p className="rounded-lg bg-gray-50 px-3 py-3 text-sm text-gray-600">
        No comments yet. Be the first to say something.
      </p>
    )
  }

  const shown = run.collapsed ? threads.slice(0, PREVIEW_COMMENTS) : threads

  return (
    <>
      <ul className="space-y-3">
        {shown.map((thread) => (
          <li key={thread.comment.id}>
            {/*
              One box for a comment and its replies, rather than a card per
              comment. A reply drawn as its own card was the thing this layout
              exists to stop: it read as a separate conversation, and the rule and
              indent below are what say "this is the answer to that".
            */}
            <div className="rounded-xl border border-gray-100 bg-white p-3">
              <CommentRow
                comment={thread.comment}
                discussion={discussion}
                userId={userId}
                replyToId={replyToId}
                onReply={onReply}
                onCancelReply={onCancelReply}
                replyRef={replyRef}
              />
              {thread.replies.length > 0 && (
                <Replies
                  replies={thread.replies}
                  discussion={discussion}
                  userId={userId}
                  replyToId={replyToId}
                  onReply={onReply}
                  onCancelReply={onCancelReply}
                  replyRef={replyRef}
                />
              )}
            </div>
          </li>
        ))}
      </ul>
      {run.collapsible && (
        <SeeMoreButton
          expanded={run.expanded}
          hidden={run.hidden}
          noun="comment"
          onClick={run.toggle}
        />
      )}
    </>
  )
}

/**
 * The replies under one comment, indented and ruled off the thing they answer.
 *
 * The rule is the load-bearing part of the indent: indentation on its own is a
 * difference a screen reader cannot hear, so the replies are also a list *inside*
 * the comment's list item, and the border is what the eye follows.
 *
 * Its own collapse state, because a thread is the unit that is opened and closed -
 * one thread being read to the end says nothing about the next one.
 */
function Replies({
  replies,
  discussion,
  userId,
  replyToId,
  onReply,
  onCancelReply,
  replyRef,
}: {
  replies: IdeaComment[]
  discussion: ReturnType<typeof useComments>
  userId: string | null
  replyToId: string | null
  onReply: (commentId: string) => void
  onCancelReply: () => void
  replyRef: React.RefObject<HTMLTextAreaElement | null>
}) {
  const run = useCollapsedRun(
    replies.length,
    replies.some(
      (reply) =>
        reply.id === replyToId ||
        reply.id === discussion.editingCommentId ||
        reply.id === discussion.lastPostedId,
    ),
  )
  const shown = run.collapsed ? replies.slice(0, PREVIEW_COMMENTS) : replies

  return (
    <>
      <ul className="mt-3 space-y-3 border-l-2 border-gray-100 pl-3">
        {shown.map((reply) => (
          <li key={reply.id}>
            <CommentRow
              comment={reply}
              discussion={discussion}
              userId={userId}
              replyToId={replyToId}
              onReply={onReply}
              onCancelReply={onCancelReply}
              replyRef={replyRef}
            />
          </li>
        ))}
      </ul>
      {run.collapsible && (
        <SeeMoreButton
          expanded={run.expanded}
          hidden={run.hidden}
          noun="reply"
          onClick={run.toggle}
        />
      )}
    </>
  )
}

/**
 * Whether the rest of a run of comments is on screen.
 *
 * A run of one-word answers is a wall, and the reader came for the idea rather
 * than for the argument, so `PREVIEW_COMMENTS` of them are drawn and the rest
 * wait behind a "See more" they press. It is a disclosure and not truncation:
 * nothing is fetched differently and nothing is lost, so the count is the number
 * the reader is being asked to agree to see, spelled out rather than reduced to
 * an ellipsis.
 *
 * `holdOpen` is the one exception - a run with a reply box or an edit open in it
 * is never collapsed, because collapsing it would take the words out from under
 * somebody who is writing them. Neither is a run containing the comment the
 * reader has just posted: a comment nobody can find after posting it is the one
 * comment that must never be hidden.
 *
 * The reader's own choice is the only thing that reopens it otherwise. A comment
 * arriving while a run is collapsed does not push the thread open on its own,
 * because a list that rearranges itself under a reader mid-post is a list being
 * read from under them - and whoever posted it can see it by pressing "See more".
 */
function useCollapsedRun(count: number, holdOpen: boolean) {
  const [expanded, setExpanded] = useState(false)
  const collapsible = count > PREVIEW_COMMENTS
  const open = expanded || holdOpen
  return {
    collapsible,
    collapsed: collapsible && !open,
    /** How many are waiting, for the label - zero once they are all on screen. */
    hidden: collapsible && !open ? count - PREVIEW_COMMENTS : 0,
    /** Whether the extra comments are showing, which is what this discloses. */
    expanded: open,
    toggle: () => setExpanded((was) => !was),
  }
}

/**
 * The disclosure itself. `aria-expanded` is on the button because the button is
 * what controls the rows - and the wording says what pressing it will *do*
 * ("See 12 more replies" / "See fewer"), because a reader who cannot see the
 * rows they are about to reveal should still be able to predict them.
 */
function SeeMoreButton({
  expanded,
  hidden,
  noun,
  onClick,
}: {
  expanded: boolean
  hidden: number
  noun: 'comment' | 'reply'
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      onClick={onClick}
      className="mt-2 inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs font-semibold text-gray-500 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
    >
      {expanded ? (
        'See fewer'
      ) : (
        <>
          See {hidden} more {noun}
          {hidden === 1 ? '' : 's'}
        </>
      )}
    </button>
  )
}

/**
 * One comment: who said it, when, what, what may be done to it - and the reply
 * box, which renders *inside* this comment rather than at the foot of the
 * thread.
 */
function CommentRow({
  comment,
  discussion,
  userId,
  replyToId,
  onReply,
  onCancelReply,
  replyRef,
}: {
  comment: IdeaComment
  discussion: ReturnType<typeof useComments>
  userId: string | null
  replyToId: string | null
  onReply: (commentId: string) => void
  onCancelReply: () => void
  replyRef: React.RefObject<HTMLTextAreaElement | null>
}) {
  const mine = comment.authorId === userId
  const editing = discussion.editingCommentId === comment.id
  const busy = discussion.busyCommentId === comment.id

  return (
    <div className="flex items-start gap-3">
      {/*
        The schema carries an `authorId` and no name, avatar or headline -
        deliberately, so this client cannot render (or leak) a member's email on
        a comment. So the avatar is a monogram of the one thing that is known,
        and the name line says as much as the server said: "You" or "A member".
        A future `CommentAuthor` shape fills these two in without touching this
        layout.
      */}
      <span
        aria-hidden="true"
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-700"
      >
        {(mine ? 'You' : 'A member').charAt(0)}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-gray-900">{mine ? 'You' : 'A member'}</p>
            <p className="text-xs text-gray-500">
              {new Date(comment.createdAt).toLocaleString()}
              {comment.updatedAt !== comment.createdAt && <span> · edited</span>}
            </p>
          </div>
          {/*
            Offered to the author only, and behind an overflow rather than two
            buttons on the row: an edit and a delete are housekeeping, not what a
            reader came to the thread to do, and the server enforces both whether
            or not they were drawn.
          */}
          {mine && !editing && (
            <CommentMenu
              busy={busy}
              onEdit={() => discussion.startEditing(comment.id)}
              onDelete={() => {
                void discussion.remove(comment.id)
              }}
            />
          )}
        </div>

        {editing ? (
          <EditBox
            commentId={comment.id}
            initial={comment.content}
            busy={busy}
            error={
              discussion.writeError?.field === 'content' ? discussion.writeError.message : null
            }
            onCancel={discussion.cancelEditing}
            onSave={(content) => {
              void discussion.saveEdit(comment.id, content)
            }}
          />
        ) : (
          <>
            {/* Rendered as text. The backend stores the content verbatim and does
                not escape it, so rendering it as markup here would be where an
                injection would have to be prevented. */}
            <p className="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-gray-800">
              {comment.content}
            </p>
            {/* No "Reply" on a reply. The server refuses a reply to a reply
                (`ideas.services.add_comment`), so a button here would be offering
                something guaranteed to fail; a reader who wants to answer a reply
                answers the comment it belongs to. */}
            {comment.parentId === null && (
              <button
                type="button"
                aria-expanded={replyToId === comment.id}
                onClick={() => onReply(comment.id)}
                className="mt-1.5 inline-flex items-center gap-1.5 rounded px-1 py-0.5 text-xs font-semibold text-gray-500 hover:text-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.75}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="h-4 w-4"
                >
                  <path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H8l-4 3v-5.2A7.5 7.5 0 1 1 20 11.5Z" />
                </svg>
                Reply
              </button>
            )}
            {replyToId === comment.id && (
              <ReplyBox
                commentId={comment.id}
                discussion={discussion}
                replyRef={replyRef}
                onCancel={onCancelReply}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}

/**
 * The box a reply is written in, which lives inside the comment it answers.
 *
 * Its own text, and cleared only once the server has accepted it - so a refusal
 * leaves the words where the reader can fix them rather than taking them away.
 * `posting` and the character count are shared with the foot composer because
 * this is the same conversation; `post` refuses a second concurrent post either
 * way.
 */
function ReplyBox({
  commentId,
  discussion,
  replyRef,
  onCancel,
}: {
  commentId: string
  discussion: ReturnType<typeof useComments>
  replyRef: React.RefObject<HTMLTextAreaElement | null>
  onCancel: () => void
}) {
  const [content, setContent] = useState('')
  const overLong = content.length > MAX_COMMENT_LENGTH
  const canSubmit = content.trim().length > 0 && !overLong && !discussion.posting

  function submit() {
    if (!canSubmit) return
    void discussion.post(content, commentId).then((ok) => {
      // Closed on success only. A refusal keeps the box open with the text in
      // it, which is the whole reason it is not simply unmounted.
      if (ok) {
        setContent('')
        onCancel()
      }
    })
  }

  return (
    <form
      className="mt-2"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <label htmlFor={`reply-${commentId}`} className="sr-only">
        Reply to this comment
      </label>
      <textarea
        id={`reply-${commentId}`}
        ref={replyRef}
        value={content}
        rows={2}
        placeholder="Write a reply"
        onChange={(event) => {
          setContent(event.target.value)
          discussion.clearWriteError()
        }}
        onKeyDown={(event) => {
          if (event.key !== 'Enter') return
          // Shift+Enter is the line break, which is how every conversation the
          // reader has ever typed in behaves. So is a modifier held with it -
          // somebody reaching for Shift+Enter should not get a post either.
          if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return
          // An Enter that is choosing a character, not sending a reply: it is
          // part of an IME composition, or the "keydown dead" 229 some browsers
          // report for one. Submitting here would post a half-typed word.
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
          // Nothing to send, or one already on its way. Left alone, so the
          // newline lands where the reader pressed it instead of the key
          // appearing to do nothing at all.
          if (!canSubmit) return
          event.preventDefault()
          submit()
        }}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
      />
      {/* This box's own field errors only. Anything else is the shared write
          error and belongs to whichever box was being written in - showing it in
          both would blame the one that was not. */}
      {discussion.writeError !== null && discussion.writeError.field === 'content' && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {discussion.writeError.message}
        </p>
      )}
      {overLong && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          A comment must be {MAX_COMMENT_LENGTH} characters or fewer.
        </p>
      )}
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-xs text-gray-500">{content.length} characters</span>
        <span className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            disabled={discussion.posting}
            onClick={onCancel}
            className="rounded-lg px-2 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {discussion.posting && <SpinnerIcon className="h-3.5 w-3.5 motion-safe:animate-spin" />}
            Reply
          </button>
        </span>
      </div>
    </form>
  )
}

/**
 * A comment's own author, and the only person who sees it.
 *
 * A disclosure rather than two buttons, because "Edit" and "Delete" are a
 * reader's housekeeping and not the thread's vocabulary, and a row that leads
 * with them reads as though they were the point of commenting.
 */
function CommentMenu({
  busy,
  onEdit,
  onDelete,
}: {
  busy: boolean
  onEdit: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((current) => !current)}
        className="rounded-lg px-1.5 py-0.5 text-sm font-semibold leading-none text-gray-400 hover:bg-gray-50 hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
      >
        <span aria-hidden="true">…</span>
        <span className="sr-only">More actions for your comment</span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-10 mt-1 w-32 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
        >
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={() => {
              setOpen(false)
              onEdit()
            }}
            className="block w-full px-3 py-1.5 text-left text-xs font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:bg-gray-50 disabled:opacity-50"
          >
            Edit
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={() => {
              setOpen(false)
              onDelete()
            }}
            className="block w-full px-3 py-1.5 text-left text-xs font-semibold text-red-700 hover:bg-red-50 focus:outline-none focus-visible:bg-red-50 disabled:opacity-50"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

function CommentComposer({
  idea,
  discussion,
  content,
  onContentChange,
  textareaRef,
}: {
  idea: Idea
  discussion: ReturnType<typeof useComments>
  content: string
  onContentChange: (content: string) => void
  textareaRef: React.RefObject<HTMLTextAreaElement | null>
}) {
  // `idea.discussionOpen` is the server's answer, from the same rule
  // `createComment` enforces. Re-deriving "is this rejected?" here would be the
  // second copy of a lifecycle rule this app has twice decided not to keep on
  // the client - the same reason `availableTransitions` exists.
  if (!idea.discussionOpen) {
    return (
      <p className="mt-3 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-500">
        This idea is closed for discussion.
      </p>
    )
  }

  const overLong = content.length > MAX_COMMENT_LENGTH
  const canSubmit = content.trim().length > 0 && !overLong && !discussion.posting

  function submit() {
    if (!canSubmit) return
    // Cleared only once the server has accepted it, so a refusal leaves the
    // text where the reader can fix it.
    void discussion.post(content).then((ok) => {
      if (ok) onContentChange('')
    })
  }

  return (
    <form
      className="mt-3"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <label htmlFor={`comment-${idea.id}`} className="sr-only">
        Add a comment
      </label>
      <textarea
        id={`comment-${idea.id}`}
        ref={textareaRef}
        value={content}
        rows={2}
        placeholder="Add a comment"
        onChange={(event) => {
          onContentChange(event.target.value)
          discussion.clearWriteError()
        }}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
      />
      {discussion.writeError !== null && discussion.writeError.field !== 'content' && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {discussion.writeError.message}
        </p>
      )}
      {overLong && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          A comment must be {MAX_COMMENT_LENGTH} characters or fewer.
        </p>
      )}
      {discussion.writeError?.field === 'content' && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {discussion.writeError.message}
        </p>
      )}
      <div className="mt-2 flex items-center justify-between">
        <span className="text-xs text-gray-500">{content.length} characters</span>
        <button
          type="submit"
          disabled={!canSubmit}
          className="inline-flex items-center gap-2 rounded-lg bg-brand-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {discussion.posting && <SpinnerIcon className="h-3.5 w-3.5 motion-safe:animate-spin" />}
          Comment
        </button>
      </div>
    </form>
  )
}

function EditBox({
  commentId,
  initial,
  busy,
  error,
  onCancel,
  onSave,
}: {
  commentId: string
  initial: string
  busy: boolean
  error: string | null
  onCancel: () => void
  onSave: (content: string) => void
}) {
  const [content, setContent] = useState(initial)
  const overLong = content.length > MAX_COMMENT_LENGTH
  const canSubmit = content.trim().length > 0 && !overLong && !busy

  return (
    <form
      className="mt-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (canSubmit) onSave(content)
      }}
    >
      <label htmlFor={`edit-comment-${commentId}`} className="sr-only">
        Edit your comment
      </label>
      <textarea
        id={`edit-comment-${commentId}`}
        value={content}
        rows={2}
        onChange={(event) => setContent(event.target.value)}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
      />
      {overLong && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          A comment must be {MAX_COMMENT_LENGTH} characters or fewer.
        </p>
      )}
      {error !== null && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {error}
        </p>
      )}
      <div className="mt-2 flex gap-2">
        <button
          type="submit"
          disabled={!canSubmit}
          className="rounded-lg bg-brand-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-50"
        >
          {busy && <SpinnerIcon className="mr-2 inline h-3.5 w-3.5 motion-safe:animate-spin" />}
          Save
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}
