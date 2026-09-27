# Frontend

React + TypeScript + Vite web application.

**Status:** Sprint 0 (S0-003) delivered the infrastructure foundation.
Sprint 1 has added the Identity feature: the `/auth` sign in/up/forgot-
password UI (S1-002), real email/password authentication - login, an
in-memory access token, and session persistence via the backend's refresh
cookie (S1-003) - and Google/OAuth sign-in via Google Identity Services,
wired into the same `AuthContext`/session mechanics (S1-004). See
[`/docs/architecture.md`](../docs/architecture.md) for the target frontend
architecture.

## Stack

- React 19 + TypeScript
- Vite 8
- React Router (`react-router-dom`) — client-side routing
- Tailwind CSS 4 (via `@tailwindcss/vite`)
- `graphql-request` — minimal GraphQL client, no cache/store to configure

## Prerequisites

- Node.js 22+
- npm

## Installation

```bash
cd frontend
npm install
cp .env.example .env
```

## Development

```bash
npm run dev
```

## Production build

```bash
npm run build   # runs `tsc -b` then `vite build`, output in dist/
npm run preview # serve the production build locally
```

## Code quality

Oxlint (lint), the TypeScript compiler (types, including unused
locals/parameters) and oxfmt (formatting, the Oxc formatter that pairs with
Oxlint) are the only quality tools. Config: [`.oxlintrc.json`](.oxlintrc.json),
[`.oxfmtrc.json`](.oxfmtrc.json), `tsconfig.*.json`.

```bash
npm run lint           # Oxlint; warnings fail the run (CI-friendly)
npm run lint:fix       # apply safe auto-fixes
npm run typecheck      # tsc -b
npm run format:check   # formatting check for src/ and vite.config.ts
npm run format         # apply formatting
```

Oxlint enables the correctness rules plus React hooks, accessibility
(`jsx-a11y`), import, TypeScript and Vitest rules. `format` covers source code
only (`src/`, `vite.config.ts`), not JSON or Markdown.

## Testing

Vitest + React Testing Library + jsdom, configured in `vite.config.ts`.

```bash
npm run test           # watch mode (development)
npm run test:run       # single non-interactive run (CI, pre-PR)
npm run test:coverage  # single run with V8 coverage; report in coverage/
```

Tests sit next to the code they cover (`Foo.tsx` → `Foo.test.tsx`).
Shared helpers live in `src/test/`. Tests never call a real backend:
`VITE_GRAPHQL_URL` is fixed in the Vitest config and network calls are
stubbed, so results don't depend on `frontend/.env`.

## Environment configuration

Vite only exposes variables prefixed `VITE_` to the browser bundle — never
put secrets (API keys, credentials, backend secret keys) here.

| Variable            | Purpose                                      |
| ------------------- | --------------------------------------------- |
| `VITE_GRAPHQL_URL`  | Public URL of the Django GraphQL endpoint (required) |
| `VITE_GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client id for "Sign in with Google" (optional - the button is disabled, not broken, when unset) |

Values are compiled into the bundle at build time. See
[`/docs/environments.md`](../docs/environments.md) for the environment
strategy.

See `.env.example`. Access env vars through [`src/lib/env.ts`](src/lib/env.ts)
rather than reading `import.meta.env` directly in components.

## Architecture

```
src/
├── app/            # App root wiring: route tree (routes.tsx)
├── components/     # Shared reusable UI (ErrorBoundary, etc.)
├── features/
│   ├── identity/     # Sign in/up/reset/confirm UI, auth state (auth/)
│   ├── organizations/ # Organizations, members, roles; the active-organization context
│   └── ideas/        # File, edit and submit ideas (S2-002)
├── graphql/         # Centralized GraphQL client (client.ts), token store
├── layouts/        # Page shells (RootLayout)
├── lib/            # Cross-cutting utilities (env access)
├── routes/         # Route-level page components (HomePage, DashboardPage, ...)
├── types/          # Ambient TypeScript declarations
├── App.tsx         # Root component: ErrorBoundary + AuthProvider + RouterProvider
└── main.tsx        # Entry point
```

`hooks/` is intentionally not created yet — it'll be added when the first
shared hook (outside a feature) actually needs it.

### GraphQL client

[`src/graphql/client.ts`](src/graphql/client.ts) exports a single shared
`graphqlClient` (a `graphql-request` `GraphQLClient`) configured from
`VITE_GRAPHQL_URL`. Feature modules import this client rather than
constructing their own or hardcoding a URL. Two things make every request
authenticated automatically, without a second "authenticated client":
`credentials: 'include'` (so the backend's refresh-token cookie flows with
cross-origin requests) and a `headers` function that reads the current
access token from [`src/graphql/tokenStore.ts`](src/graphql/tokenStore.ts)
and attaches `Authorization: Bearer <token>` when one is set.

### Authentication (Sprint 1, S1-003/S1-004)

[`src/features/identity/auth/`](src/features/identity/auth) owns all
authentication state:

- `authApi.ts` — the `login`/`googleLogin`/`refreshToken`/`logout`/`register`
  GraphQL operations, plus the four emailed-link ones
  (`requestPasswordReset`, `resetPassword`, `activateAccount`,
  `resendActivationEmail`). None of those four establishes a session, so
  none returns an access token — asking for one would be a contract the
  backend does not have.
- `AuthContext.tsx` — `AuthProvider` (wraps the router in `App.tsx`) and the
  `useAuth()` hook, exposing `status` (`'loading' | 'authenticated' |
  'unauthenticated'`), `user`, `login()`, `loginWithGoogle()` and
  `logout()`. On mount, it silently calls `refreshToken` once to
  re-establish a session from the backend's cookie (see below) before
  deciding the status. `loginWithGoogle()` and `login()` both funnel into
  the same `applySession()` - a successful Google sign-in is
  indistinguishable, from `AuthContext`'s point of view, from a password
  login.
- `useGoogleSignIn.ts` — wires the existing, custom-styled
  `GoogleAuthButton` to Google Identity Services (GIS), loaded dynamically
  from `accounts.google.com` (never bundled). GIS only issues a credential
  from a real click on a button *it* rendered, so this renders GIS's own
  button into a visually hidden container and forwards a click on
  `GoogleAuthButton` to it - Google's own documented pattern for a
  custom-styled trigger. Returns `isConfigured` (false, and the button
  stays inertly disabled, when `VITE_GOOGLE_OAUTH_CLIENT_ID` is unset) and
  `trigger()`. Used from both `SignInForm` and `SignUpForm` - Google
  sign-in doesn't distinguish signing up from signing in, so both call the
  same `loginWithGoogle()`.

  **Deployment constraint — do not set COOP on the page that hosts this.**
  Google Identity Services delivers the credential from a
  `accounts.google.com` popup back to this page with `window.postMessage`.
  `Cross-Origin-Opener-Policy: same-origin` severs `window.opener`, so the
  credential never arrives and sign-in silently does nothing. If whatever
  serves this app in a deployed environment sets COOP, it must be
  `same-origin-allow-popups` (Google's documented requirement for this flow)
  or unset. The backend sets `SECURE_CROSS_ORIGIN_OPENER_POLICY` on *its own*
  responses, which is inert here - COOP is honoured per-document, and this
  app's document is the static one - so that setting is unrelated and is
  deliberately left strict.
- `tokenRefresh.ts` — keeps the access token alive: schedules a `refreshToken`
  shortly before `accessTokenExpiresAt` rather than waiting for a request to
  come back unauthenticated, and holds the *single* in-flight refresh promise
  every caller shares. That sharing is not an optimisation: the refresh
  cookie is single-use and `refreshToken` rotates it, so two overlapping
  refreshes would mean the second one presents an already-revoked credential
  and fails, signing the user out. A failed refresh is terminal for the
  session rather than an event to retry - the failure handler signs the user
  out, the schedule is cancelled, and a latch short-circuits any further
  call until a real sign-in lifts it, so there is no path from "refresh
  failed" back to "refresh again". `tokenRefreshState.ts` holds the shared
  module state, in a dependency-free module so a test setup file can reset it
  without evaluating `authApi` before a test's own `vi.mock` (see
  `src/test/setup.ts`).

**Token storage, deliberately:** the short-lived access token lives only in
`graphql/tokenStore.ts` - a plain in-memory module variable, never
`localStorage`/`sessionStorage`. It does not survive a page reload by
design; `AuthContext`'s mount-time `refreshToken` call is what re-establishes
a session afterwards, using the backend's `HttpOnly` refresh-session cookie
(which JavaScript can't read at all, by design - see `backend/README.md`'s
GraphQL API section and `docs/architecture.md` for the full cookie/CORS/CSRF
design).

The store holds the token's expiry alongside the token, in the same module
and cleared by the same call. A client can only refresh *proactively* — before
a request fails — if it knows when the current token stops working, and
keeping the two in one place makes it impossible to hold a token without also
holding its deadline, or to clear one and leave a stale deadline behind to
schedule a refresh for a session that no longer exists.

`SignUpForm` submits the real `register` mutation (`authApi.registerRequest`)
and maps the backend's field-level failure onto the matching field, so a
duplicate email is shown next to the email input exactly like a client-side
validation error. Registration does not authenticate — the backend's
`register` mutation creates the account only — so success shows a
confirmation that leads to sign-in rather than navigating into the app. That
confirmation also says a confirmation link has been emailed *and* that
confirming is not required: the backend's `login` does not check
`isVerified`, so an unconfirmed account can sign in and work normally, and
copy that implied otherwise would strand anyone who lost the first message.

`ForgotPasswordForm` and `ResetPasswordForm` are wired to the real
`requestPasswordReset` and `resetPassword` mutations. Three outcomes are kept
apart, because conflating any two of them misleads the user about something
that matters:

| Outcome | Shown as | Why |
| ------- | -------- | --- |
| success | confirmation, or the success view | a definite claim the backend backs |
| `field === 'token'` | invalid-link view | the link is no good and the form would be pointless |
| `field === 'password'` | message next to the input | the link is still live; only the password was rejected |
| no `field` (e.g. a throttle) | form-level error | no field is at fault, and nothing was sent |
| transport failure | form-level error, form kept | the request never reached a decision — claiming the link is dead would send the user off to request a new one for no reason |

A successful reset says that every session on the account was signed out,
including this browser's, because that is the part people are surprised by
and it is the backend's actual behaviour.

`src/features/identity/components/RequireAuth.tsx` gates the `/app` route:
it shows a neutral loading state while the initial `refreshToken` call is in
flight, redirects to `/auth` if it comes back unauthenticated, and renders
the protected route otherwise.

### Error handling

[`src/components/ErrorBoundary.tsx`](src/components/ErrorBoundary.tsx)
wraps the router in `App.tsx` so a render error in any route shows a
fallback message instead of an uncontrolled blank page.

### Routing

[`src/app/routes.tsx`](src/app/routes.tsx) defines the route tree via
`createBrowserRouter`:

- `/` (`HomePage`) and a catch-all (`NotFoundPage`).
- `/auth` (`AuthPage`) — sign in, sign up and forgot password, combined into
  one view that swaps in place (see the Identity feature's own docs in
  `src/features/identity/`).
- `/reset-password` (`ResetPasswordPage`) — where a password-reset link lands
  (`?token=…`). Redeems the token with the real `resetPassword` mutation.
- `/activate-account` (`ActivateAccountPage`) — where a confirmation link
  lands. Confirms the address with `activateAccount`, and offers
  `resendActivationEmail` for a lost first message. Following the link is an
  explicit button press rather than a request on mount, because mail clients
  and link scanners fetch URLs in a message before a person sees them and the
  token is single-use.
- `/app` (`RequireAuth` → `DashboardPage`) — the authenticated area,
  redirecting to `/auth` when there's no session. `DashboardPage` is a
  placeholder proving the login/session lifecycle end to end, not a real
  product surface.
- `/app/ideas` (`RequireAuth` → `IdeasPage`) — the Ideas area. An `/app`
  child rather than a top-level route, so it inherits the auth gate and the
  `OrganizationProvider`, and takes its organization from the same switcher as
  the rest of the authenticated app rather than from a picker of its own.

Future business domains each get their own route module under
`src/features/<domain>` and are wired into this tree.

## What this is not

What exists is Identity (sign in, sign up, Google sign-in, session lifecycle,
password reset and email confirmation), the organizations tier (create an
organization, switch between them, see members and roles), and the first
Ideas slice (file an idea, edit your own draft, submit it for review, move it
through review, browse ideas by category, search and status over a paged list,
read and write the discussion on any idea, and vote for the ideas worth
doing).
Reviews, projects, notifications, attachments and everything else in that
direction do not exist yet. Within Identity, what is still missing is an
authenticated Google-account-linking flow: Google sign-in authenticates or
provisions, and never links to an existing account by email.

Two things the reset and confirmation flows deliberately do **not** do on this
side, because the backend is the authority for both:

- They do not decide whether an email address has an account. Both
  `requestPasswordReset` and `resendActivationEmail` answer identically for a
  registered address, an unregistered one, and a deactivated account, so
  these pages show the backend's own message verbatim rather than wording
  their own — a locally-worded "we found your account" would quietly turn the
  page into an existence oracle.
- They do not explain *why* a link failed. The backend gives one message for
  every unusable token (never real, expired, already used, or belonging to
  the other flow), and the UI repeats that discipline instead of helping.

Two things the Ideas area deliberately does **not** do on this side:

- It does not filter anything *for itself*. S2-004 added a category, a status
  and a search, and every one of them is sent to the backend and applied
  there: the client filters nothing it is given, and the `IdeaFilters` type has
  no `visibility` and no `authorId` field to send, because a filter that can
  widen a result is a client-side control over what somebody may read. The
  frontend still arranges what it shows — the status badge, the pager — and
  never decides what it is shown.
- It does not decide who may edit, submit, review or approve. The buttons on
  an idea are rendered from `idea.availableTransitions`, which the backend
  computed *for this viewer* from the same transition matrix that enforces the
  change — so there is no client-side rule saying "the author may submit" or
  "a reviewer may approve" that could disagree with the server's. Editing a
  draft is the one exception, and it is `idea.authorId` compared with the
  signed-in user: still only a question of what to *offer*. The server refuses
  whatever it should refuse whether or not a button was drawn, so hiding them
  is a courtesy to the reader, not a control.
- It does not carry a copy of the lifecycle. Status names and the button for
  each transition come from one vocabulary table in
  `features/ideas/utils/lifecycle.ts`, and a state the backend reports that
  table has never heard of renders as its raw name rather than being hidden.
  The status *filter* offers the same seven names for the same reason: the
  union is the contract, and a client that only listed the two states Sprint 2
  could produce would need editing the moment review did.
- It does not do its own paging. The page controls are driven entirely by the
  backend's `pageInfo` — `hasNextPage`, `hasPreviousPage`, `totalCount` — and
  move by the `limit` the server applied rather than by a page index
  invented here, so a clamped page size cannot produce a gap. Nothing on this
  side knows what the maximum page size is, because nothing needs to.
- It does not search on every keystroke. The box is debounced at 300ms, and a
  term that is typed and then replaced is never sent at all — rather than
  being sent and then overwritten, which is what would leave the list showing
  results for a word that is no longer in the box.
- It does not fetch a discussion until it is opened. Each idea's card has a
  discussion toggle, and the thread is requested on first open: the ideas page
  is a *list*, and mounting twenty discussions would be twenty requests for
  content nobody asked to read. At most one is open at a time, and the open one
  cannot outlive the results it belongs to.
- It does not decide who may edit or delete a comment. The buttons are drawn
  where `comment.authorId` matches the signed-in user, which is a question of
  what to *offer*; the server checks authorship itself and would refuse
  regardless, and there is no client-side elevation and no argument that could
  express one.
- It does not copy the "is this discussion open?" rule. `idea.discussionOpen`
  is reported by the server from the same rule `createComment` enforces, for
  the same reason `availableTransitions` exists.
- It does not count votes itself. `voteCount` and `viewerHasVoted` arrive on
  each idea from the server, so a page of twenty cards costs no extra requests,
  and `useIdeaVotes` keeps control state keyed by idea id — a response that
  arrives late writes the idea it was for, never whichever one is on screen.
  There is no optimistic arithmetic: the number rendered after a vote is the
  one the mutation returned, which already accounts for other people's
  concurrent votes, so a failed vote leaves the count exactly where it was and
  there is nothing to roll back.
- It does not re-fetch the ideas list after a comment or a vote. A discussion is
  append-only in reading order, so a post, an edit or a delete updates the
  thread in place — the reader keeps their filters and their page, and a second
  submission in the same tick is dropped rather than duplicated.

On organizations specifically: what exists is the data model, the
authorization rules and a deliberately small UI - there is no
role-management screen, no permission editor, and no member invitations. The
create/switch/see surface is enough to exercise the tenant-isolation
guarantees end to end, and the backend enforces those regardless of what the
UI does. `GoogleAuthButton` is no longer a placeholder (S1-004) - see
Authentication above - but there is no authenticated "link this Google
account to my existing session" flow, and the automatic linking that an
earlier version of this document described is **not** what the backend does.

**Google account-linking policy (Policy B — refuse, never link).** The only
thing that authenticates into an *existing* account through Google is an
exact `(provider='google', provider_subject=<sub>)` match on the stable
Google subject. A first-time Google identity whose email matches an account
that already exists — a password-registered one, or one created by a
different Google identity — is **refused**, with the same generic message as
an invalid credential, and no link is created. This is true *regardless of
`email_verified`*: Google's `email_verified: true` proves Google confirmed
mailbox control at some point in the past, not that today's sign-in is the
person who registered here — and once an address can be reassigned outside
this platform's control, auto-linking would hand the *old* account to a new
mailbox holder with no consent, no notification and no audit trail.

The user-visible consequence, and the reason it is called out here rather
than left to the backend: someone who signed up with a password and then
tries "Continue with Google" using the same address is **refused** — never
signed in, never linked. Google sign-in will not adopt the account for them.

The message they get is specific rather than generic: *"An account already
exists for this email."* followed by the two real next steps. Everyone else —
invalid, expired, replayed, or unverified credential, Google sign-in not
configured — still gets the single generic *"Could not sign in with Google."*
The form renders whatever message comes back, so no special-casing is needed
on this side.

Why the specific message is safe to send: reaching it means Google verified
the credential *and* asserted that this person controls that mailbox. They
could therefore have established the fact themselves. It is not a way to find
out whether somebody else's address is registered, because nobody can present
a Google credential for an address they do not control. The intended way to
add Google sign-in to a password account is an authenticated "link this
Google account to my current session" flow, which is not implemented yet. The
reasoning is recorded in full in
`backend/identity/authentication.py`'s `authenticate_with_google` docstring
and `GoogleEmailInUseError`, and in `docs/architecture.md`.
