/**
 * Shared Tailwind classes for the auth form controls, so every field looks
 * consistent. Deliberately excludes any width utility: Tailwind resolves
 * conflicting width classes by their order in the generated stylesheet, not
 * by the order they're concatenated here, so callers add their own width
 * (`w-full`, `flex-1`, a fixed size, ...) instead of relying on override order.
 */
interface InputClassOptions {
  /** A `<textarea>`: its height comes from `rows`, not a fixed `h-11`. */
  multiline?: boolean
  /** A roomier field with larger text, for the main fields of a long form. */
  large?: boolean
}

export function inputClasses(
  hasError = false,
  { multiline = false, large = false }: InputClassOptions = {},
): string {
  const height = multiline ? (large ? 'py-3' : 'py-2.5') : large ? 'h-12' : 'h-11'
  return [
    // border-gray-300 (rather than the barely-there gray-200) gives each
    // field a clearly visible outline against the white card, so the form
    // reads at a glance instead of needing a squint.
    `block ${height} rounded-lg border bg-white px-3.5 text-gray-900 shadow-sm`,
    large ? 'text-base' : 'text-sm',
    'placeholder:text-gray-400 motion-safe:transition-colors motion-safe:duration-150',
    'focus:outline-none focus:ring-2',
    hasError
      ? 'border-red-400 hover:border-red-400 focus:border-red-500 focus:ring-red-200'
      : 'border-gray-300 hover:border-gray-400 focus:border-brand-500 focus:ring-brand-200',
    'disabled:cursor-not-allowed disabled:border-gray-200 disabled:bg-gray-50 disabled:text-gray-400',
  ].join(' ')
}

export const labelClasses = 'mb-1.5 block text-sm font-semibold text-gray-800'

export const fieldErrorClasses = 'mt-1.5 text-sm text-red-600'
