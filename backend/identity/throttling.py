"""
Authentication throttling (S1-009).

The unauthenticated entry points that can be turned into an amplifier -
`login`, `refreshToken`, `googleLogin`, `register`, and the emailed-link
flows (`requestPasswordReset`, `resetPassword`, `activateAccount`,
`resendActivationEmail`) - are the ones this module guards. Each of them is
reachable with no session at all. The first two are reachable with a value the
caller chooses, so an attacker can issue unlimited attempts; the emailed ones
are reachable with an address the caller chooses, so they can be used to mail
someone else on demand. Both are the same problem, and the same limit answers
it.

Why a cache, and which one. Counting has to be shared by every process: with
a per-process counter, worker 1 blocks the 11th attempt while workers 2..N
each allow 10 more, and a single attacker simply gets N times the budget -
which is precisely the "fragile in-memory mechanism" this exists to avoid.
So the counters live in the `auth_throttle` cache alias (see
config/settings/base.py), and `config/settings/production.py` refuses to start
a deployed environment whose security-critical aliases resolve to a
per-process backend. The same guarantee the Google ID-token replay check
relies on, for the same reason.

The algorithm is a fixed window: a key per (operation, scope, subject) is
created with `cache.add` and then incremented with `cache.incr`. Both are
atomic on every backend that matters (`add` is `SETNX`, `incr` is `INCR` on
Redis; both are lock-guarded in-process for localmem), so a genuine burst of
concurrent requests cannot each read the same count and all be admitted.

Fixed windows, not sliding ones, on purpose: a sliding window needs
per-request timestamps and an atomic range query, which no Django cache
backend offers portably. The cost is the classic boundary burst - up to
`2 * limit` attempts across a window edge - which is not a meaningful
weakening at these limits, and is not worth a bespoke shared-datastore
mechanism.

**Failing closed.** If the cache is unreachable, every attempt is refused
rather than admitted. That is the uncomfortable choice, and it is the
correct one: this limit is the only thing standing between an attacker and
unlimited password guessing, and "the cache is down" is not evidence that
the caller is legitimate. It does mean a cache outage takes authentication
down with it, which is the deliberate trade-off - the alternative is a
silent, invisible removal of a security control.

Subjects are hashed before becoming cache keys. A Redis key is readable in
`MONITOR`, in slow-query logs and in a crash dump; an account's email
address and a client's IP address are personal data that has no business
being stored in plaintext as a cache key.
"""

import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import InvalidCacheBackendError, caches

# Cache-key namespace. A prefix, not a secret.
_CACHE_KEY_PREFIX = 'identity:auth_throttle:'

# The one message every throttled operation answers with. It says what
# happened and nothing about who: no account name, no email, no IP, no
# operation-specific detail that could distinguish "this account is locked"
# from "this client is locked", and no confirmation that the submitted
# address corresponds to an account at all (a caller that reaches this
# message reached it by making too many attempts itself, whether or not the
# account exists - see this module's docstring on why that leaks nothing).
THROTTLED_MESSAGE = 'Too many attempts. Please try again later.'


class ThrottledError(Exception):
    """
    Raised when an operation's limit is exhausted.

    `identity.schema` turns this into a `success: false` payload carrying
    `THROTTLED_MESSAGE`. It is deliberately not a subclass of
    `identity.authentication.AuthenticationError`: a throttle is not an
    authentication failure, and conflating the two is what would let a
    future change to one message silently change the other.
    """


@dataclass(frozen=True)
class ThrottlePolicy:
    """
    One limit: at most `limit` attempts per `window_seconds`, per subject.

    `name` is part of the cache key, so two policies with the same limit
    still count independently - an account's login budget must not be
    consumed by its registration attempts, or vice versa.
    """

    name: str
    limit: int
    window_seconds: int


# --- policies ----------------------------------------------------------------------
#
# The numbers are deliberately generous for a human and tight for a script.
# They are not a substitute for a password-strength requirement or for MFA;
# they exist to make bulk guessing and bulk account creation expensive
# relative to their value.

# Login, per account: the primary credential-guessing limit. Ten wrong
# passwords per quarter of an hour, generous for a person mistyping their
# own password repeatedly, and 360 guesses a day even if an attacker
# sprints the window boundary on every attempt.
LOGIN_PER_ACCOUNT = ThrottlePolicy('login.account', limit=10, window_seconds=900)

# Login, per client address: the spray limit. Higher than the per-account
# one on purpose - many people legitimately share an address (an office, a
# mobile carrier's NAT, a university), and this counter cannot tell them
# apart. It is here to cap the total, not to police individuals.
LOGIN_PER_CLIENT = ThrottlePolicy('login.client', limit=20, window_seconds=300)

# Google sign-in, per client address. There is no account to key on before
# the credential has been verified (and keying on the credential itself
# would be redundant - a replayed credential is already rejected outright by
# the replay check, at no cost to the caller). Keying on the address is
# therefore the only meaningful scope, and it stops one client grinding
# through Google-issued credentials or, more usefully, hammering this
# backend with signature-verification work.
GOOGLE_LOGIN_PER_CLIENT = ThrottlePolicy('google_login.client', limit=20, window_seconds=300)

# Refresh, per refresh credential. Scoped to the credential rather than the
# address because one threat here is a stolen cookie being replayed from
# elsewhere: this caps how fast a single stolen credential can be ground
# against the server, including after it has been rotated away (the replay
# itself is rejected, but the attempt is still counted), and leaves a shared
# address completely alone.
REFRESH_PER_CREDENTIAL = ThrottlePolicy('refresh.credential', limit=60, window_seconds=300)

# Refresh, per client address. The per-credential limit above cannot help an
# attacker who simply refreshes in a loop, because every successful refresh
# hands them a *new* credential and therefore a fresh budget - which is also
# why it never inconveniences an honest client. This is the limit that bounds
# a flood from one address, and its number is set by the number of *people*
# rather than of requests: an access token lasts fifteen minutes by default,
# so in steady state each user contributes about one refresh per three
# five-minute windows, and 600 per window leaves room for well over a
# thousand users behind one address. A loop-refresher exhausts it in seconds.
REFRESH_PER_CLIENT = ThrottlePolicy('refresh.client', limit=600, window_seconds=300)

# Registration, per client address. Ten accounts an hour from one address is
# far above what a person onboarding a team needs and far below what makes
# bulk account creation worth doing. Not keyed by email: every new account
# has a new email, so a per-email limit would never be reached by exactly
# the abuse it was meant to stop.
REGISTER_PER_CLIENT = ThrottlePolicy('register.client', limit=10, window_seconds=3600)

# Password-reset request, per submitted address. The tightest limit in this
# module, and the reason is email, not guessing: every request that reaches an
# existing account mints a token and sends a real message, so an unmetered
# version of this is a way to use the platform as a mail cannon - filling
# someone else's inbox, and getting a third party's domain to absorb the
# bounces and the reputation damage. Five an hour per address is well above
# what someone locked out of their own account needs (they request once, wait,
# retry) and far below anything worth automating.
#
# Keyed on the *submitted* address, never a looked-up account, for the same
# reason login does: whether this address has an account cannot change the
# answer, or the counter itself becomes an enumeration oracle. That is why
# both the hit and the miss cost the caller the same budget.
PASSWORD_RESET_REQUEST_PER_ACCOUNT = ThrottlePolicy(
    'password_reset.request.account', limit=5, window_seconds=3600
)

# Password-reset request, per client address. What bounds a *spray* - one
# address, many different victims - which the per-account counter above cannot
# see at all, since each victim's address carries its own separate budget.
# Without it, an attacker with a list could mail every name on it.
PASSWORD_RESET_REQUEST_PER_CLIENT = ThrottlePolicy(
    'password_reset.request.client', limit=20, window_seconds=3600
)

# Password reset (spending a token), per client address. This is not a
# credential-guessing limit: the token is 384 bits of `secrets` output, which
# no amount of trying guesses. It is here because each attempt is a database
# write - a password re-hash, a revoke-all-sessions update - performed by an
# anonymous caller who controls the input, so the number to bound is "how much
# work can an unauthenticated request make this server do", same as the login
# per-client limit but for a cheaper operation, hence a higher number.
PASSWORD_RESET_PER_CLIENT = ThrottlePolicy('password_reset.client', limit=30, window_seconds=900)

# Changing a password while signed in, per account. Unlike a reset this checks
# the *current* password, so it is a guessing surface for anyone holding a
# stolen access token; the same budget as login's per-account limit.
PASSWORD_CHANGE_PER_ACCOUNT = ThrottlePolicy(
    'password_change.account', limit=10, window_seconds=900
)

# Account activation (spending a token), per client address. Same reasoning as
# the reset limit above and the same order of magnitude; kept as a separate
# policy rather than shared because the two flows have genuinely different
# traffic - an activation link is followed once, a reset link maybe a few times
# - and one deployment may legitimately want them set differently.
ACTIVATION_PER_CLIENT = ThrottlePolicy('activation.client', limit=30, window_seconds=900)

# Resending the activation email, per submitted address. Tighter than the
# request-per-account limit above for the same mail-cannon reason, and tighter
# still in effect: a resend is only ever wanted because the first one was
# lost, so a handful per day is a lost-inbox budget, not a busy-day one.
ACTIVATION_RESEND_PER_ACCOUNT = ThrottlePolicy(
    'activation.resend.account', limit=3, window_seconds=86400
)

# Resending the activation email, per client address, for the same spray
# reason as PASSWORD_RESET_REQUEST_PER_CLIENT.
ACTIVATION_RESEND_PER_CLIENT = ThrottlePolicy(
    'activation.resend.client', limit=10, window_seconds=3600
)


def _throttle_cache():
    """
    The cache holding the counters.

    Resolved per call from settings, like `identity.google_oauth`'s replay
    cache, so the alias is configuration rather than a constant. An
    `InvalidCacheBackendError` here means settings and code disagree about
    the alias set - a deployment error that must not silently disable
    throttling, so it propagates as a throttle (fail closed) rather than
    being swallowed.
    """
    return caches[settings.AUTH_THROTTLE_CACHE_ALIAS]


def _cache_key(policy: ThrottlePolicy, subject: str) -> str:
    digest = hashlib.sha256(subject.encode('utf-8')).hexdigest()
    return f'{_CACHE_KEY_PREFIX}{policy.name}:{digest}'


def _client_address(request) -> str:
    """
    The request's originating address, for per-client policies.

    `REMOTE_ADDR` only, and never a forwarded header. Behind a TLS-
    terminating proxy every request shares the proxy's address, which
    over-restricts - a real cost, and the reason a trusted-proxy setting
    belongs here before that is deployed. It is still the right default: a
    forwarded header is attacker-controlled unless the proxy provably
    overwrites it, and trusting it blindly would let anyone opt out of
    per-client limiting by inventing an `X-Forwarded-For`, which is strictly
    worse than sharing a limit with everyone behind one proxy.
    """
    return (request.META.get('REMOTE_ADDR') or '').strip() or 'unknown-client'


def register_attempt(policy: ThrottlePolicy, subject: str) -> None:
    """
    Count one attempt against `policy`/`subject` and raise if over the limit.

    Counted whether or not the attempt would have succeeded, and *before*
    the attempt is evaluated. That ordering matters twice: counting every
    attempt keeps a locked-out caller's own retries from extending the
    window, and checking first means the expensive part (a password hash, a
    Google signature verification) is never performed for a request that is
    already over the limit.

    `cache.add` then `cache.incr`, so the first attempt in a window creates
    the key at 0 and the increment makes it 1, with no window in which a
    concurrent request sees "no key" and every request in that window is
    admitted. If the increment finds nothing - the key was evicted between
    the two calls, or expired mid-flight - the attempt is treated as the
    first of a new window rather than raising, so an unlucky eviction
    resets the budget instead of locking anybody out.
    """
    key = _cache_key(policy, subject)
    cache = _throttle_cache()
    cache.add(key, 0, timeout=policy.window_seconds)
    try:
        count = cache.incr(key)
    except ValueError:
        # Evicted or expired between `add` and `incr`: start a new window.
        cache.add(key, 1, timeout=policy.window_seconds)
        return
    if count > policy.limit:
        raise ThrottledError(THROTTLED_MESSAGE)


def clear_attempts(policy: ThrottlePolicy, subject: str) -> None:
    """
    Forget `subject`'s counter under `policy`.

    Called only after a *successful* authentication, and it does not undo a
    throttle that is already in force - an over-limit request is refused
    before it is ever authenticated, so a throttled caller recovers by
    waiting out the window, not by getting lucky. What it does do is stop a
    successful sign-in from *reaching* the limit: someone who mistyped a few
    times, signed in correctly, and then mistyped a few more is starting from
    zero, not from the sum of both runs.

    That is safe because reaching this point requires already knowing the
    credential: a caller who can clear a per-account counter could already
    authenticate, so they gain nothing by clearing it - and clearing only
    that account's counter means it cannot be used to launder attempts
    against any other account. The per-client counter is deliberately *not*
    cleared: it is the spray limit, and this account's owner clearing it
    would weaken the protection for everyone else's account behind the same
    address.
    """
    _throttle_cache().delete(_cache_key(policy, subject))


def _guard(policy: ThrottlePolicy, subject: str) -> None:
    try:
        register_attempt(policy, subject)
    except (ThrottledError, InvalidCacheBackendError):
        raise ThrottledError(THROTTLED_MESSAGE) from None


# --- guards for each operation ------------------------------------------------------


def guard_login(request, email: str) -> None:
    """
    Admit or refuse one `login` attempt.

    Both counters are checked, and the per-account one first: it is the
    tighter limit, so the cheapest decision is the one that most often
    refuses. The per-account subject is the *submitted* address, normalized
    so `Ada@Example.com` and `ada@example.com` cannot each hold a separate
    budget. The subject is taken from the request, never from the
    authenticated user (there isn't one yet) - and equally never checked
    against a stored account, so whether the address exists cannot change the
    answer.
    """
    account = _normalize_email_subject(email)
    _guard(LOGIN_PER_ACCOUNT, account)
    _guard(LOGIN_PER_CLIENT, _client_address(request))


def clear_login_account(email: str) -> None:
    """
    Reset the per-account login counter after a successful sign-in, so the
    next run of mistyped passwords starts from zero rather than from the
    accumulated total. The per-client counter is left alone on purpose (see
    `clear_attempts`).
    """
    clear_attempts(LOGIN_PER_ACCOUNT, _normalize_email_subject(email))


def guard_refresh(raw_refresh_token: str, request) -> None:
    """
    Admit or refuse one `refreshToken` attempt.

    Two counters, because they defend against two different things: the
    per-client one bounds a flood from one address (and is the only one a
    loop-refreshing attacker runs into, since every successful refresh hands
    them a new credential and therefore a new per-credential budget), and the
    per-credential one bounds how fast one stolen cookie can be ground
    against the server.

    A missing credential still reaches `auth_service.refresh`, which answers
    generically - there is nothing to count against an empty subject.
    """
    _guard(REFRESH_PER_CLIENT, _client_address(request))
    if not raw_refresh_token:
        return
    _guard(REFRESH_PER_CREDENTIAL, raw_refresh_token)


def guard_google_login(request, credential: str) -> None:
    """
    Admit or refuse one `googleLogin` attempt, by client address.

    The credential is not used as the subject: a replayed credential is
    already rejected by `identity.google_oauth`'s replay check at no cost,
    while an attacker grinding through *distinct* credentials is what this
    is for, and that is not visible from the credential alone.
    """
    _guard(GOOGLE_LOGIN_PER_CLIENT, _client_address(request))


def guard_registration(request) -> None:
    """Admit or refuse one `register` attempt, by client address."""
    _guard(REGISTER_PER_CLIENT, _client_address(request))


def guard_password_reset_request(request, email: str) -> None:
    """
    Admit or refuse one `requestPasswordReset` attempt.

    Two counters, the per-account one first because it is the tighter of the
    pair and the cheaper decision is the one that most often refuses. The
    per-account subject is the *submitted* address, normalized and never
    checked against a stored account, so the answer cannot depend on whether
    that address has an account - which would otherwise make the throttle
    itself the enumeration oracle this whole flow exists to avoid.
    """
    _guard(PASSWORD_RESET_REQUEST_PER_ACCOUNT, _normalize_email_subject(email))
    _guard(PASSWORD_RESET_REQUEST_PER_CLIENT, _client_address(request))


def guard_password_reset(request) -> None:
    """
    Admit or refuse one `resetPassword` attempt, by client address.

    Scoped to the address rather than to the presented token: the token is
    384 bits of `secrets` output, so a "grind one token" counter would only
    ever be measuring the client's request rate anyway, and keying on the
    token would additionally let a caller who happened to hold a valid one
    escape the limit by presenting a different (invalid) one.
    """
    _guard(PASSWORD_RESET_PER_CLIENT, _client_address(request))


def guard_password_change(user_id: object) -> None:
    """Admit or refuse one `changePassword` attempt by the signed-in account."""
    _guard(PASSWORD_CHANGE_PER_ACCOUNT, str(user_id))


def guard_activation(request) -> None:
    """
    Admit or refuse one `activateAccount` attempt, by client address.

    Same reasoning as `guard_password_reset`: the token is unguessable, and
    the work each attempt causes (a lookup and a flag flip) is what needs a
    ceiling.
    """
    _guard(ACTIVATION_PER_CLIENT, _client_address(request))


def guard_activation_resend(request, email: str) -> None:
    """
    Admit or refuse one `resendActivationEmail` attempt.

    Both counters, per-account first, on exactly the same reasoning as
    `guard_password_reset_request` - including the requirement that the
    per-account subject is the submitted address and is never resolved to a
    user, so a miss costs the caller the same budget as a hit.
    """
    _guard(ACTIVATION_RESEND_PER_ACCOUNT, _normalize_email_subject(email))
    _guard(ACTIVATION_RESEND_PER_CLIENT, _client_address(request))


def _normalize_email_subject(email: str) -> str:
    """
    Canonicalize an email for use as a throttle subject.

    `strip` + `casefold` rather than `User.normalize_email`: that helper
    also rewrites the domain part of a Gmail-style address, which is right
    for a uniqueness constraint and irrelevant here - what matters is only
    that the same mailbox, typed the same handful of ways, is one subject.
    Nothing derived from this value is stored or compared against an
    account; it is hashed into a cache key and forgotten.
    """
    return (email or '').strip().casefold()
