# Backend

Django backend, organized as a modular monolith around business domains
(identity, organizations, ideas, reviews, opportunities, proposals,
developers, projects, tasks, notifications, impact, files, audit).

**Status:** Sprint 1, in progress. Sprint 0 delivered environment-driven
settings, a PostgreSQL database, and a foundation GraphQL endpoint (no
business domain apps or authentication). Sprint 1 has added the `identity`
app: the platform `User` model, registration (S1-002), email/password
login with JWT access tokens + rotating refresh sessions (S1-003), and
Google/OAuth sign-in via Google Identity Services' ID-token flow (S1-004).
Not yet implemented: email verification, the forgot/reset password
backend, and every other business domain.

See [`/docs/architecture.md`](../docs/architecture.md) for the target
backend architecture.

## Layout

```
backend/
├── manage.py
├── requirements.txt        # runtime dependencies
├── requirements-dev.txt    # + test and lint tooling (pytest, pytest-django, pytest-cov, ruff)
├── pytest.ini
├── ruff.toml               # lint + format configuration (Ruff)
├── .coveragerc
├── tests/                  # pytest suite (see Testing below)
├── config/
│   ├── settings/
│   │   ├── base.py         # shared settings, reads from environment
│   │   ├── local.py        # development defaults (DEBUG, Vite CORS/CSRF origins)
│   │   └── production.py   # deployed envs: validates config, secure defaults
│   ├── urls.py             # mounts /health/ and /graphql/
│   ├── views.py            # health check
│   ├── wsgi.py
│   └── asgi.py
├── identity/                # Identity domain: User, registration, authentication
│   ├── models.py           # User, ExternalIdentity, RefreshSession
│   ├── services.py         # registration business logic
│   ├── authentication.py   # login/refresh/logout/Google sign-in business logic
│   ├── tokens.py           # JWT access-token issue/verify
│   ├── google_oauth.py     # Google ID token (OIDC) verification
│   ├── schema.py           # this domain's GraphQL Query/Mutation slice
│   ├── admin.py, forms.py
│   ├── migrations/
│   └── tests/
├── administration/         # Platform administration console (see docs/administration.md)
│   ├── models.py           # AdminAuditEntry + the administration.* permissions
│   ├── authorization.py    # require_admin / capabilities_for
│   ├── selectors.py        # cross-tenant reads for the console
│   ├── services.py         # audited administrative operations
│   ├── schema.py           # admin* GraphQL fields
│   ├── views.py            # audited evidence download
│   └── management/commands/grant_platform_admin.py
└── graphql_api/            # GraphQL infrastructure (not a business domain)
    ├── apps.py
    ├── schema.py           # root Query/Mutation - merges identity.schema in
    └── views.py            # Strawberry Django view, mounted at /graphql/
```

## Setup

### 1. Create the PostgreSQL database

Requires a running local PostgreSQL server. Create a project-specific
role and database (do not use the `postgres` superuser role for the
app):

```bash
sudo -u postgres psql <<'EOF'
CREATE USER automation_platform WITH PASSWORD 'change-me';
CREATE DATABASE automation_platform_dev OWNER automation_platform;
\c automation_platform_dev
GRANT ALL ON SCHEMA public TO automation_platform;
EOF
```

Pick your own password and use it in `DATABASE_URL` below — never commit
a real password.

### 2. Configure the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set DJANGO_SECRET_KEY, DATABASE_URL and other values
python manage.py migrate
python manage.py runserver
```

`DJANGO_SETTINGS_MODULE` defaults to `config.settings.local`. Deployed
environments (development, staging, production) set it to
`config.settings.production`, which refuses to start on missing or unsafe
configuration. See [`/docs/environments.md`](../docs/environments.md) for
every variable and the environment conventions.

`DJANGO_SECRET_KEY`, `DJANGO_JWT_SIGNING_KEY` and `DATABASE_URL` (in
`backend/.env`) are required and have no defaults. `DJANGO_JWT_SIGNING_KEY`
signs JWT access tokens (Sprint 1) and must be a different value from
`DJANGO_SECRET_KEY` - generate both the same way. `DATABASE_URL` must point
at the database created above, e.g.:

```
DATABASE_URL=postgres://automation_platform:<your-password>@localhost:5432/automation_platform_dev
```

### 3. Verify the connection

```bash
python manage.py check
python manage.py migrate
```

`python manage.py migrate` succeeding against PostgreSQL (rather than
failing to connect, or silently using SQLite) confirms the database is
wired up correctly. You can also confirm the active backend directly:

```bash
python manage.py shell -c "from django.db import connection; print(connection.vendor)"
# postgresql
```

### 4. Seed development categories (optional)

An idea needs a category before it can be submitted. Categories are managed in
the administration console (`/app/admin/categories`); for local development
you can also load the temporary sample set:

```bash
python manage.py seed_dev_categories
```

It is idempotent and never changes or removes existing categories. These are
development data, not the production taxonomy - see
[Development categories](../docs/ideas-domain.md#development-categories-temporary-seed).

### 5. Make yourself a platform administrator (optional)

The administration console at `/app/admin` is for platform administrators
only - organization Owners are not administrators. Grant an existing account
every console permission (audited):

```bash
python manage.py grant_platform_admin you@example.com
python manage.py grant_platform_admin you@example.com --revoke
```

A superuser (`createsuperuser`) also has every console permission. See
[`docs/administration.md`](../docs/administration.md).

## Testing

```bash
pip install -r requirements-dev.txt   # once; adds pytest, pytest-django, pytest-cov
pytest                                # run the whole suite
pytest --cov                          # with coverage (config in .coveragerc)
pytest --cov --cov-report=html        # browsable report in htmlcov/
pytest tests/test_graphql.py -k ping  # a single file / test
```

Tests run against `config.settings.local` and the same `DATABASE_URL` as
development. pytest-django creates a separate, disposable `test_<name>`
database for the run and drops it afterwards, so no data in your
development database is touched. The database role therefore needs
`CREATEDB` (the role created in [Setup](#1-create-the-postgresql-database)
has it). No test settings or credentials are committed.

| Test file                   | Covers |
| --------------------------- | ------ |
| `tests/test_configuration.py` | Django loads and passes system checks |
| `tests/test_health.py`      | `GET /health/` |
| `tests/test_graphql.py`     | `/graphql/`: `apiStatus`, `ping`, GraphQL errors |
| `tests/test_database.py`    | PostgreSQL-backed test database lifecycle |
| `tests/test_cors.py`        | CORS allowed on `/graphql/` only; credentials require an explicit origin |
| `tests/test_settings.py`    | Production settings reject unsafe configuration |
| `tests/test_security.py`    | Security invariants: HTTPS, HSTS, cookies, headers, CORS, local/CI vs. production |
| `identity/tests/test_models.py` | `User`, `ExternalIdentity`, `RefreshSession`: creation, email normalization/uniqueness, password hashing |
| `identity/tests/test_services.py` | Registration business logic and its validation rules |
| `identity/tests/test_schema.py` | `register` mutation via the real `/graphql/` endpoint |
| `identity/tests/test_tokens.py` | JWT access-token issue/verify, including tampered/expired/wrong-type tokens |
| `identity/tests/test_google_oauth.py` | Google ID token verification: signature/issuer/audience/expiry failures, missing claims, name-claim fallbacks |
| `identity/tests/test_authentication.py` | Login, refresh rotation/replay, logout, resolving a user from an access token, `authenticate_with_google`'s account resolution/linking/provisioning and concurrency handling |
| `identity/tests/test_authentication_schema.py` | `login`/`refreshToken`/`logout`/`me`/`googleLogin` via the real `/graphql/` endpoint, including the refresh cookie |
| `identity/tests/test_admin.py` | Admin restrictions (e.g. `RefreshSession` rows can't be added manually) |

`production.py` is exercised in subprocesses (each case imports it with a
different environment), so it shows 0% in the coverage report even though
its validation rules are tested.

## Code quality

[Ruff](https://docs.astral.sh/ruff/) is the only Python linter, import sorter
and formatter; it is configured in [`ruff.toml`](ruff.toml) and installed with
`requirements-dev.txt`. Rules cover pyflakes (unused imports/variables),
pycodestyle, isort, bugbear, pyupgrade, simplify, flake8-django, pytest style,
bandit security checks, no stray `print`, and a McCabe complexity limit of 10.
Migrations are excluded; code style is single quotes, 100 columns.

```bash
ruff check .              # lint (add --fix for safe auto-fixes)
ruff format --check .     # formatting check (CI-friendly)
ruff format .             # apply formatting
```

## Health check

`GET /health/` returns `{"status": "ok"}` with no auth and no
dependencies, for CI and deploy tooling.

## GraphQL API

GraphQL is the primary application API; REST is reserved for
specialized/infrastructure operations (file uploads, webhooks, health
checks, OAuth callbacks). See [`/docs/architecture.md`](../docs/architecture.md).

**Endpoint:** `POST /graphql/` (a GraphiQL IDE is also served there in
development, when `DEBUG=True`).

`graphql_api/schema.py` holds only foundation/infrastructure operations and
merges in each business domain's own schema slice by inheritance (e.g.
`identity.schema.Query`/`Mutation`) - it does not implement domain logic
itself:

- `Query.apiStatus` — returns `{ status, version, djangoVersion }`, proving
  the GraphQL layer resolves end to end.
- `Mutation.ping(message)` — echoes its input, proving the mutation root
  resolves.

**Identity (Sprint 1)**, defined in `identity/schema.py`:

- `Mutation.register(input: RegisterInput!)` — creates a `User`. See
  `identity/services.py` for validation (email normalization/uniqueness,
  password strength, phone format).
- `Mutation.login(input: LoginInput!)` — email/password authentication.
  Returns a short-lived JWT access token in the response body and sets an
  HttpOnly, `SameSite=Lax` refresh-session cookie (scoped to `/graphql/`,
  never returned as a GraphQL field or storable in `localStorage`).
- `Mutation.googleLogin(input: GoogleLoginInput!)` — Google/OAuth sign-in
  (S1-004). Takes the ID token (JWT) issued by Google Identity Services'
  Sign In With Google flow, verifies it server-side
  (`identity/google_oauth.py`), and on success behaves exactly like
  `login`: same `AuthPayload` shape, same access token + refresh cookie
  mechanics. See `docs/architecture.md` for the account
  resolution/linking/provisioning policy.
- `Mutation.refreshToken` — exchanges the refresh cookie for a new access
  token, rotating the refresh credential (the old one becomes invalid).
  Takes no arguments; the credential comes only from the cookie.
- `Mutation.logout` — revokes the current refresh session and clears its
  cookie.
- `Query.me` — the authenticated user (from the `Authorization: Bearer
  <token>` header), or `null`.

Every mutation returns a payload with `success`/`message` rather than a raw
GraphQL error for expected failures (invalid credentials, duplicate email,
...); a raw error means something unexpected happened. Login/refresh/Google
sign-in failures each share one generic message - see
`identity/authentication.py` for why. None of these ever return a password
or password hash. See `docs/architecture.md` for the cookie/CORS/CSRF
design and `docs/environments.md` for the new environment variables
(`DJANGO_JWT_SIGNING_KEY`, `ACCESS_TOKEN_LIFETIME_MINUTES`,
`REFRESH_TOKEN_LIFETIME_DAYS`, `GOOGLE_OAUTH_CLIENT_ID`).

Business-domain schemas beyond identity (organizations, ideas, ...) will be
added as their own Django apps in later sprints and merged in the same way,
not built into separate schema instances.

Built with [Strawberry](https://strawberry.rocks/) (`strawberry-graphql-django`),
a code-first, type-hint-driven GraphQL library.

Example query:

```bash
curl -X POST http://localhost:8000/graphql/ \
  -H "Content-Type: application/json" \
  -d '{"query": "{ apiStatus { status version djangoVersion } }"}'
```
