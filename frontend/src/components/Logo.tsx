/**
 * The MUSTANET brand mark: an "M" drawn as a connected network path with
 * nodes at its joints, next to the wordmark. Decorative mark, readable name -
 * the accessible name comes from the wordmark text. Drawn for the green app
 * header: a white tile and a light wordmark.
 */
export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <svg
        aria-hidden="true"
        viewBox="0 0 32 32"
        className="h-8 w-8 shrink-0 drop-shadow-sm"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <rect width="32" height="32" rx="8" className="fill-white" />
        <path
          d="M8 23V10l8 8 8-8v13"
          className="stroke-brand-700"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="8" cy="10" r="2.25" className="fill-brand-400" />
        <circle cx="16" cy="18" r="2.25" className="fill-brand-400" />
        <circle cx="24" cy="10" r="2.25" className="fill-brand-400" />
      </svg>
      <span className="text-lg font-bold tracking-tight text-white">
        MUSTA<span className="text-brand-300">NET</span>
      </span>
    </span>
  )
}
