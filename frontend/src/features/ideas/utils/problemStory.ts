import type { IdeaCurrentTool, IdeaFrequency, IdeaImpact, IdeaProblemStory } from '../api/ideasApi'

/**
 * Plain-language wording for the problem story's closed vocabularies, and the
 * questions each answer belongs to.
 *
 * The *values* are the backend's enums (`ideas.models.Idea.Frequency`,
 * `Impact`, `CurrentTool`) and the server refuses anything else; the *words*
 * are presentation and live here, the same split `SELECTABLE_VISIBILITIES`
 * makes. Shared by the intake form, which asks the questions, and
 * `IdeaStory`, which shows a reader the answers - so a question is always
 * shown back in the words it was asked in.
 */

/** A story with nothing answered: what a new idea starts from. */
export const EMPTY_PROBLEM_STORY: Readonly<IdeaProblemStory> = {
  currentProcess: '',
  currentTools: [],
  currentToolsOther: '',
  performedBy: '',
  affectedPeople: '',
  frequency: null,
  timeRequired: '',
  peopleInvolved: null,
  impacts: [],
  impactDetails: '',
  improvementGoal: '',
  desiredOutcome: '',
  easierForPeople: '',
  expectedBenefit: '',
  importantConsiderations: '',
}

interface Option<T extends string> {
  value: T
  label: string
}

export const FREQUENCY_OPTIONS: ReadonlyArray<Option<IdeaFrequency>> = [
  { value: 'SEVERAL_TIMES_A_DAY', label: 'Several times a day' },
  { value: 'DAILY', label: 'Daily' },
  { value: 'SEVERAL_TIMES_A_WEEK', label: 'Several times a week' },
  { value: 'WEEKLY', label: 'Weekly' },
  { value: 'MONTHLY', label: 'Monthly' },
  { value: 'OCCASIONALLY', label: 'Occasionally' },
  { value: 'OTHER', label: 'Other' },
]

export const IMPACT_OPTIONS: ReadonlyArray<Option<IdeaImpact>> = [
  { value: 'TOO_MUCH_TIME', label: 'It takes too much time' },
  { value: 'REPEATED_WORK', label: 'People have to repeat the same work' },
  { value: 'MISTAKES', label: 'Mistakes happen' },
  { value: 'WAITING', label: 'People have to wait' },
  { value: 'DELAYS', label: 'Work gets delayed' },
  { value: 'OVERLOAD', label: 'People become overloaded' },
  { value: 'LOST_INFORMATION', label: 'Information gets lost' },
  { value: 'COMPLAINTS', label: 'Customers, students or users complain' },
  { value: 'HIGHER_COSTS', label: 'It increases costs' },
  { value: 'OTHER', label: 'Other' },
]

export const CURRENT_TOOL_OPTIONS: ReadonlyArray<Option<IdeaCurrentTool>> = [
  { value: 'PAPER_FORMS', label: 'Paper forms' },
  { value: 'EXCEL', label: 'Excel' },
  { value: 'GOOGLE_SHEETS', label: 'Google Sheets' },
  { value: 'EMAIL', label: 'Email' },
  { value: 'WHATSAPP', label: 'WhatsApp' },
  { value: 'PHONE_CALLS', label: 'Phone calls' },
  { value: 'WEBSITE', label: 'Website' },
  { value: 'MOBILE_APP', label: 'Mobile application' },
  { value: 'COMPUTER_PROGRAM', label: 'Computer program' },
  { value: 'PHYSICAL_FILES', label: 'Physical files' },
  { value: 'OTHER', label: 'Other' },
]

/** Rough answers to "how much time does it take", offered as one-tap fills. */
export const TIME_SUGGESTIONS: readonly string[] = [
  'A few minutes',
  'About 30 minutes',
  'Several hours',
  'Most of the day',
  'Several days',
]

/**
 * Bounds mirrored from `ideas.services` (and the model's column lengths) so
 * an input can stop at the limit instead of being refused after a round
 * trip. The server's are the ones that count.
 */
export const STORY_LIMITS = {
  longAnswer: 5000,
  shortAnswer: 300,
  timeRequired: 120,
  currentToolsOther: 200,
  maxPeopleInvolved: 1_000_000,
} as const

function labelOf<T extends string>(options: ReadonlyArray<Option<T>>, value: T): string {
  return options.find((option) => option.value === value)?.label ?? value
}

/** One answered question, ready to show a reader. */
export interface StoryAnswer {
  question: string
  answer: string
}

/** The sections of the story, in the order the form asks them. */
export interface StorySection {
  title: string
  answers: StoryAnswer[]
}

/**
 * The answered parts of a story, grouped the way the form asks them and
 * with every unanswered question left out - a reader should see what the
 * author said, not a list of blanks.
 */
export function storySections(story: IdeaProblemStory): StorySection[] {
  const tools = [
    ...story.currentTools
      .filter((tool) => tool !== 'OTHER')
      .map((tool) => labelOf(CURRENT_TOOL_OPTIONS, tool)),
    ...(story.currentToolsOther.trim()
      ? [story.currentToolsOther.trim()]
      : story.currentTools.includes('OTHER')
        ? ['Other']
        : []),
  ]
  const impacts = story.impacts.map((impact) => labelOf(IMPACT_OPTIONS, impact))

  const sections: Array<{ title: string; answers: Array<[string, string]> }> = [
    {
      title: 'How it happens today',
      answers: [
        ['How it is handled now', story.currentProcess],
        ['What is used today', tools.join(', ')],
      ],
    },
    {
      title: 'The impact',
      answers: [
        ['Who usually does this', story.performedBy],
        ['Who is affected', story.affectedPeople],
        [
          'How often it happens',
          story.frequency ? labelOf(FREQUENCY_OPTIONS, story.frequency) : '',
        ],
        ['How much time it takes', story.timeRequired],
        [
          'People involved',
          story.peopleInvolved === null ? '' : `About ${story.peopleInvolved.toLocaleString()}`,
        ],
        ['What happens because of it', impacts.join(', ')],
        ['Anything else that happens', story.impactDetails],
      ],
    },
    {
      title: 'What should improve',
      answers: [
        ['What should be different', story.improvementGoal],
        ['What should happen instead', story.desiredOutcome],
        ['What people should be able to do more easily', story.easierForPeople],
      ],
    },
    {
      title: 'How we will know it is solved',
      answers: [['What would be better', story.expectedBenefit]],
    },
    {
      title: 'Important to know',
      answers: [['Things to consider', story.importantConsiderations]],
    },
  ]

  return sections
    .map(({ title, answers }) => ({
      title,
      answers: answers
        .filter(([, answer]) => answer.trim() !== '')
        .map(([question, answer]) => ({ question, answer })),
    }))
    .filter((section) => section.answers.length > 0)
}
