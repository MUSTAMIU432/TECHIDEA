import { useState } from 'react'

import { useAuth } from '../../identity/auth/AuthContext'
import { SpinnerIcon } from '../../identity/components/icons'
import { MAX_COMMENT_LENGTH, useComments } from '../hooks/useComments'
import type { Idea } from '../api/ideasApi'

/**
 * One idea's discussion: the comments, and what the signed-in reader may do to
 * them.
 *
 * Rendered inside an idea's card on `/app/ideas`, behind a disclosure, because
 * the list is a *list* - twenty ideas, each with its own thread, and a page
 * that fetched all twenty discussions would be twenty requests for content
 * nobody asked to read. So the discussion is fetched when it is opened and not
 * before.
 *
 * The three states a reader can be in are kept apart, because they are
 * different facts and one message would be a lie twice over:
 *
 * - nothing has been said yet,
 * - the request failed (which is *not* the same as there being nothing, and is
 *   not rendered as though it were),
 * - the idea is readable but the thread is empty because it is closed.
 *
 * What is offered on a comment comes from `comment.authorId` compared with the
 * signed-in user. That decides which buttons to draw and nothing else: the
 * server refuses an edit or a delete it should refuse whether or not a button
 * was rendered, so hiding them is a courtesy rather than a control.
 */
export function IdeaDiscussion({
  idea,
  open,
  onToggle,
}: {
  idea: Idea
  /** Whether this idea's thread is the open one. Owned by `IdeaList`. */
  open: boolean
  onToggle: () => void
}) {
  const { user } = useAuth()
  // Fetched only while open. The list can hold twenty ideas, and a page that
  // asked for twenty discussions up front would be twenty requests for content
  // nobody asked to read.
  const discussion = useComments(open ? idea.id : null, true)

  return (
    <div className="mt-3 border-t border-gray-100 pt-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="inline-flex items-center gap-1.5 rounded-lg px-1 py-0.5 text-xs font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
      >
        {open ? 'Hide discussion' : 'Discussion'}
      </button>
      {/*
        A labelled region, so the thread is reachable as a unit by assistive
        technology and so a test can ask "what is in the discussion?" of the
        discussion rather than of the whole card - which also holds whatever
        else a card grows.
      */}
      {open && (
        <section aria-label={`Discussion: ${idea.title}`} className="mt-3">
          <CommentList discussion={discussion} userId={user?.id ?? null} />
          <CommentComposer idea={idea} discussion={discussion} />
        </section>
      )}
    </div>
  )
}

function CommentList({
  discussion,
  userId,
}: {
  discussion: ReturnType<typeof useComments>
  userId: string | null
}) {
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

  return (
    <ul className="space-y-2">
      {discussion.comments.map((comment) => {
        const mine = comment.authorId === userId
        const editing = discussion.editingCommentId === comment.id
        const busy = discussion.busyCommentId === comment.id

        return (
          <li key={comment.id} className="rounded-lg bg-gray-50 px-3 py-2.5">
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-xs text-gray-500">
                <span className="font-semibold text-gray-700">{mine ? 'You' : 'A member'}</span> ·{' '}
                {new Date(comment.createdAt).toLocaleString()}
                {comment.updatedAt !== comment.createdAt && <span> · edited</span>}
              </p>
              {/*
                Offered to the author only. The server enforces it; this is
                about not drawing a button that is guaranteed to be refused.
              */}
              {mine && !editing && (
                <div className="flex shrink-0 gap-1">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => discussion.startEditing(comment.id)}
                    className="rounded px-1.5 py-0.5 text-xs font-semibold text-gray-600 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-50"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      void discussion.remove(comment.id)
                    }}
                    className="rounded px-1.5 py-0.5 text-xs font-semibold text-red-700 hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
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
              // Rendered as text. The backend stores the content verbatim and
              // does not escape it, so rendering it as markup here would be
              // where an injection would have to be prevented.
              <p className="mt-1 whitespace-pre-wrap text-sm text-gray-800">{comment.content}</p>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function CommentComposer({
  idea,
  discussion,
}: {
  idea: Idea
  discussion: ReturnType<typeof useComments>
}) {
  const [content, setContent] = useState('')

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
      if (ok) setContent('')
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
        value={content}
        rows={2}
        placeholder="Add a comment"
        onChange={(event) => {
          setContent(event.target.value)
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
