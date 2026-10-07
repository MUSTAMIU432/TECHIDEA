import type { PipelineStage } from '../api/automationApi'
import { PIPELINE_LABELS } from '../utils/labels'

const MARK: Record<PipelineStage['state'], string> = { done: '✓', current: '●', pending: '○' }
const STYLE: Record<PipelineStage['state'], string> = {
  done: 'text-emerald-700',
  current: 'text-brand-700 font-semibold',
  pending: 'text-gray-400',
}
const STATE_WORD: Record<PipelineStage['state'], string> = {
  done: 'done',
  current: 'in progress',
  pending: 'not started',
}

/** The delivery pipeline as it really stands, drawn from the project's own records. */
export function Pipeline({ stages }: { stages: PipelineStage[] }) {
  return (
    <ol
      aria-label="Delivery pipeline"
      className="grid gap-x-6 gap-y-1.5 sm:grid-cols-3 lg:grid-cols-5"
    >
      {stages.map((s) => (
        <li key={s.stage} className={`flex items-center gap-2 text-sm ${STYLE[s.state]}`}>
          <span aria-hidden="true" className="w-4 text-center">
            {MARK[s.state]}
          </span>
          <span>{PIPELINE_LABELS[s.stage] ?? s.stage}</span>
          <span className="sr-only">({STATE_WORD[s.state]})</span>
        </li>
      ))}
    </ol>
  )
}
