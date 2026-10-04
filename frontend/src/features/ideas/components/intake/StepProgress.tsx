import type { Step } from './formModel'

type StepState = 'current' | 'answered' | 'error' | 'untouched'

/**
 * The status pill on a step card, once there is something to say: the step
 * is started, or needs fixing. An untouched step shows none - its status
 * line already says it is not answered yet.
 */
function StatusPill({ state, answered }: { state: StepState; answered: boolean }) {
  if (state !== 'error' && !answered) return null
  const [label, classes] =
    state === 'error'
      ? ['Fix', 'bg-red-50 text-red-700 ring-red-200']
      : ['Started', 'bg-brand-50 text-brand-700 ring-brand-200']
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-[0.7rem] font-semibold ring-1 ${classes}`}
    >
      {label}
    </span>
  )
}

/** The line under a step's title; none for a step not answered yet. */
function statusLine(state: StepState, answered: boolean): string | null {
  if (state === 'current') return 'Currently open'
  if (state === 'error') return 'Needs attention'
  return answered ? 'Answered' : null
}

/**
 * Where the author is in the guided flow, and a way to jump to any step.
 *
 * Every step is reachable at any time, forwards or backwards: only the first
 * one holds anything a draft needs, so gating the others would make the
 * author answer questions in an order that suits the form rather than them.
 * The values live in the form, not in the steps, so moving never loses an
 * answer.
 *
 * Wide, it is a frosted sidebar of step cards, each with its status; narrow (the edit panel beside the ideas list), a row of
 * numbered buttons under a progress bar. The same buttons either way, each
 * named in full for assistive technology whichever way it is drawn.
 */
export function StepProgress({
  steps,
  current,
  states,
  answered,
  onSelect,
  disabled = false,
}: {
  steps: readonly Step[]
  current: number
  states: readonly StepState[]
  /** Whether anything on each step is answered, including the current one. */
  answered: readonly boolean[]
  onSelect: (index: number) => void
  disabled?: boolean
}) {
  const answeredCount = answered.filter(Boolean).length

  return (
    <nav aria-label="Form progress">
      <p className="hidden text-xs font-semibold tracking-[0.18em] text-brand-600 uppercase @3xl:block">
        Your problem story
      </p>
      <div className="flex items-center justify-between gap-3 @3xl:mt-3">
        <p className="text-sm font-semibold text-gray-900">
          Step {current + 1} of {steps.length}
        </p>
        <span className="rounded-full bg-brand-600 px-2.5 py-0.5 text-xs font-semibold text-white shadow-sm">
          <span aria-hidden="true">
            {answeredCount}/{steps.length}
          </span>
          <span className="sr-only">
            {answeredCount} of {steps.length} steps started
          </span>
        </span>
      </div>
      <progress
        value={current + 1}
        max={steps.length}
        aria-label={`Step ${current + 1} of ${steps.length}: ${steps[current].title}`}
        className="mt-2 block h-1.5 w-full appearance-none overflow-hidden rounded-full bg-gray-200 [&::-moz-progress-bar]:bg-brand-600 [&::-webkit-progress-bar]:bg-gray-200 [&::-webkit-progress-value]:bg-brand-600 [&::-webkit-progress-value]:motion-safe:transition-[width]"
      />
      <ol className="mt-4 flex flex-wrap gap-2 @3xl:mt-4 @3xl:flex-1 @3xl:flex-col @3xl:gap-1.5">
        {steps.map((step, index) => {
          const state = states[index]
          const status = statusLine(state, answered[index])
          const hasPill = state === 'error' || answered[index]
          return (
            <li key={step.title}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onSelect(index)}
                aria-current={state === 'current' ? 'step' : undefined}
                aria-label={`Step ${index + 1}: ${step.title}${
                  state === 'error'
                    ? ' (needs attention)'
                    : state === 'answered'
                      ? ' (started)'
                      : ''
                }`}
                className={`group flex items-center gap-3 rounded-full text-left text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed motion-safe:transition-colors @3xl:w-full @3xl:rounded-xl @3xl:border @3xl:px-3 @3xl:py-2 @3xl:shadow-sm @3xl:backdrop-blur-sm ${
                  state === 'current'
                    ? '@3xl:border-brand-300 @3xl:bg-brand-50/85 @3xl:shadow-brand-900/5 @3xl:ring-1 @3xl:ring-brand-200'
                    : state === 'error'
                      ? '@3xl:border-red-200 @3xl:bg-white/80'
                      : '@3xl:border-white/80 @3xl:bg-white/70 @3xl:hover:border-brand-200 @3xl:hover:bg-white/90'
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ring-1 ${
                    state === 'current'
                      ? 'bg-brand-600 text-white ring-brand-600'
                      : state === 'error'
                        ? 'bg-red-50 text-red-700 ring-red-300'
                        : state === 'answered'
                          ? 'bg-brand-50 text-brand-700 ring-brand-200'
                          : 'bg-white text-gray-500 ring-gray-300'
                  }`}
                >
                  {state === 'error' ? '!' : index + 1}
                </span>
                <span aria-hidden="true" className="hidden min-w-0 flex-1 @3xl:block">
                  <span
                    className={`block leading-5 font-semibold ${
                      state === 'current' ? 'text-brand-800' : 'text-gray-900'
                    }`}
                  >
                    {step.title}
                  </span>
                  {(status || hasPill) && (
                    <span className="mt-1 flex items-center justify-between gap-2">
                      <span
                        className={`text-xs ${state === 'error' ? 'text-red-700' : 'text-gray-500'}`}
                      >
                        {status}
                      </span>
                      <StatusPill state={state} answered={answered[index]} />
                    </span>
                  )}
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

export type { StepState }
