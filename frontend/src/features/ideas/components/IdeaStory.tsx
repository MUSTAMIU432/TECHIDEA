import type { IdeaProblemStory } from '../api/ideasApi'
import { storySections } from '../utils/problemStory'

/**
 * The author's problem story, as a reader sees it: every answered question,
 * in the order the intake form asks them, and nothing for the questions left
 * blank. Rendered as text - never as markup - like every other piece of
 * author content.
 *
 * Shows only what the server returned for an idea the reader may already
 * see; it adds no data and makes no access decision of its own.
 */
export function IdeaStory({ story }: { story: IdeaProblemStory }) {
  const sections = storySections(story)
  if (sections.length === 0) return null

  return (
    <section aria-label="The problem in detail" className="mt-4 space-y-4">
      {sections.map((section) => (
        <div key={section.title}>
          <h4 className="text-sm font-semibold text-gray-900">{section.title}</h4>
          <dl className="mt-1.5 space-y-2">
            {section.answers.map(({ question, answer }) => (
              <div key={question}>
                <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
                  {question}
                </dt>
                <dd className="mt-0.5 text-sm leading-6 whitespace-pre-line text-gray-700">
                  {answer}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </section>
  )
}
