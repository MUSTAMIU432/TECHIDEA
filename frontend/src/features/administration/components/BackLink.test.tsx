import { fireEvent, render, screen } from '@testing-library/react'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { BackLink } from './BackLink'
import { useBackTarget } from '../hooks/useBackTarget'
import { useUrlFilters } from '../hooks/useUrlFilters'

/**
 * The way back, and the thing it depends on: a list that tells the page it opens
 * where the reader was standing.
 *
 * The failure this protects against is quiet and easy to ship - a hard-coded
 * `to="/app/admin/ideas"` looks correct, passes every test that only checks that
 * a link exists, and quietly throws away an administrator's filters. So the
 * cases below are about what the link *points at*, not whether one rendered.
 */
function ListRow() {
  const { here } = useUrlFilters()
  return (
    <Link to="/app/admin/ideas/1" state={here}>
      Automate the invoice run
    </Link>
  )
}

function Detail() {
  const backTo = useBackTarget('/app/admin/ideas')
  return <BackLink to={backTo}>All ideas</BackLink>
}

/** Mount the two pages at the addresses they really live at. */
function renderPair(from: string) {
  return render(
    <MemoryRouter initialEntries={[from]}>
      <Routes>
        <Route path="/app/admin/ideas" element={<ListRow />} />
        <Route path="/app/admin/ideas/:id" element={<Detail />} />
        <Route path="/app/admin/users" element={<ListRow />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('The way back', () => {
  it('returns to the filtered, paged list the row was opened from', () => {
    renderPair('/app/admin/ideas?status=SUBMITTED&offset=40')
    fireEvent.click(screen.getByText('Automate the invoice run'))

    // Every filter and the page number, not the list's front door: four things
    // the administrator did to get here and would otherwise have to repeat.
    expect(screen.getByRole('link', { name: 'All ideas' })).toHaveAttribute(
      'href',
      '/app/admin/ideas?status=SUBMITTED&offset=40',
    )
  })

  it('falls back to the section root for a page opened cold', () => {
    // A pasted, bookmarked or reloaded link carries no state at all, and the
    // reader still has to have a way out.
    renderPair('/app/admin/ideas/1')

    expect(screen.getByRole('link', { name: 'All ideas' })).toHaveAttribute(
      'href',
      '/app/admin/ideas',
    )
  })

  it('ignores a return address from another section', () => {
    // An idea opened from the users list is not an idea the reader wants to go
    // back to a users list from - and the link says "All ideas", so it must not
    // quietly become a lie.
    renderPair('/app/admin/users?offset=20')
    fireEvent.click(screen.getByText('Automate the invoice run'))

    expect(screen.getByRole('link', { name: 'All ideas' })).toHaveAttribute(
      'href',
      '/app/admin/ideas',
    )
  })

  it('ignores a return address that only looks like this section', () => {
    // "/app/admin/ideas-and-more" starts with the section root as a string but
    // is a different page, which is what a prefix match without the separator
    // would happily hand somebody.
    function Sneaky() {
      const backTo = useBackTarget('/app/admin/ideas')
      return <BackLink to={backTo}>All ideas</BackLink>
    }
    render(
      <MemoryRouter
        initialEntries={[
          { pathname: '/app/admin/ideas/1', state: { from: '/app/admin/ideas-and-more' } },
        ]}
      >
        <Routes>
          <Route path="/app/admin/ideas/:id" element={<Sneaky />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'All ideas' })).toHaveAttribute(
      'href',
      '/app/admin/ideas',
    )
  })

  it('is a link with a visible destination, not a button calling history', () => {
    renderPair('/app/admin/ideas/1')

    // A real `Link` can be opened in a new tab and announced as a destination;
    // `navigate(-1)` can do neither, and grows the console history every press.
    const back = screen.getByRole('link', { name: 'All ideas' })
    expect(back.tagName).toBe('A')
    expect(back).toHaveAttribute('href', '/app/admin/ideas')
  })
})
