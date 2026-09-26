# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — Sprint 2: Ideas & Problem Submission

- S2-001: Ideas domain architecture — the `ideas` app, the platform's first
  business domain beyond Identity & Access. Five entities: `Category`
  (platform-wide, flat, reusable, retired rather than deleted once used),
  `Idea`, `Comment`, `Vote` and `Attachment`, plus their migration and admin
  registrations. Scope is deliberately schema-only: no service, selector,
  GraphQL operation or UI ships in this item, and none is stubbed. The
  design and the planned seams for S2-002 are in `docs/ideas-domain.md`.
  - **Lifecycle** — the full vocabulary (`draft`, `submitted`,
    `under_review`, `changes_requested`, `rejected`, `approved`,
    `automation_proposal`) is established so implementing the review
    workflow in Sprint 3 is behaviour, not a migration. Only `DRAFT →
    SUBMITTED` belongs to Sprint 2. `status` and `visibility` are enforced by
    database `CHECK` constraints *as well as* `choices`, because `choices`
    only covers `full_clean()` and `bulk_create`/`QuerySet.update`/a
    management command all bypass it; both are generated from one definition
    so they cannot drift.
  - **Visibility** — `public`/`organization`/`department`/`private`,
    defaulting to `private` so a new submission is visible to nobody until
    somebody deliberately widens it. `department` is a **reserved value**,
    not a half-built feature: the platform has no Department model, and
    inventing one inside `ideas` would have been a different sprint's work.
    What enforcement will need is recorded in the domain doc.
  - **No second authorization system.** `ideas` adds no permission codes and
    no `permissions.py`; membership and organization permissions stay in
    `organizations/authorization.py`. The one Ideas-specific decision -
    which ideas a user may *read* - is a visibility filter in the future
    selector layer, built on `get_membership`, not a parallel permission
    hierarchy.
  - **Tenancy** — `Idea.organization` is required on every row, so there is
    no idea that exists outside a tenant and a forgotten tenant filter cannot
    reach anything. `Category` is deliberately *not* tenant-scoped: it is
    what makes ideas comparable across organizations later.
  - **Storage boundary** — `Attachment` is metadata only
    (`storage_key`, `filename`, `content_type`, `size`) and holds no file
    field of any kind, so attachment bytes cannot end up in PostgreSQL. The
    bucket, presigned uploads and signed downloads are later work. A test
    asserts no `FileField`/`BinaryField`/`DataField` ever appears.
  - **Deletion behaviour** — `Idea.category` is `PROTECT`, because an idea
    that has been reviewed must not vanish when the category list is tidied;
    `is_active=False` is the supported retirement. `organization` and the
    child rows cascade, matching `Membership`'s rule in Sprint 1.
  - **Indexes** — five composite indexes shaped like the queries Sprint 2
    will actually make (organization feed, organization+status,
    category, visibility, author), and the three `Idea` foreign keys that
    lead one of them have Django's automatic single-column index switched
    off, since it would be a strict prefix of an index PostgreSQL could
    already use. Asserted by tests so the two cannot diverge.
  - A test asserts the app's model set is exactly these five, so a review,
    proposal or developer model arriving here fails the build.

### Added — Sprint 1: Identity & Access

- S1-002: User registration — the `identity` app's `User` model (email as
  the login identifier, Django's own password hashing) and a `register`
  GraphQL mutation with email normalization and password/phone validation.
  See `backend/identity/models.py` and `services.py`.
- S1-003: Email/password authentication — `login`/`refreshToken`/`logout`
  mutations, short-lived JWT access tokens held in memory only, and a
  persistent, rotating `RefreshSession` credential delivered as an HttpOnly
  cookie (never `localStorage`). `me` resolves the current user from the
  access token alone. `CORS_ALLOW_CREDENTIALS` is on for the cookie, still
  scoped to an explicit, non-wildcard origin allow-list.
- S1-004: Google sign-in — the `googleLogin` mutation over Google Identity
  Services' ID-token (OIDC) flow rather than an authorization-code exchange,
  so no client secret exists on either side. Credential verification
  (`backend/identity/google_oauth.py`) covers signature, issuer, audience
  and expiry, plus one-time-use replay protection keyed by the token's
  hash. **Account-linking policy: refuse, never link** — only an exact
  `(provider, provider_subject)` match authenticates into an existing
  account; a matching email is refused with the same generic message as an
  invalid credential, *regardless* of `email_verified`. Auto-linking by
  verified email was considered and rejected; the reasoning is recorded in
  `identity/authentication.py`.
- S1-005: Current user and protected application — the `me` query as the
  single authoritative identity source (no arguments, so no client value can
  influence it), `RequireAuth` gating `/app`, and the dashboard shell.
- S1-006: Organization membership — `Organization`/`Membership` models, the
  `createOrganization` bootstrap mutation, and `meOrganizations`/
  `meMemberships`. The bootstrap is deliberately gated on authentication
  alone: requiring a membership permission there would be circular, since
  the membership does not exist until the organization does.
- S1-007: Roles and permissions — `Role`, `Permission` and `MembershipRole`,
  all organization-scoped, with a system `Owner` role granted the full
  permission set at creation.
- S1-008: Authorization and tenant isolation — every organization-scoped
  operation resolves the caller's active membership and derives permission
  from it (`organizations/authorization.py`); nothing reads authorization
  input from the request. Cross-tenant requests return nothing and never
  confirm the target exists.
- S1-009: Sprint 1 integration and security hardening —
  - Fixed the backend Google configuration: `GOOGLE_OAUTH_CLIENT_ID` was
    read by the code while the local `backend/.env` stored the value under
    the wrong name, silently disabling Google sign-in. Corrected locally,
    documented in `backend/.env.example`, and pinned by tests that assert
    the exact variable name and that a misnamed one is ignored.
  - Removed a real Google client id from `frontend/.env.example`; the
    template is a placeholder again and a test rejects any real-looking
    client id in either template.
  - Added a shared cache abstraction: three separated aliases
    (`default`, `replay_protection`, `auth_throttle`) selected by
    `CACHE_URL`, which is now **required** in deployed environments and
    refused if it resolves to a per-process backend. Google replay
    protection and the new rate limits are correct across processes because
    of it, and both fail closed if the cache is unreachable.
  - Added authentication rate limiting (`identity/throttling.py`) on
    `login` (per account *and* per client), `googleLogin` (per client),
    `refreshToken` (per credential *and* per client) and `register` (per
    client), checked before the expensive work, with hashed subjects, a
    shared counter, recovery on window expiry, and no change to the generic
    authentication messages.
  - Reworked the frontend's access-token lifecycle: `tokenStore` holds the
    expiry alongside the token, and the new `tokenRefresh` refreshes
    proactively before expiry through a *single* in-flight promise (the
    refresh cookie is single-use, so overlapping refreshes would revoke
    each other), treats a failed refresh as terminal for the session
    instead of retrying, and writes nothing to browser storage.
  - Wired `SignUpForm` to the real `register` mutation, mapping the
    backend's field-level errors onto the matching fields and replacing the
    fake timeout. Registration does not authenticate, so success shows a
    confirmation leading to sign-in.
  - Added end-to-end HTTP integration suites: the complete authentication
    journey (cookie attributes, refresh rotation, replay rejection, logout,
    post-logout refresh), tenant isolation across two organizations, the
    Google sign-in flow end to end, the organization bootstrap model, the
    CSRF posture of `/graphql/`, and a two-sided GraphQL schema contract
    check shared with the frontend.
  - `.coveragerc` now includes `organizations`, so coverage reflects
    S1-006/S1-007/S1-008.
  - A refused Google account-linking collision is now reported
    specifically: a caller whose credential Google verified as
    `email_verified: true` is told the address already has an account, with
    the two real next steps, instead of a bare "Could not sign in with
    Google." with nothing to act on. The **refusal is unchanged** - Google
    sign-in still never auto-links by email, verified or not. The disclosure
    is gated on `email_verified` precisely so it cannot become an
    account-existence oracle: nobody can present a Google credential for an
    address they do not control, so the only addresses anyone can get an
    answer about are their own. Every other failure keeps the single generic
    message, and one message covers the whole collision class so a caller
    cannot learn whether the existing account is password-registered or
    belongs to a different Google identity. See
    `identity/authentication.py`'s `GoogleEmailInUseError`.
  - A transport failure during sign-in no longer rejects or strands the
    form. `login`/`loginWithGoogle` now always resolve to an outcome, so a
    backend that is simply not running shows an actionable error and
    re-enables the form instead of leaving the button spinning and logging
    an uncaught rejection. The message is deliberately not one of the
    backend's generic authentication messages - those report a decision the
    request never reached.
  - Documentation corrected: `frontend/README.md` described automatic
    Google account linking, a design that was rejected; `docs/architecture.md`
    now reflects S1-005 through S1-009 and `docs/environments.md` documents
    `CACHE_URL`.
- S1-010: Password reset and account activation — the two emailed-link flows,
  and the frontend that consumes them. `EmailToken` (with `EmailTokenPurpose`),
  `identity/email.py` and its two plain-text templates, the
  `requestPasswordReset`/`resetPassword`/`activateAccount`/
  `resendActivationEmail` mutations, the five SMTP/frontend-origin settings,
  and the `/activate-account` route the confirmation link needs.
  - **Neither request side discloses whether an address is registered.**
    `requestPasswordReset` and `resendActivationEmail` take a caller-supplied
    address, so a different answer for a hit than for a miss would make each
    an account-existence oracle. Every outcome — no account, deactivated,
    already verified, or Google-only with no password to reset — returns one
    message, and only the hit side sends anything. The frontend displays that
    message verbatim rather than wording its own, for the same reason.
  - **Only a hash is stored.** The raw token exists in exactly one place,
    the email; `EmailToken.token_hash` is a SHA-256 digest, so a database
    leak yields nothing replayable. A fast hash is right here precisely
    because the value is 384 bits of `secrets` output rather than a
    human-chosen secret — the reasoning `RefreshSession` already used,
    applied again rather than assumed.
  - **One-time, expiring, superseded.** A redeemed token is recorded as used
    rather than deleted, so "already used" stays distinguishable from "never
    existed" in the record even though the caller cannot tell them apart.
    Requesting a new link supersedes any outstanding one for the same
    purpose, so a leaked older email stops being a live credential — and
    superseding cannot erase the record that an earlier link was redeemed.
  - **One message for every unusable token**, and one per flow: never real,
    expired, already used, or belonging to the other operation. A message per
    case would tell whoever holds a stolen link exactly how far it got.
  - **A reset ends every session on the account**, the caller's own browser
    included, and clears its refresh cookie in the same response. Without
    that step a password changed *because it may have been stolen* leaves the
    attacker's existing session signed in until it expires on its own
    schedule.
  - **Verification is not a login requirement.** `login` does not check
    `isVerified`, so an unconfirmed account owns a legitimate account and is
    not locked out of it. Making verification an authorization gate is a
    separate decision, to be taken in one place, once there is a reason to.
  - **Delivery failure is logged, never raised**, at `ERROR` with the user's
    primary key and never the token — raising would either leak account
    existence to fix a mail problem or force every caller to reimplement the
    catch. The cost is that a broken mail setup is found in the logs rather
    than in a support ticket, which is why production settings now *require*
    the five email variables and refuse the console backend outright: both
    flows report generic success whether or not anything was sent, so a
    deployment that cannot send is otherwise indistinguishable from one
    nobody has used yet.
  - **All four operations are throttled** on the S1-009 infrastructure, with
    the per-address limits keyed on the *submitted* address and never
    resolved to a user, so a miss costs the caller the same budget as a hit.
    The password-reset and activation-resend limits are the mail-cannon
    bound; the reset/activation execution limits are about how much work an
    anonymous request can cause, not about guessing an unguessable token.
  - **Frontend wiring**: `ForgotPasswordForm` and `ResetPasswordForm` are no
    longer placeholders, and a new `/activate-account` route exists for the
    confirmation link. The three outcomes are kept apart on purpose — a
    refused token, a rejected password, and an unreachable server are three
    different facts, and treating them as one either sends a user off to
    request a new link for a link that was fine, or tells them their link is
    dead when the request never arrived. Following an activation link is an
    explicit press rather than a request on mount, because mail clients and
    link scanners fetch URLs before a person sees them and the token is
    single-use.
  - Tests: the flows at the service, GraphQL, transport and real-HTTP layers,
    the four throttle policies, the settings guards, and `.env.example`
    completeness. Several were written to fail first — a throttle rendered as
    "check your email" on the forgot-password form, and a permanently
    disabled confirm button on the activation page, were both caught this
    way.

### Added — Sprint 0: Foundation

- S0-001: Repository foundation — `frontend/`, `backend/`, `docs/`,
  `infrastructure/`, `scripts/` directory boundaries; `.github/` workflow,
  issue, and PR templates; root `.gitignore`, `.env.example`, `README.md`,
  `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, and `LICENSE`.
- S0-002: Django backend foundation — bare Django project under
  `backend/config`, environment-driven settings split into
  `base`/`local`/`production` via `django-environ`, SQLite placeholder
  database, and a dependency-free `/health/` endpoint. No business domain
  apps, authentication, PostgreSQL, or GraphQL yet.
- S0-006: Environment configuration — `ENVIRONMENT` convention (local,
  development, staging, production); required `DJANGO_SECRET_KEY` with no
  default; environment-driven CORS (`django-cors-headers`, `/graphql/` only)
  and CSRF trusted origins; fail-fast validation in
  `config.settings.production`; per-app `.env.example` files
  (`backend/`, `frontend/`); `docs/environments.md`.
- S0-007: Testing foundation — backend `pytest` + `pytest-django` +
  `pytest-cov` (`backend/tests/`, `requirements-dev.txt`); frontend Vitest +
  React Testing Library + jsdom with V8 coverage (`test`, `test:run`,
  `test:coverage`); existing GraphQL and settings tests migrated to pytest;
  a blank `DATABASE_URL` now fails at startup.
- S0-008: Code quality foundation — backend Ruff (lint, import sorting,
  format) configured in `backend/ruff.toml`; frontend Oxlint config expanded
  (React hooks, a11y, import, vitest rules; warnings fail CI) plus `oxfmt`
  formatter and `typecheck`/`format`/`format:check`/`lint:fix` scripts;
  existing code made compliant. No pre-commit hooks: local checks (and CI,
  once workflows exist) are the quality gate.
- S0-009: GitHub Actions CI foundation — `.github/workflows/ci.yml` runs
  backend (Ruff, Django checks, migration check, pytest with coverage against
  a PostgreSQL service) and frontend (Oxlint, oxfmt, `tsc`, Vitest, production
  build) jobs on pull requests to and pushes to `develop`/`main`. Read-only
  permissions, no secrets, no deployment.
- S0-010: Documentation foundation — new `docs/development.md`,
  `docs/testing.md` and `docs/git-workflow.md`; `docs/architecture.md`
  current-implementation status and repository structure brought up to date
  (target architecture preserved); `docs/environments.md` documents the CI
  execution context; `README.md` and `CONTRIBUTING.md` corrected for stale
  Sprint 0 statements and linked to the new guides.
- S0-011: Security foundation — explicit shared browser protections in
  `base.py` (nosniff, referrer policy, COOP, `X-Frame-Options: DENY`,
  HttpOnly/SameSite cookies, CORS wildcard and credentials off);
  production HSTS (default one year in production, one hour in
  development/staging, environment-driven, `includeSubDomains`
  and `preload` opt-in and validated) and opt-in proxy HTTPS detection
  (`DJANGO_TRUST_X_FORWARDED_PROTO`); `config.settings.local` now refuses
  deployed `ENVIRONMENT` names so neither local nor CI can run as production;
  `tests/test_security.py` (settings invariants, response headers, HTTPS
  redirect, `check --deploy`); `SECURITY.md` rewritten to match. No
  authentication or authorization yet (Sprint 1).
