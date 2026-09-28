# Architecture

This document describes the **target architecture** for Automation
Platform, and calls out clearly what is **currently implemented** versus
what is planned for future sprints. See
[Current Implementation Status](#current-implementation-status) for what
exists today and [`CHANGELOG.md`](../CHANGELOG.md) for what has landed.

Sections headed "target" describe the intended design. Unless a section says
otherwise, they are not implemented.

## Product Flow

The platform turns real-world problems into delivered automation:

```
PROBLEM → IDEA → VALIDATION → AUTOMATION OPPORTUNITY → REQUIREMENTS
→ PROPOSAL → DEVELOPER/TEAM → PROJECT → DEVELOPMENT → TESTING
→ DEPLOYMENT → IMPACT
```

## Target High-Level Architecture

### Frontend (target — foundation implemented)

React + TypeScript + Vite, using React Router, Tailwind CSS, a GraphQL
client, and React Query for non-GraphQL concerns. Organized by feature so
that a future React Native client can reuse backend contracts without a
backend rewrite.

Implemented: the application shell, routing, Tailwind, a shared GraphQL
client, and an error boundary. Not implemented: React Query, feature
modules, and any business UI. See
[Current Implementation Status](#current-implementation-status).

### Backend (target — foundation implemented)

Django, structured as a **modular monolith**: one Django project containing
separate apps per business domain, rather than one large application.
Expected future domains:

- identity
- organizations
- ideas
- reviews
- opportunities
- proposals
- developers
- projects
- tasks
- notifications
- impact
- files
- audit

Implemented: the Django project, split settings, the `graphql_api`
infrastructure app (not a business domain), and four business domain apps —
`identity`, `organizations`, `ideas` and `reviews` (models, reviewer
eligibility, the review queue and history, starting and deciding reviews,
resubmission after changes are requested, and the author's decision email,
S3-002 to S3-006, and the lifecycle audit trail `ideas.IdeaTransition`,
S3-007). The rest are introduced
incrementally in later sprints.

### API (target — foundation implemented)

GraphQL is the primary application API. REST is reserved for
infrastructure/specialized operations: file uploads/downloads, webhooks,
health checks, and OAuth callbacks.

Implemented: the `/graphql/` endpoint with a foundation schema and the
`/health/` check. No file, webhook or OAuth endpoints exist.

### Database (target — implemented)

PostgreSQL, configured entirely through environment variables — no
credentials committed to source control. Implemented via `DATABASE_URL`.
`identity`, `organizations`, `ideas` and `reviews` own the business models
and migrations; every other domain is still only Django's built-in tables.

### Asynchronous Processing (target — not yet implemented)

Redis + Celery for background jobs (email, notifications, AI processing,
analytics, scheduled tasks). Not present yet.

Redis (or Memcached, or a database) *is* nevertheless required today for
cache: the Google ID-token replay check and the authentication rate limits
both record state that every process has to agree on, so a per-process cache
would make both silently useless the moment a second worker exists. See
[Caching](#caching-target--implemented) under Security.

### AI Architecture (target — not yet implemented)

AI functionality will live behind a dedicated gateway/service abstraction
rather than being scattered across business domain apps, and its output
will be treated as a recommendation subject to human validation. No AI
functionality exists yet.

### Storage (target — filesystem today, object storage later)

Object storage (e.g. S3-compatible) for files, rather than storing large
binary content in PostgreSQL.

S2-001 implemented the *boundary*: `ideas.Attachment` stores only metadata
(`storage_key`, `filename`, `content_type`, `size`) and holds no file
column of any kind, so attachments cannot end up in PostgreSQL by accident.
S2-007 implemented the operations on top of it - upload, download, listing,
deletion - routed through Django's pluggable `Storage` API
(`ideas/storage.py`, `config/settings/base.py`'s `STORAGES['attachments']`)
rather than a filesystem path or a client library hardcoded into
`ideas/services.py`. What is actually configured today is Django's own
filesystem backend; no object-storage bucket, no `django-storages`
dependency and no presigned/signed-URL mechanism exist in this codebase —
switching to one is a settings change (`ATTACHMENTS_STORAGE_BACKEND`) once a
bucket and its driver package are actually provisioned, not a rewrite of the
domain. See [`ideas-domain.md`](ideas-domain.md#storage-boundary) for the
full design and the reasoning for going through this server rather than
straight to a bucket at the current scale.

### Security (target — partly implemented)

Environment-driven secrets, DEBUG/production settings separation,
ALLOWED_HOSTS, CORS/CSRF strategy, security headers, authentication
(Sprint 1, Identity), organization-scoped authorization and tenant
isolation (Sprint 1), authentication rate limiting, and a shared cache for
cross-process security state. Audit logging is still a placeholder.

Implemented (S0-011): environment-driven secrets, the local/production
settings split with fail-fast validation, `ALLOWED_HOSTS`, CORS/CSRF
configuration, secure cookies, HTTPS redirect, HSTS and browser security
headers, with automated tests.

Implemented (S1-002/S1-003): the `identity` app's `User` model (email as the
login identifier, Django's own password hashing), email/password
registration and login, short-lived JWT access tokens, and a persistent,
rotating `RefreshSession` credential delivered as an HttpOnly cookie (never
`localStorage`). `CORS_ALLOW_CREDENTIALS` is on for this reason, still
scoped to an explicit, non-wildcard origin allow-list. See
[`environments.md`](environments.md) for the cookie/CORS/CSRF reasoning and
`backend/identity/` (`models.py`, `services.py`, `authentication.py`,
`tokens.py`, `schema.py`) for the implementation.

Implemented (S1-004): Google/OAuth sign-in via Google Identity Services'
ID-token (OIDC) flow, not the classic authorization-code exchange - the
backend never needs a client secret, since a Google-signed credential is
verified directly against Google's public keys
(`backend/identity/google_oauth.py`) rather than exchanged for one. The
existing `ExternalIdentity(provider='google', provider_subject=<sub>)`
table backs it, unmodified; a successful Google sign-in produces the exact
same JWT access token + `RefreshSession` as email/password login
(`identity/authentication.py`'s `authenticate_with_google`).

Account-linking policy (revised after a dedicated security review; this is
the load-bearing decision in S1-004, not a footnote): the **only** thing
that ever authenticates into an *existing* account is an exact
`(provider='google', provider_subject=<sub>)` match. A first-time Google
identity with no matching `ExternalIdentity` and no existing `User` for its
email provisions a brand-new account (`phone_number` is left blank -
Google never provides one, see below - and `is_verified` is set from
Google's own `email_verified` claim). A first-time Google identity whose
email matches *any* existing account - a password-registered one, or one
created earlier by a *different* Google identity - is refused.
Google/OAuth sign-in never auto-links by email, **regardless of
`email_verified`**.

The *refusal* is unconditional; the *message* is not, and this is a
deliberate, load-bearing exception to the project's generic-error rule rather
than a loosening of it. A caller whose credential Google verified as
`email_verified: true` is told the address already has an account, and given
the two real next steps (sign in with the existing account, or sign up with a
different address). Everybody else - invalid, expired, replayed, unverified
or unconfigured - still gets the single generic "Could not sign in with
Google.", byte for byte.

That gate is what makes the disclosure safe rather than an account-existence
oracle. Reaching it means the request presented a Google-issued ID token that
passed signature, issuer, audience and expiry checks, and that Google itself
asserts control of that mailbox - so the caller could have established the
fact by checking their own inbox. The attack this must not enable is asking
whether *someone else's* address is registered, and it cannot be: nobody can
present a Google credential for an address they do not control, so the only
addresses anyone can get an answer about are their own. A single message
covers the whole collision class, so a caller also cannot learn whether the
existing account is password-registered or belongs to a different Google
identity - that difference is the existing account's history, not theirs.
Enforced in `identity/authentication.py`'s `_email_collision_error`, and
reasoned through in `GoogleEmailInUseError`.

An earlier version of this design *did* auto-link when `email_verified`
was true, reasoning it was equivalent to a password-reset-by-email flow's
trust level. A dedicated review rejected that: `email_verified=true` only
proves Google confirmed mailbox control *at some point in the past* - not
that today's Google sign-in is the same person who registered the platform
account, particularly once an email address can be reassigned outside this
platform's control (e.g. a company reissuing a departed employee's address
to someone new, who gets a fresh Google identity with `email_verified=true`
for it). Auto-linking would have silently and permanently handed that new
mailbox holder the *old* account - with no consent, no notification to the
original owner, and no audit trail - and would have done so as this
platform's first email-provenance-based path into an existing account,
since no password-reset-by-email flow exists yet to compare the risk
against. (The earlier reasoning also cited Firebase Authentication's
default behavior as precedent; that citation was wrong - Firebase's actual
default for a colliding email is to reject the sign-in and require the
existing method first, which is this same refuse-first policy.) An
authenticated "link this Google account to my current session" flow,
initiated by an already-logged-in user, is the intended way to add Google
sign-in to an existing password account, and remains out of scope for
S1-004. See `identity/authentication.py`'s `authenticate_with_google` and
`_resolve_google_user` docstrings for the full reasoning and for how a
provisioning race is prevented from re-opening this via a timing window
(a collision is never resolved by trusting "whichever account has this
email now" - it re-checks from scratch).

Replay protection: Google ID tokens are also rejected on a second use,
even if the token itself is still within its lifetime and would otherwise
still verify (`identity/google_oauth.py`'s `_reject_if_already_used`, keyed
by the token's SHA-256 hash via Django's cache framework, TTL matching the
token's own remaining lifetime). This is deliberately a one-time-use check
rather than a transmitted OIDC `nonce`: GIS's button flow delivers the
credential directly via a JS callback rather than a browser redirect, so
the leak vector a nonce exists to close (a captured authorization redirect
replayed into a different browser session) mostly doesn't apply, and there
is no pre-existing client-side session to have stored an expected nonce
value in. See that module's docstring for the full reasoning.

The record is kept on a dedicated `replay_protection` cache alias rather
than the general `default` cache, so it can never be wiped by routine cache
housekeeping (S1-009). Which backend that alias resolves to is a deployment
requirement, not a local preference: a per-process backend makes the check
complete for a single-process deployment only, because a second worker would
never see what the first one recorded. `CACHE_URL` therefore selects a shared
backend and is **required** in every deployed environment;
`config/settings/production.py` refuses to start if the security-critical
aliases resolve to a local in-memory cache. Local development may use the
in-process backend, where there is only one process to agree with.

A new field-level decision: `User.phone_number` is `blank=True` at the
model level (`identity/migrations/0003_allow_blank_phone_number.py`) so a
Google-provisioned account can be created without one; registration's own
validation (`identity/services.py`) still requires a real phone number for
that path, unaffected. Collecting a phone number for a Google-provisioned
account is deferred to a future "complete your profile" step, not built in
S1-004.

Implemented (S1-005): the `me` query as the one authoritative answer to "who
is the currently authenticated user". It takes no arguments at all - the
access token is the only input - so no client-supplied value can influence
which user is returned, and an absent, invalid, expired, wrong-type or
deactivated-account token all resolve to `null` identically rather than
distinguishing why. On the client, `RequireAuth` gates `/app` on it and
`AuthProvider` uses it as the source of truth for bootstrapping a session.

Implemented (S1-006/S1-007/S1-008): the `organizations` app and
organization-scoped authorization. `Organization`, `Membership`,
`MembershipRole`, `Role` and `Permission` are separate models, and a role
is always scoped to one organization - a role id means nothing without the
organization it belongs to. Every organization-scoped resolver resolves the
caller's **active membership** of the organization being acted on, and
derives permission from the roles attached to that membership
(`organizations/authorization.py`); nothing reads a role, organization or
user from the request body or from a client-supplied claim. Reads
(`organization`, `organizationMembers`, `organizationRoles`) resolve to
`null` or `[]` for a tenant the caller is not an active member of, and
writes (`assignRoleToMembership`, `removeRoleFromMembership`) are refused
without confirming the target exists, so a cross-tenant request cannot be
used to probe which ids are real. The GraphQL surface takes no
organization id for the caller's own listings (`meOrganizations`,
`meMemberships`, `myOrganizationRoles`), so there is no argument to tamper
with there at all.

**Organization bootstrap, and why it is the one permission-gated exception.**
`createOrganization` is gated on being an active authenticated user and
*nothing else*. Requiring a membership permission would be circular: the
membership that carries the grant does not exist until the organization
does, and the grant itself is created by the very call being checked. The
bootstrap is therefore
`authenticated user → organization → creator membership → Owner role →
Owner permissions`, and the `organization.create` permission is still
provisioned and still granted to Owner (so the role's permission set
describes the organization surface completely) but is never used as a gate.
Nothing else is a bootstrap: every *subsequent* operation on the new
organization is permission-checked and tenant-isolated like any other. See
`organizations/services.create_organization_for_user`'s docstring for the
full argument and `organizations/authorization.py`'s module docstring for
what that module deliberately does not do.

Implemented (S1-009): authentication rate limiting (`identity/throttling.py`)
on the four unauthenticated entry points, with the scope chosen per
operation because the threats differ: **login** is limited per account
(credential guessing) *and* per client address (password spraying);
**googleLogin** per client address (there is no account to key on before
verification, and a replayed credential is already refused by the replay
check); **refreshToken** per refresh credential (a stolen cookie being
grounded against the server) *and* per client address (a loop, which
rotation alone would not catch since every successful refresh hands the
caller a fresh budget); **register** per client address. Counters are
fixed-window, on the shared `auth_throttle` cache alias rather than in
process memory, and every attempt is counted *before* the work it would
trigger, so a throttled caller never gets a password hashed or a Google
signature verified on their behalf. A successful sign-in clears that
account's own counter (so a run of typos does not compound), and a throttle
is otherwise lifted only by the window expiring. Rate limiting is not
signalled by a different authentication error: the generic invalid-credential
and Google messages are unchanged, and the throttle answer carries no
account, address or operation detail - it depends only on how many attempts
the caller has made, never on whether the submitted address has an account,
so it cannot be used as an existence oracle.

### Caching (target — implemented)

Django's cache framework with three deliberately separated aliases
(`config/settings/base.py`): `default` for general-purpose caching, and
`replay_protection` and `auth_throttle` for the two pieces of state that
decide a security outcome. The latter two are isolated so a routine
`cache.clear()` on general caching can never erase them, and
`config/settings/production.py` requires a backend every process shares
(`CACHE_URL`) and refuses to start otherwise. What makes this necessary
rather than merely tidy is that a per-process cache is a per-process *copy*:
with one worker it is correct, and with two it is worse than useless for
this purpose, because the second worker admits what the first one blocked.
Both consumers fail **closed** if the cache is unreachable - the alternative
is a silent, invisible removal of a security control.

Implemented (S1-010): the two emailed-link flows - password reset and
account activation. Both mint a single-use, expiring `EmailToken`, mail a
link into the *frontend* built from `FRONTEND_URL`, and redeem it once.
The properties that matter are these, and each is asserted by a test rather
than left to review:

- **Neither request side discloses whether an address is registered.**
  `requestPasswordReset` and `resendActivationEmail` take a caller-supplied
  address, so a different answer for a hit than for a miss would make each one
  an account-existence oracle. Every outcome - no account, deactivated,
  already verified, Google-only with no password to reset - returns the same
  message, and only the *hit* side sends a message at all.
- **Only a hash is stored.** The raw token exists in exactly one place, the
  email; `EmailToken.token_hash` is a SHA-256 digest, so a database leak
  yields nothing replayable. A fast hash is the right tool here precisely
  because the value is 384 bits of `secrets` output rather than a
  human-chosen secret.
- **One-time, expiring, and superseded.** A redeemed token is recorded as
  used rather than deleted, so "already used" stays distinguishable from
  "never existed" in the record even though the caller cannot tell them
  apart. Requesting a new link supersedes any outstanding one for the same
  purpose, so a leaked older email stops being a live credential.
- **One message for every unusable token.** Never real, expired, already used,
  or belonging to the other flow. A message per case would tell whoever holds
  a stolen link exactly how far it got.
- **A reset ends every session on the account**, the caller's own browser
  included, and clears its refresh cookie. Without that step a password
  changed *because it may have been stolen* leaves the attacker's existing
  session signed in and working until it expires on its own schedule.
- **Verification is not a login requirement.** `login` does not check
  `isVerified`, so an unconfirmed account owns a legitimate account and is
  not locked out of it. Turning verification into an authorization gate is a
  separate decision, to be made in one place, once there is a reason to.
- **Delivery failure is logged, never raised** (`identity/email.py`), with the
  user's primary key and never the token. Raising would either leak account
  existence to fix a mail problem or force every caller to reimplement the
  catch - and a broken mail setup is found in the logs rather than in a
  support ticket, which is why `config/settings/production.py` refuses to
  start on the settings that cause the commonest version of it (the console
  backend). See [Email](#email-target--implemented) below.
- All four operations are throttled on the S1-009 infrastructure, and the
  per-address limits are keyed on the *submitted* address and never resolved
  to a user, so a miss costs the caller the same budget as a hit.

Not yet implemented: an authenticated "link this Google account to my
existing session" flow (today's Google sign-in only ever authenticates or
provisions - it never links to an existing account), member invitations, a
role-management UI, logout-everywhere, and audit logging. See
[`SECURITY.md`](../SECURITY.md).

### Email (target — implemented)

Outgoing transactional mail for the two emailed-link flows, and nothing
else. The platform sends no other mail: no notifications, no digests, no
marketing, and no queue. Everything below is `identity/email.py` (transport)
plus `identity.services` (decisions) plus the settings that point them at an
SMTP server.

**Transport.** Plain SMTP through `django.core.mail`, so moving to a hosted
provider later is an `EMAIL_BACKEND` change rather than a rewrite. Plain
*text* messages on purpose: these carry a credential whose entire purpose is
"click this link", and HTML would add remote images, a tracking pixel, and a
link whose text can differ from its target - the things that make a phishing
mail convincing. Not a Google API client: the only Google credential this
project holds is a public OAuth *client id* for verifying sign-in ID tokens,
which authorizes nothing and cannot send. Gmail's SMTP relay needs no
service account and no key file - a mailbox address and an app password.

**Five variables, all required in a deployed environment.** `EMAIL_BACKEND`
(must actually send - the console backend is refused), `EMAIL_HOST_USER` and
`EMAIL_HOST_PASSWORD` (secrets), `DEFAULT_FROM_EMAIL` (rejected at the local
default: SPF/DKIM are checked against that domain), and `FRONTEND_URL` (must
be `https://`, because the link carries the token in its query string).
`EMAIL_HOST`, `EMAIL_PORT` and `EMAIL_USE_TLS` have defaults and no guard.
See [environments.md](environments.md) and `backend/.env.example`.

**Why "required" rather than "recommended".** Both flows answer generically
whatever happens, by design - that is what stops them being account-existence
oracles. The same property means a deployment whose mail is silently broken
is indistinguishable, at the API boundary, from one nobody has used yet: the
reset request "succeeds" and the user waits for a message that was never
sent. A send that fails is therefore logged at `ERROR` with the user's
primary key and never the token, and the settings that cause the commonest
version of the failure are refused at startup. The cost of that choice is
that a mail misconfiguration is found in the logs rather than in a support
ticket, which is the right way round.

Not implemented, and deliberately so: any queue. Sending is synchronous on
the request path, which leaves a residual timing signal on
`requestPasswordReset` - a request for a real account takes measurably
longer (an SMTP round trip) than one for an address that does not exist. It
is a weaker oracle than the response body, and what bounds it in practice is
the throttle: five probes per address per hour, twenty per client per hour, so
the same limit that stops this endpoint being used as a mail cannon also caps
the number of samples an enumeration attack can collect. Closing the gap
properly means moving the send onto a task queue, which is later work
(Redis + Celery, below).

### Multi-Tenancy (target — organization tier implemented)

Organization → Membership → User, with resources (Ideas, Projects, etc.)
optionally scoped to an organization for tenant isolation. The
Organization → Membership → User tier and its authorization model are
implemented (S1-006/S1-007/S1-008) - see
[Security](#security-target--partly-implemented) for the enforcement model
and the one bootstrap exception. `ideas.Idea` is the first resource hung off
an organization, and requires one on every row (S2-001), so there is no idea
that exists outside a tenant. A Department tier is still absent:
`Idea.visibility` reserves a `department` value, but nothing sets or filters
on it until that tier exists - see
[`ideas-domain.md`](ideas-domain.md#visibility).

### Ideas (target — partly implemented)

`ideas` is the first business domain beyond Identity & Access. S2-002
implemented its first vertical slice end to end — file an idea, edit it as a
draft, submit it — S2-003 added the lifecycle and the server-side read policy,
S2-004 added categories and discovery, S2-005 added comments and discussion,
S2-006 added voting, and S2-007 added attachments and their upload/download
endpoints.

**Authorization is Sprint 1's, unchanged.** `ideas` adds no permission code and
no `permissions.py`. Filing an idea requires an *active membership* in the
target organization, resolved by `organizations.authorization.get_membership`,
and editing or submitting requires *authorship* on top of that. Both halves
are needed and for different reasons: membership is whether a person may act
in that tenant at all, and it is re-checked on every write so that leaving an
organization takes the ability to write into it with you; authorship is who
owns the content, and a colleague in the same organization may not rewrite it.
An earlier idea that `idea.create` should be a permission code was rejected -
the question is already answered, and a second answer would be free to drift
from the first.

**The client is never trusted with ownership or tenancy.** `organizationId` is
an input to a *decision* - the server authorizes that organization and then
uses it - and the author is always the authenticated user, read from the
access token. Neither appears in any input type, so "file this as somebody
else" and "move this to another tenant" are not operations the API has the
vocabulary for. Refusals never distinguish *no such idea* from *not yours*,
so none of these operations can be used to discover which idea ids are real.

**Reads live in `ideas/selectors.py`, and the filter is not optional.** Every
read goes through a selector that has already applied tenancy and visibility,
so the only way to obtain an idea is through a function that decided whether
the caller may see it. `IdeaType` carries `authorId` and `organizationId` as
ids and deliberately does not embed a user object: a `PUBLIC` idea is readable
platform-wide, so a nested user would publish a member's email address with
it.

**`DEPARTMENT` fails closed.** It is reserved vocabulary with no Department
model behind it, so the service refuses it as a choice and the selectors
treat it as author-only. Treating it as `ORGANIZATION` would mean an idea an
author deliberately scoped to their department was readable by their whole
team - the exact outcome the reserved value exists to prevent. When the
department tier arrives, the selectors are the one place that changes.

**Submission is a rule of the transition, not of the schema.** A draft is
incomplete by definition, so completeness (a title, a description of at least
20 characters, a category) is checked by `submit_idea` rather than by a
`null=False` column or a database constraint that would make a half-written
idea unsavable. A successful submission stamps `submitted_at` in the same
transaction as the status change, because the model treats the two as
inconsistent apart.

**The lifecycle is a table, and it is the only way a status changes.**
`ideas.lifecycle.TRANSITIONS` maps `(from, to)` to the actor that may make
that move — seven pairs, `DRAFT → SUBMITTED` and `CHANGES_REQUESTED →
SUBMITTED` for the author, the five review moves for a reviewer. `transitionIdea`
changes a status for every move except the four a review makes (start,
request changes, approve, reject): since S3-004 those are refused there and
made only by `startReview` / `completeReview`, which write the `Review` in the
same transaction through `ideas.lifecycle.apply_review_transition`. No write
input has a `status` field, and `updateIdea` refuses anything but `DRAFT` and `CHANGES_REQUESTED` (S3-005), so a client cannot mark
its own idea reviewed or approved by any request shape. `IdeaType.availableTransitions`
reports the viewer's own moves *from the same table that enforces them*, so
the UI cannot offer something the server would refuse — or drift from it.

**A reviewer is an active member holding `idea.review` in the idea's
organization, and never the author.** Until S3-002 the gate was "holds a
system role" (Owner only). The `idea.review` permission is held by the Owner
role and by a non-system Reviewer role provisioned per organization, so
reviewing can be granted through `assignRoleToMembership` without granting
ownership; see [`reviews-domain.md`](reviews-domain.md). The self-review
exclusion is structural: an author who also holds the `Owner` role genuinely
holds the permission, so the refusal comes from the authorship check rather
than from the role.

**Transitions are serialized per idea.** The row is read with
`SELECT … FOR UPDATE` inside the transaction, so "approve" and "reject" fired
together cannot both apply — the second waits and then finds a status that no
longer permits its move.

**Submitting ends editability, not the author's access.** A submitted idea is
refused by `updateIdea` - by its own author, in its own organization - until
the review workflow introduces the `CHANGES_REQUESTED` route back to a draft.
No part of the review *management* is implemented: there is no review queue, no
reviewer assignment, no reason text, and nothing acts on an idea once it
reaches `AUTOMATION_PROPOSAL`.

See [`ideas-domain.md`](ideas-domain.md) for the entity design and the
reasoning behind each decision above.

### Environments (target — convention implemented)

LOCAL, DEVELOPMENT, STAGING, PRODUCTION, each configured via environment
variables rather than source-code branching. The configuration convention is
implemented; only a local environment exists, with no deployed
infrastructure. See [`environments.md`](environments.md).

## Current Implementation Status

Sprint 0 established the engineering foundation. Sprint 1 (Identity) is
substantially implemented: user registration (S1-002), email/password login
with rotating refresh sessions (S1-003), Google/OAuth sign-in (S1-004), the
current-user query and protected application route (S1-005), organizations
and membership (S1-006), roles and permissions (S1-007), authorization and
tenant isolation (S1-008), and the integration/security hardening pass
(S1-009), and the emailed-link flows - password reset and account activation
(S1-010). What remains inside Identity is an authenticated
Google-account-linking flow.

Sprint 2 starts the core business domain with the `ideas` app. Its
architecture and schema (S2-001), creation and submission (S2-002), lifecycle
and visibility (S2-003), categories and discovery (S2-004), comments and
discussion (S2-005), voting (S2-006) and attachments (S2-007) exist. The
review *queue* is Sprint 3 and is not started. Every other business domain is
not started.

### Implemented (Sprint 2)

| Area | What exists | Task |
| ---- | ----------- | ---- |
| Ideas domain | `ideas` app: `Category`, `Idea`, `Comment`, `Vote`, `Attachment`, and their migration/admin | S2-001 |
| Idea lifecycle | The `DRAFT`/`SUBMITTED`/`UNDER_REVIEW`/`CHANGES_REQUESTED`/`REJECTED`/`APPROVED`/`AUTOMATION_PROPOSAL` vocabulary, enforced at the database as well as by `choices` | S2-001 |
| Idea creation and submission | `createIdea`/`updateIdea`/`submitIdea`, `idea`/`ideas`/`organizationIdeas`/`categories` queries, tenant- and visibility-filtered selectors, and the `/app/ideas` UI | S2-002 |
| Idea lifecycle | The seven-pair transition matrix in `ideas/lifecycle.py` behind a single `transitionIdea` mutation, with the per-viewer `availableTransitions` field. No mutation and no write input can set a status | S2-003 |
| Categories and discovery | `list_discoverable_ideas` as the single read path, with `IdeaFilters` (category, status, search) that can only **narrow** what the visibility filter allowed, and `ideas/pagination.py` for bounded offset paging. `ideas`/`organizationIdeas` return an `IdeaPage`; there is no `visibility` or `authorId` filter to send | S2-004 |
| Comments and discussion | `add_comment`/`update_comment`/`delete_comment` behind a `comments(ideaId)` query that filters by the idea's own visibility, so a comment is never more readable than the idea it is on. Edit and delete are author-only, with no elevated path and no new permission code | S2-005 |
| Voting & engagement | One vote per user per idea, enforced by a service check *and* the `unique_vote_per_user_idea` constraint. Voting is gated on idea visibility only, with no lifecycle condition - a vote is interest in the idea, not participation in a review. `voteCount`/`viewerHasVoted` are annotated onto the discovery page, so a page of ideas costs no extra queries | S2-006 |
| Attachments & supporting evidence | `upload_attachment`/`delete_attachment`, gated on idea **authorship** (not merely readability) with no lifecycle condition. The storage key, recorded content type and display filename are all server-derived, never client-supplied; file type is checked by an extension allow-list *and* the file's own leading bytes. Binary transfer is HTTP (`ideas/views.py`), never GraphQL; metadata, listing and deletion are GraphQL (`attachments`/`attachment`/`deleteAttachment`) | S2-007 |
| Idea authorization | An active membership in the idea's organization to file or act on an idea, **plus** authorship to edit or submit one or to add/remove its attachments. No `ideas/permissions.py` and no Ideas permission code - see [Ideas](#ideas-target--partly-implemented) | S2-002 |
| Idea visibility | The `PUBLIC`/`ORGANIZATION`/`DEPARTMENT`/`PRIVATE` vocabulary, defaulting to `PRIVATE` (fail closed) | S2-001 |
| Storage boundary | `Attachment` as metadata only; attachment bytes can never be stored in PostgreSQL. Filesystem storage today, swappable for object storage via `ATTACHMENTS_STORAGE_BACKEND` | S2-001, operations in S2-007 |
| Domain documentation | [`ideas-domain.md`](ideas-domain.md) | S2-001 |

See [`ideas-domain.md`](ideas-domain.md) for the entities, the
authorization and storage boundaries, and the planned service, selector,
GraphQL and frontend seams.

### Implemented (Sprint 1, in progress)

| Area | What exists | Task |
| ---- | ----------- | ---- |
| Identity domain | `identity` app: `User` (email login, hashed passwords), `ExternalIdentity` foundation | S1-002 |
| Registration | `register` GraphQL mutation, email normalization, password/phone validation | S1-002 |
| Authentication | `login`/`refreshToken`/`logout` mutations, JWT access tokens, rotating `RefreshSession` refresh credential in an HttpOnly cookie, `me` query | S1-003 |
| Frontend auth state | `AuthProvider`/`useAuth()`, in-memory access token, `/app` protected route | S1-003 |
| Google/OAuth sign-in | `googleLogin` GraphQL mutation, Google ID-token (OIDC) verification, account provisioning/linking policy, `GoogleAuthButton` wired to Google Identity Services | S1-004 |
| Current user | `me` query as the authoritative identity source, `RequireAuth` gating `/app`, `OrganizationSwitcher`/workspace | S1-005 |
| Organizations | `Organization`/`Membership` models, `createOrganization` bootstrap mutation, `meOrganizations`/`meMemberships` | S1-006 |
| Roles and permissions | `Role`/`Permission`/`MembershipRole`, organization-scoped system `Owner` role, `organizationRoles` | S1-007 |
| Authorization | Permission-gated `organization`/`organizationMembers`/`organizationRoles` queries and `assignRoleToMembership`/`removeRoleFromMembership` mutations; tenant isolation across every one | S1-008 |
| Access-token lifecycle | `tokenStore` holding the token *and* its expiry; `tokenRefresh` proactive, single-flight refresh; `registerRequest` wired into `SignUpForm` | S1-009 |
| Security hardening | Authentication rate limiting (login/googleLogin/refreshToken/register), shared `replay_protection`/`auth_throttle` cache aliases required in deployed environments, end-to-end auth, tenant-isolation and Google-flow integration suites | S1-009 |
| Password reset | `requestPasswordReset`/`resetPassword` mutations, single-use hashed `EmailToken`, plaintext reset email over SMTP, all sessions on the account revoked by a successful reset, `/reset-password` wired to the real mutation | S1-010 |
| Account activation | `activateAccount`/`resendActivationEmail` mutations, a confirmation link emailed at registration, `/activate-account` route; confirmation is *not* a login requirement | S1-010 |
| Emailed-link throttling | Per-address and per-client limits on all four operations, keyed on the submitted address so a miss costs the same budget as a hit | S1-010 |

### Implemented (Sprint 0)

| Area | What exists | Task |
| ---- | ----------- | ---- |
| Repository | Monorepo layout, root docs, `.gitignore`, issue and PR templates | S0-001 |
| Backend | Django 5.2 project (`backend/config`), split settings (`base`, `local`, `production`), `/health/` endpoint | S0-002 |
| Frontend | React 19 + TypeScript + Vite 8, React Router, Tailwind CSS 4, shared GraphQL client, error boundary | S0-003 |
| Database | PostgreSQL via `DATABASE_URL` (`psycopg`); no SQLite fallback | S0-004 |
| GraphQL | `/graphql/` via Strawberry Django with a foundation schema (`apiStatus` query, `ping` mutation) | S0-005 |
| Environment configuration | `ENVIRONMENT` convention, per-app `.env.example`, fail-fast production validation, CORS/CSRF settings | S0-006 |
| Security foundation | Production HTTPS/HSTS/cookie/header settings, local-vs-production guards, security tests | S0-011 |
| Testing | Backend pytest + pytest-django + pytest-cov; frontend Vitest + React Testing Library | S0-007 |
| Code quality | Backend Ruff; frontend Oxlint, oxfmt and TypeScript checking | S0-008 |
| CI | GitHub Actions workflow validating backend and frontend on pull requests and pushes to `develop`/`main`; no deployment | S0-009 |
| Documentation | Development, testing, Git workflow and environment guides | S0-010 |

How these are used day to day: [`development.md`](development.md),
[`testing.md`](testing.md), [`git-workflow.md`](git-workflow.md).

### Not implemented (planned)

- Business domain apps other than `identity`, `organizations`, `ideas` and
  `reviews`: opportunities, proposals, developers, projects, tasks,
  notifications, impact, files, audit
- Review *management*: the lifecycle transitions exist and work, but there is
  no review queue, no reviewer assignment, no reasons recorded against a
  `CHANGES_REQUESTED` idea, and no dashboard of any kind
- A ranking or scoring of the votes that now exist, any "who voted" listing,
  threaded comment replies, comment moderation and soft delete, and rate
  limiting — this project has no throttling mechanism to extend. Also: an
  actual object-storage bucket for attachments (S2-007 implemented the
  operations against Django's filesystem backend; see
  [Storage](#storage-target--filesystem-today-object-storage-later)),
  presigned/signed attachment URLs, and any AI processing of attachment
  content
- A discovery *algorithm*: there is no ranking, recommendation, trending or
  social feed. S2-004 filters and pages what a reader may see; it does not
  decide what they should see, and no engagement data is collected
- The `DEPARTMENT` visibility tier
- An authenticated Google-account-linking flow (today's Google sign-in only
  ever authenticates or provisions a *new* account - it never links to an
  existing one, by verified email or otherwise)
- The Department tier of multi-tenancy (`Idea.visibility` reserves a
  `department` value but nothing sets or filters on it), member invitations,
  a role-management or permission-editor UI, and logout-everywhere
- Redis + Celery background processing
- AI gateway
- The object storage bucket itself, and a presigned/signed-URL upload or
  download path (S2-007 implemented the attachment metadata boundary *and*
  the upload/download operations, routed through this server against
  Django's filesystem storage backend; see
  [Storage](#storage-target--filesystem-today-object-storage-later))
- Audit logging
- React Query in the frontend
- Deployment automation and any deployed environment (development, staging,
  production)

## Repository Structure

```
automation-platform/
├── backend/                  # Django project
│   ├── config/               # settings (base/local/production), urls, health view, wsgi/asgi
│   ├── graphql_api/          # GraphQL infrastructure (not a business domain)
│   ├── identity/             # business domain: accounts, sessions, external identities, emailed links
│   ├── organizations/        # business domain: organizations, memberships, roles, permissions
│   ├── ideas/                # business domain: ideas, categories, comments, votes, attachments
│   ├── tests/                # cross-cutting pytest suite (settings, security, integration)
│   ├── manage.py
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── pytest.ini
│   ├── ruff.toml
│   ├── .coveragerc
│   └── .env.example
├── frontend/                 # React + TypeScript + Vite app
│   ├── src/                  # app, components, graphql, layouts, lib, routes, test, types
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts        # Vite and Vitest configuration
│   ├── .oxlintrc.json
│   ├── .oxfmtrc.json
│   └── .env.example
├── docs/
│   ├── architecture.md
│   ├── environments.md
│   ├── development.md
│   ├── testing.md
│   ├── ideas-domain.md       # business domain: ideas & problem submission
│   └── git-workflow.md
├── infrastructure/           # Placeholder: no infrastructure implemented yet
├── scripts/                  # Placeholder: no scripts yet
├── .github/
│   ├── workflows/ci.yml      # CI (validation only)
│   ├── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE.md
├── .env.example              # index pointing at the per-app templates
├── .gitignore
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
├── CHANGELOG.md
└── LICENSE
```
