import { useCallback, useEffect, useRef, useState } from 'react'

import {
  commentsRequest,
  createCommentRequest,
  deleteCommentRequest,
  updateCommentRequest,
  type IdeaComment,
  type IdeaPageInfo,
} from '../api/ideasApi'

/**
 * One idea's discussion, and the four things a reader can do to it.
 *
 * The shape of the state is the interesting part. A discussion is *append-only
 * in reading order*: comments are listed oldest first and a new one goes at the
 * bottom, so a post does not reorder anything the reader is looking at. That
 * lets a successful create splice the new comment onto the end of the loaded
 * page and adjust the total, instead of re-fetching - which is what keeps a
 * reader's scroll position and the page they were on through somebody else
 * saying something.
 *
 * A reply is one of those comments with a `parentId`, so it is spliced and
 * ordered exactly as any other would be. Nothing here knows what a thread
 * *looks* like: the list stays chronological and the component groups it.
 *
 * Three things it will not do:
 *
 * - **No local filtering, sorting or de-duplication.** The server's order and
 *   membership are the truth. A client that sorted its own copy would show an
 *   order the backend never promised. Grouping a reply under its parent is not
 *   this: it reads the `parentId` the server sent and reorders nothing.
 * - **No pretending a refusal is a failure.** A `success: false` payload is the
 *   server's answer and is reported as its message, with `field` for the input
 *   at fault. Only a thrown error - the request never got a decision - becomes
 *   the transport message.
 * - **No second opinion about authorization.** Whether the current user may
 *   edit or delete a comment is decided by comparing `authorId` with the
 *   signed-in user, which is a question of what to *offer*. The server refuses
 *   either way.
 */
export interface CommentDiscussion {
  comments: IdeaComment[]
  pageInfo: IdeaPageInfo | null
  loading: boolean
  error: string | null
  /** A business refusal from the last write, with the field it blames. */
  writeError: { message: string; field: string | null } | null
  posting: boolean
  /** The comment currently being written or edited, by id. */
  busyCommentId: string | null
  editingCommentId: string | null
  /** The comment this reader last posted, for a view that hides a long thread. */
  lastPostedId: string | null
  canPost: boolean
  /**
   * Post a comment, or - with `parentId` - a reply to another comment on this
   * idea. The parent is a hint about the shape of the request, never a
   * permission: the server checks it can be read, is on this idea, and is not
   * itself a reply.
   */
  post: (content: string, parentId?: string | null) => Promise<boolean>
  startEditing: (commentId: string) => void
  cancelEditing: () => void
  saveEdit: (commentId: string, content: string) => Promise<boolean>
  remove: (commentId: string) => Promise<boolean>
  clearWriteError: () => void
}

const TRANSPORT_FAILURE = 'We could not reach the server. Please try again.'

/** One fetched page, tagged with the idea it belongs to. */
interface Answer {
  key: string
  comments: IdeaComment[]
  pageInfo: IdeaPageInfo
}

/**
 * Matches the backend's `MAX_COMMENT_LENGTH`.
 *
 * Duplicated on purpose, and only to fail *before* a round trip: the server
 * remains the authority and its message is what a reader is shown if the two
 * ever disagree. Sending 2,001 characters to be told "too long" would be a
 * request the client knew was going to be refused.
 */
export const MAX_COMMENT_LENGTH = 2000

export function useComments(ideaId: string | null, canPost: boolean): CommentDiscussion {
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)
  const [writeError, setWriteError] = useState<{ message: string; field: string | null } | null>(
    null,
  )
  const [posting, setPosting] = useState(false)
  const [busyCommentId, setBusyCommentId] = useState<string | null>(null)
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null)
  /**
   * The comment this reader last posted, or null if they have not posted one
   * this session.
   *
   * Not cleared afterwards. It names a comment rather than an event, so a stale
   * value can only ever match a comment that was deleted, and the view that
   * asks is looking for one row to keep on screen - where a missing id and a
   * null are the same answer.
   */
  const [lastPostedId, setLastPostedId] = useState<string | null>(null)
  // Bumped only when a successful write has nothing to splice into - i.e. the
  // read failed, so there is no page to update. The normal path updates the
  // answer in place and never touches this.
  const [reloadToken, setReloadToken] = useState(0)

  // Refs mirroring the two "is a write in flight" flags.
  //
  // React state cannot be read synchronously from an event handler, so
  // guarding a submit with `if (posting) return` still lets two clicks in the
  // same tick through - and a double-posted comment is a real duplicate the
  // reader has to delete by hand, not a cosmetic glitch. The ref is checked in
  // the tick the click happened in; the state is what the UI renders.
  //
  // `useRef`, deliberately, and not module scope: a module-level flag would be
  // shared by every discussion on the page, so opening a second one would
  // disable the first one's submit button - and it would outlive the component
  // that set it, which is the same cross-test leak the test setup has to reset
  // by hand elsewhere in this app.
  const postingRef = useRef(false)
  const busyRef = useRef<string | null>(null)

  // The discussion this state belongs to, as a comparable value.
  //
  // Deciding "is the answer in state the one being waited for" by *comparing*
  // during render, rather than by writing a fresh `loading` state into an
  // effect, is the same trick `useIdeaDiscovery` uses: a second render to
  // express something knowable already, and what makes a late answer for a
  // discussion the reader has since closed be ignored rather than painted under
  // the next one.
  const key = ideaId

  useEffect(() => {
    // No idea, nothing to fetch. The component renders that case from `ideaId`
    // rather than as an empty discussion.
    if (key === null) return

    // A reload after a write re-runs this effect for the *same* discussion,
    // so comparing keys cannot tell the two fetches apart: without this, the
    // older one settling last would put back a discussion missing the comment
    // just posted, or its failure would replace fresh contents with an error
    // (S2-008). Same guard as `useIdeaDiscovery`.
    let cancelled = false

    commentsRequest(key)
      .then((page) => {
        if (cancelled) return
        setAnswer({ key, comments: page.items, pageInfo: page.pageInfo })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setAnswer(null)
        setErrorKey(key)
      })

    return () => {
      cancelled = true
    }
    // `reloadToken` is a trigger rather than an input: it is how a successful
    // write says "there is no page to update, go and ask again". The linter is
    // right that the effect does not read it.
    // oxlint-disable-next-line react/exhaustive-effect-dependencies
  }, [key, reloadToken])

  const settled = answer !== null && answer.key === key
  const failed = errorKey === key
  // The last answer rather than necessarily the current one, so re-opening a
  // discussion shows its contents immediately while it refreshes.
  const comments = answer?.comments ?? []
  const pageInfo = answer?.pageInfo ?? null
  const loading = key !== null && !settled && !failed
  // A transport failure is reported as itself. An empty list would read as
  // "this idea has no comments", which is a different claim.
  const error = failed ? TRANSPORT_FAILURE : null

  const post = useCallback(
    async (content: string, parentId?: string | null) => {
      if (ideaId === null) return false

      // Refuse a second click before it becomes a request. `posting` alone
      // cannot do this: it is state, so two clicks in the same tick both read
      // the old value, and a double-posted comment is a duplicate the reader
      // has to delete by hand.
      if (postingRef.current) return false
      postingRef.current = true

      setPosting(true)
      setWriteError(null)
      try {
        const result = await createCommentRequest(ideaId, content, parentId ?? null)
        if (!result.success || result.comment === null) {
          setWriteError({ message: result.message, field: result.field })
          return false
        }
        // Spliced onto the end, because the server orders oldest first and a
        // new comment is the newest. The total moves with it, so the count on
        // screen stays true - and the idea list above is untouched, so the
        // reader keeps their filters and their page.
        //
        // A reply is spliced the same way, at the end rather than under its
        // parent: this list is chronological because that is how a conversation
        // is read, and the reply is *rendered* under its parent by grouping
        // `parentId`, which the payload already carries.
        const created = result.comment
        // So a view that collapses a long thread can put this reader's own
        // words back on screen. A comment nobody can find after posting it is
        // the one comment that must never be hidden, and the id is the only
        // thing that knows it is theirs rather than the server's.
        setLastPostedId(created.id)
        if (answer === null) {
          // The read failed, so there is no page to splice into. Reload rather
          // than invent one, so the total is the server's number.
          setReloadToken((token) => token + 1)
        } else {
          setAnswer({
            ...answer,
            comments: [...answer.comments, created],
            pageInfo: { ...answer.pageInfo, totalCount: answer.pageInfo.totalCount + 1 },
          })
        }
        return true
      } catch {
        setWriteError({ message: TRANSPORT_FAILURE, field: null })
        return false
      } finally {
        postingRef.current = false
        setPosting(false)
      }
    },
    [ideaId, answer],
  )

  const saveEdit = useCallback(
    async (commentId: string, content: string) => {
      if (busyRef.current !== null) return false
      busyRef.current = commentId
      setBusyCommentId(commentId)
      setWriteError(null)
      try {
        const result = await updateCommentRequest(commentId, content)
        if (!result.success || result.comment === null) {
          setWriteError({ message: result.message, field: result.field })
          return false
        }
        // Replaced in place: an edit does not move a comment, so re-fetching
        // would be a request for nothing, and it would risk the row leaving the
        // loaded page for reasons that have nothing to do with the edit.
        const edited = result.comment
        if (answer !== null) {
          setAnswer({
            ...answer,
            comments: answer.comments.map((comment) =>
              comment.id === commentId ? edited : comment,
            ),
          })
        }
        setEditingCommentId(null)
        return true
      } catch {
        setWriteError({ message: TRANSPORT_FAILURE, field: null })
        return false
      } finally {
        busyRef.current = null
        setBusyCommentId(null)
      }
    },
    [answer],
  )

  const remove = useCallback(
    async (commentId: string) => {
      if (busyRef.current !== null) return false
      busyRef.current = commentId
      setBusyCommentId(commentId)
      setWriteError(null)
      try {
        const result = await deleteCommentRequest(commentId)
        if (!result.success) {
          setWriteError({ message: result.message, field: result.field })
          return false
        }
        if (answer !== null) {
          // Deleting a comment takes its replies with it, on the server. Any
          // reply left on screen would be a reply to a question that is gone.
          //
          // When this page held one, the whole page is re-read rather than
          // patched: the server's cascade may have removed replies this page
          // never loaded - the discussion is paged - so the total is not
          // knowable from what is in state, and arithmetic on a guess is the
          // kind of number that is wrong without looking wrong.
          const droppedReplies = answer.comments.filter(
            (comment) => comment.parentId === commentId,
          ).length

          if (droppedReplies > 0) {
            setReloadToken((token) => token + 1)
          } else {
            setAnswer({
              ...answer,
              comments: answer.comments.filter((comment) => comment.id !== commentId),
              pageInfo: {
                ...answer.pageInfo,
                totalCount: Math.max(0, answer.pageInfo.totalCount - 1),
              },
            })
          }
        }
        if (editingCommentId === commentId) setEditingCommentId(null)
        return true
      } catch {
        setWriteError({ message: TRANSPORT_FAILURE, field: null })
        return false
      } finally {
        busyRef.current = null
        setBusyCommentId(null)
      }
    },
    [answer, editingCommentId],
  )

  return {
    comments,
    pageInfo,
    loading,
    error,
    writeError,
    posting,
    busyCommentId,
    editingCommentId,
    lastPostedId,
    canPost,
    post,
    startEditing: setEditingCommentId,
    cancelEditing: () => setEditingCommentId(null),
    saveEdit,
    remove,
    clearWriteError: () => setWriteError(null),
  }
}
