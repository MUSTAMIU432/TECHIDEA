const JOURNEY_STEPS = [
  {
    step: '01',
    title: 'Discover',
    description: 'Find problems worth automating.',
  },
  {
    step: '02',
    title: 'Validate',
    description: 'Turn ideas into structured opportunities.',
  },
  {
    step: '03',
    title: 'Build',
    description: 'Connect ideas with developers and teams.',
  },
  {
    step: '04',
    title: 'Measure',
    description: 'Understand the impact of automation.',
  },
]

/** Minimal original wordmark: an initials badge plus the product name. */
export function BrandMark() {
  return (
    <div className="flex items-center gap-2.5">
      <span
        aria-hidden="true"
        className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-400 to-brand-600 text-xs font-bold tracking-wide text-white shadow-sm shadow-brand-950/40"
      >
        AP
      </span>
      <span className="text-base font-semibold tracking-tight text-white">Automation Platform</span>
    </div>
  )
}

/**
 * Subtle node/connection network, evoking automation and workflow without
 * competing with the foreground text. Purely decorative and static — no
 * animation, kept behind the content via DOM order.
 */
function NetworkVisual() {
  return (
    <svg
      aria-hidden="true"
      className="absolute inset-0 h-full w-full text-brand-400/20"
      viewBox="0 0 400 600"
      preserveAspectRatio="none"
    >
      <g stroke="currentColor" strokeWidth="1" fill="none">
        <path d="M20 60 L160 140 L120 280 L260 340 L220 480" />
        <path d="M380 40 L260 150 L340 260 L200 360 L260 520" />
        <path d="M160 140 L340 260" strokeOpacity="0.6" />
        <path d="M120 280 L20 60" strokeOpacity="0.4" />
      </g>
      <g fill="currentColor">
        <circle cx="20" cy="60" r="3" />
        <circle cx="160" cy="140" r="4" />
        <circle cx="120" cy="280" r="3" />
        <circle cx="260" cy="340" r="4" />
        <circle cx="220" cy="480" r="3" />
        <circle cx="380" cy="40" r="3" />
        <circle cx="340" cy="260" r="4" />
        <circle cx="200" cy="360" r="3" />
        <circle cx="260" cy="520" r="3" />
      </g>
    </svg>
  )
}

/**
 * Left brand panel for /auth: a dark, technology-forward identity for
 * Automation Platform. Desktop-only (the mobile layout uses a condensed
 * header instead, rendered by AuthPage).
 */
export function AuthBrandPanel() {
  return (
    <div className="relative hidden overflow-hidden bg-gradient-to-br from-brand-950 via-brand-900 to-brand-950 lg:flex lg:flex-col lg:justify-between lg:px-12 lg:py-14 xl:px-16">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0">
        <div className="absolute -left-16 -top-24 h-72 w-72 rounded-full bg-brand-500/25 blur-3xl" />
        <div className="absolute -right-10 bottom-0 h-96 w-96 rounded-full bg-brand-400/15 blur-3xl" />
        <NetworkVisual />
      </div>

      <div className="relative">
        <BrandMark />
      </div>

      <div className="relative max-w-md">
        <h2 className="text-3xl font-semibold leading-[1.15] tracking-tight text-white xl:text-[2.5rem]">
          Turn problems into automation opportunities.
        </h2>
        <p className="mt-4 max-w-sm text-[15px] leading-relaxed text-brand-100/70">
          Discover inefficiencies, validate ideas, and build automation that creates measurable
          impact.
        </p>
        <ul className="mt-10 space-y-6">
          {JOURNEY_STEPS.map((item) => (
            <li key={item.step} className="flex gap-4">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-brand-400/30 bg-white/5 text-xs font-semibold text-brand-200">
                {item.step}
              </span>
              <div>
                <p className="text-sm font-semibold uppercase tracking-wide text-brand-200">
                  {item.title}
                </p>
                <p className="mt-1 text-sm text-brand-100/70">{item.description}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <p className="relative text-xs text-brand-100/40">
        &copy; {new Date().getFullYear()} Automation Platform
      </p>
    </div>
  )
}
