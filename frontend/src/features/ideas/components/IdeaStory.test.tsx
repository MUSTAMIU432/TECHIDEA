import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { EMPTY_PROBLEM_STORY, storySections } from '../utils/problemStory'
import { IdeaStory } from './IdeaStory'

describe('storySections', () => {
  it('is empty for an unanswered story', () => {
    expect(storySections(EMPTY_PROBLEM_STORY)).toEqual([])
  })

  it('groups answers as the form asks them, in plain words', () => {
    const sections = storySections({
      ...EMPTY_PROBLEM_STORY,
      performedBy: 'Office staff',
      frequency: 'SEVERAL_TIMES_A_WEEK',
      impacts: ['WAITING', 'HIGHER_COSTS'],
      desiredOutcome: 'Submit once.',
    })

    expect(sections).toEqual([
      {
        title: 'The impact',
        answers: [
          { question: 'Who usually does this', answer: 'Office staff' },
          { question: 'How often it happens', answer: 'Several times a week' },
          {
            question: 'What happens because of it',
            answer: 'People have to wait, It increases costs',
          },
        ],
      },
      {
        title: 'What should improve',
        answers: [{ question: 'What should happen instead', answer: 'Submit once.' }],
      },
    ])
  })

  it('shows what else is used in the author’s words in place of "Other"', () => {
    const [today] = storySections({
      ...EMPTY_PROBLEM_STORY,
      currentTools: ['PAPER_FORMS', 'OTHER'],
      currentToolsOther: 'A notice board',
    })

    expect(today.answers).toEqual([
      { question: 'What is used today', answer: 'Paper forms, A notice board' },
    ])
  })

  it('keeps a bare "Other" when nothing else was said', () => {
    const [today] = storySections({ ...EMPTY_PROBLEM_STORY, currentTools: ['OTHER'] })

    expect(today.answers[0].answer).toBe('Other')
  })

  it('counts zero people as an answer', () => {
    const [impact] = storySections({ ...EMPTY_PROBLEM_STORY, peopleInvolved: 0 })

    expect(impact.answers).toEqual([{ question: 'People involved', answer: 'About 0' }])
  })
})

describe('IdeaStory', () => {
  it('renders nothing for an unanswered story', () => {
    const { container } = render(<IdeaStory story={EMPTY_PROBLEM_STORY} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('renders author text as text, never as markup', () => {
    render(
      <IdeaStory
        story={{ ...EMPTY_PROBLEM_STORY, importantConsiderations: '<b>Private</b> details' }}
      />,
    )

    const region = screen.getByRole('region', { name: 'The problem in detail' })
    expect(within(region).getByText('<b>Private</b> details')).toBeInTheDocument()
    expect(region.querySelector('b')).toBeNull()
  })
})
