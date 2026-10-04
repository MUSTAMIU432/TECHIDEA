import type { IdeaComment } from '../api/ideasApi'

/**
 * One comment and the replies under it, ready to render.
 *
 * A flat list with a `parentId` is not something a reader should have to read
 * as a flat list: a reply to a question four comments further down is a
 * conversation with that question, not with the page. So the thread is grouped
 * once, here, and the component draws the group.
 */
export interface CommentThread {
  comment: IdeaComment
  /** Chronological, because that is the order the server sent them in. */
  replies: IdeaComment[]
}

/**
 * Group a page of comments into the comments it starts and the replies under
 * each of them.
 *
 * Three properties, and the first is the one that matters:
 *
 * - **Nothing is dropped and nothing is invented.** Every comment in the page
 *   appears exactly once. A reply whose parent is not in this page - the
 *   discussion is paged, so a page can begin mid-thread - is returned as its
 *   own thread rather than being held back until its parent is loaded. Hiding
 *   it would lose somebody's words; inventing a parent would be a comment that
 *   does not exist.
 * - **Order follows the server.** Threads come in the order their opening
 *   comments arrived, and replies in the order they arrived. This groups, and
 *   never sorts.
 * - **One level, because the server allows one.** `parentId` points at a
 *   top-level comment, so `replies` never nests. A reply to a reply would be
 *   refused by the server, which is the reason this is a loop over two lists
 *   rather than a recursive walk.
 */
export function groupCommentThread(comments: IdeaComment[]): CommentThread[] {
  const threads: CommentThread[] = []
  const byCommentId = new Map<string, CommentThread>()

  for (const comment of comments) {
    if (comment.parentId !== null && byCommentId.has(comment.parentId)) {
      // A reply to something in this page: it belongs under it, and not in a
      // thread of its own.
      const parent = byCommentId.get(comment.parentId)
      if (parent !== undefined) parent.replies.push(comment)
      continue
    }

    const thread: CommentThread = { comment, replies: [] }
    threads.push(thread)
    byCommentId.set(comment.id, thread)
  }

  return threads
}
