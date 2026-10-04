import { describe, expect, it } from 'vitest'

import { groupCommentThread } from './commentThread'
import type { IdeaComment } from '../api/ideasApi'

/**
 * Grouping a page of comments into threads.
 *
 * The properties worth pinning are the ones a component test cannot reach: a
 * discussion is *paged*, so the interesting case is a page whose parent is on
 * another page, and the only honest way to test that is to hand this function
 * a page that begins mid-thread.
 */
function comment(
  id: string,
  parentId: string | null = null,
  content = `Comment ${id}`,
): IdeaComment {
  return {
    id,
    ideaId: '1',
    authorId: '7',
    content,
    parentId,
    createdAt: '2026-02-01T09:00:00.000Z',
    updatedAt: '2026-02-01T09:00:00.000Z',
  }
}

describe('groupCommentThread', () => {
  it('puts each reply under the comment it answers', () => {
    const threads = groupCommentThread([
      comment('c1'),
      comment('c2', 'c1', 'An answer.'),
      comment('c3'),
      comment('c4', 'c3', 'Another answer.'),
    ])

    expect(threads.map((thread) => thread.comment.id)).toEqual(['c1', 'c3'])
    expect(threads[0].replies.map((reply) => reply.id)).toEqual(['c2'])
    expect(threads[1].replies.map((reply) => reply.id)).toEqual(['c4'])
  })

  it('keeps replies in the order the server sent them', () => {
    const threads = groupCommentThread([
      comment('c1'),
      comment('c2', 'c1', 'First answer.'),
      comment('c3', 'c1', 'Second answer.'),
    ])

    expect(threads[0].replies.map((reply) => reply.content)).toEqual([
      'First answer.',
      'Second answer.',
    ])
  })

  it('shows a reply whose parent is on another page as its own thread', () => {
    /*
      The important one. A page can start in the middle of a thread, because
      the discussion is paged - and dropping the reply would lose somebody's
      words, while inventing a parent would be a comment the server never
      sent. So it stands alone until its parent is on the page.
    */
    const threads = groupCommentThread([comment('c9', 'c1', 'An answer to something above.')])

    expect(threads).toHaveLength(1)
    expect(threads[0].comment.id).toBe('c9')
    expect(threads[0].comment.parentId).toBe('c1')
    expect(threads[0].replies).toEqual([])
  })

  it('never loses or repeats a comment', () => {
    const comments = [
      comment('c1'),
      comment('c2', 'c1'),
      comment('c3'),
      comment('c4', 'c3'),
      comment('c5', 'unknown'),
    ]

    const rendered = groupCommentThread(comments).flatMap((thread) => [
      thread.comment.id,
      ...thread.replies.map((reply) => reply.id),
    ])

    expect(rendered.sort()).toEqual(['c1', 'c2', 'c3', 'c4', 'c5'])
  })

  it('returns nothing for an empty page', () => {
    expect(groupCommentThread([])).toEqual([])
  })
})
