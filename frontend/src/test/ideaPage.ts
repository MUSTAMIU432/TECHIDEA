import type { Idea, IdeaPage, IdeaPageInfo } from '../features/ideas/api/ideasApi'

/**
 * Fixtures for the paged shape discovery returns (S2-004).
 *
 * `list_ideas` used to answer with a bare array and a test could write
 * `mockResolvedValue([idea])`. Now the answer is a page, and a test that
 * hand-writes one is a test that has to be edited every time a field is added
 * to `PageInfo` — which is the moment a test suite stops being read as
 * "what does the server promise" and starts being read as "what fields does
 * this file happen to know about".
 *
 * The defaults here are the ones the backend sends for a single page of a
 * small result set, so a test that does not care about paging writes
 * `page([idea])` and means it.
 */

export const SINGLE_PAGE: IdeaPageInfo = {
  offset: 0,
  limit: 20,
  totalCount: 1,
  hasNextPage: false,
  hasPreviousPage: false,
}

/** A page of `items`, positioned as the backend would position it. */
export function page(items: Idea[], overrides: Partial<IdeaPageInfo> = {}): IdeaPage {
  const info = { ...SINGLE_PAGE, totalCount: items.length, ...overrides }
  return { items, pageInfo: info }
}

/**
 * A page of `total` ideas, with the given page of them on screen.
 *
 * For tests about paging, where the point is that the rows on screen and the
 * count the reader is shown are not the same number.
 */
export function pagedPage(
  items: Idea[],
  totalCount: number,
  overrides: Partial<IdeaPageInfo> = {},
): IdeaPage {
  const offset = overrides.offset ?? 0
  const limit = overrides.limit ?? 20
  return {
    items,
    pageInfo: {
      offset,
      limit,
      totalCount,
      hasNextPage: offset + limit < totalCount,
      hasPreviousPage: offset > 0,
      ...overrides,
    },
  }
}
