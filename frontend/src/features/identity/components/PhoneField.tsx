import { fieldErrorClasses, inputClasses } from './fieldStyles'

interface PhoneFieldProps {
  countryCode: string
  phoneNumber: string
  onCountryCodeChange: (value: string) => void
  onPhoneNumberChange: (value: string) => void
  error?: string
  disabled?: boolean
}

// A representative set of country calling codes. The platform serves users
// broadly, so this intentionally isn't limited to a single country.
const COUNTRY_CODES = [
  { code: '+255', label: 'Tanzania (+255)' },
  { code: '+254', label: 'Kenya (+254)' },
  { code: '+256', label: 'Uganda (+256)' },
  { code: '+250', label: 'Rwanda (+250)' },
  { code: '+27', label: 'South Africa (+27)' },
  { code: '+234', label: 'Nigeria (+234)' },
  { code: '+20', label: 'Egypt (+20)' },
  { code: '+1', label: 'United States / Canada (+1)' },
  { code: '+44', label: 'United Kingdom (+44)' },
  { code: '+49', label: 'Germany (+49)' },
  { code: '+33', label: 'France (+33)' },
  { code: '+91', label: 'India (+91)' },
  { code: '+86', label: 'China (+86)' },
  { code: '+81', label: 'Japan (+81)' },
  { code: '+971', label: 'United Arab Emirates (+971)' },
  { code: '+61', label: 'Australia (+61)' },
]

/** Country code + national number pair, styled as one field group. */
export function PhoneField({
  countryCode,
  phoneNumber,
  onCountryCodeChange,
  onPhoneNumberChange,
  error,
  disabled,
}: PhoneFieldProps) {
  const errorId = 'signup-phone-error'

  const labelId = 'signup-phone-label'

  return (
    // A plain, labeled group rather than <fieldset>/<legend>: browsers give
    // fieldsets a special intrinsic-sizing algorithm that ignores min-width
    // overrides and forces the whole layout to overflow on narrow viewports
    // (verified: swapping this back to <fieldset> reintroduces horizontal
    // scrolling below ~360px). role="group" + aria-labelledby gives
    // assistive tech the same grouping semantics without that quirk.
    // oxlint-disable-next-line jsx-a11y/prefer-tag-over-role
    <div role="group" aria-labelledby={labelId} className="min-w-0">
      <span id={labelId} className="mb-1.5 block text-sm font-medium text-gray-700">
        Phone number
      </span>
      <div className="flex gap-2">
        {/*
          A <select>'s own automatic minimum size is based on its widest
          <option> text, not its CSS width — unlike ordinary boxes, browsers
          don't let an explicit width or min-width:0 reduce it in a flex
          context. Sizing this wrapper instead, with overflow-hidden and
          min-w-0, and letting the select fill it with w-full keeps the
          select from forcing the row (and the whole page) wider than the
          viewport on narrow screens.
        */}
        <div className="w-32 shrink-0 overflow-hidden">
          <select
            aria-label="Country code"
            value={countryCode}
            onChange={(event) => onCountryCodeChange(event.target.value)}
            disabled={disabled}
            className={`${inputClasses(Boolean(error))} w-full min-w-0`}
          >
            {COUNTRY_CODES.map((option) => (
              <option key={option.code} value={option.code}>
                {option.code}
              </option>
            ))}
          </select>
        </div>
        <input
          id="signup-phone-number"
          name="signup-phone-number"
          type="tel"
          inputMode="tel"
          autoComplete="tel-national"
          placeholder="7XX XXX XXX"
          value={phoneNumber}
          onChange={(event) => onPhoneNumberChange(event.target.value)}
          disabled={disabled}
          aria-label="Phone number"
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          className={`${inputClasses(Boolean(error))} min-w-0 flex-1`}
        />
      </div>
      {error && (
        <p id={errorId} role="alert" className={fieldErrorClasses}>
          {error}
        </p>
      )}
    </div>
  )
}
