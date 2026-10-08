/**
 * Words for the automation lifecycle. The server's values are machine words
 * (`ready_for_assignment`); a person reads "Ready for assignment". Anything not
 * listed falls back to a readable version of the value rather than the raw one.
 */

const SPECIAL: Record<string, string> = {
  uat: 'UAT',
  ready_for_uat: 'Ready for UAT',
  not_run: 'Not run',
  todo: 'To do',
  in_progress: 'In progress',
}

export function label(value: string | null | undefined): string {
  if (!value) return ''
  if (SPECIAL[value]) return SPECIAL[value]
  const text = value.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export const CONTEXT_LABEL: Record<string, string> = {
  individual: 'Individual idea',
  team: 'Team idea',
  organization: 'Organization idea',
}

type Tone = 'neutral' | 'info' | 'good' | 'warn' | 'bad'

const TONES: Record<string, Tone> = {
  draft: 'neutral',
  discovery: 'info',
  requirements: 'info',
  solution_design: 'info',
  proposal: 'info',
  ready_for_assignment: 'warn',
  assigned: 'info',
  project_created: 'info',
  completed: 'good',
  cancelled: 'bad',
  planning: 'neutral',
  active: 'info',
  testing: 'info',
  uat: 'warn',
  deployed: 'good',
  open: 'neutral',
  satisfied: 'good',
  rejected: 'bad',
  ready: 'info',
  submitted: 'warn',
  accepted: 'good',
  changes_requested: 'warn',
  pass: 'good',
  passed: 'good',
  fail: 'bad',
  failed: 'bad',
  blocked: 'bad',
  successful: 'good',
  rolled_back: 'bad',
  recorded: 'good',
  pending: 'neutral',
  unavailable: 'neutral',
  done: 'good',
  high: 'bad',
  medium: 'warn',
  low: 'neutral',
}

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'bg-gray-100 text-gray-700',
  info: 'bg-sky-50 text-sky-800',
  good: 'bg-emerald-50 text-emerald-800',
  warn: 'bg-amber-50 text-amber-900',
  bad: 'bg-red-50 text-red-800',
}

export function badgeClasses(value: string): string {
  return TONE_CLASSES[TONES[value] ?? 'neutral']
}

/** The seven requirement kinds, in the order a form offers them. */
export const REQUIREMENT_TYPES = [
  'business',
  'functional',
  'technical',
  'security',
  'integration',
  'data',
  'compliance',
] as const

export const PIPELINE_LABELS: Record<string, string> = {
  requirements: 'Requirements',
  solution: 'Solution',
  proposal: 'Proposal',
  assignment: 'Assignment',
  development: 'Development',
  testing: 'Testing',
  uat: 'UAT',
  deployment: 'Deployment',
  impact: 'Impact',
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleDateString(undefined, { dateStyle: 'medium' })
}
