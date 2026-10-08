# Testing and Continuous Integration

This document describes the checks the repository actually runs, locally and
in CI. Setup is in [`development.md`](development.md); the pull request flow
is in [`git-workflow.md`](git-workflow.md).

Everything here is validation only. Nothing is deployed by CI.

## Backend

Run from `backend/` with the virtual environment active and PostgreSQL
running (`pip install -r requirements-dev.txt` installs the tools).

| Tool | Purpose | Command |
| ---- | ------- | ------- |
| pytest + pytest-django | Test suite | `pytest` |
| pytest-cov | Coverage report (configured in `.coveragerc`) | `pytest --cov` |
| Django system checks | Configuration sanity | `python manage.py check` |
| Django migration check | Fails if models changed without a migration | `python manage.py makemigrations --check --dry-run` |
| Ruff (lint) | Linting and import sorting (configured in `ruff.toml`) | `ruff check .` |
| Ruff (format) | Formatting | `ruff format --check .` (check) / `ruff format .` (apply) |

Notes:

- Tests run against `config.settings.local` (set in `pytest.ini`) and the
  `DATABASE_URL` in your environment. pytest-django creates and drops its own
  `test_<name>` database, so your development data is untouched. The database
  role needs `CREATEDB`.
- Cross-cutting infrastructure tests live in `backend/tests/` as `test_*.py`;
  each business-domain app owns its own tests under `<app>/tests/` (e.g.
  `backend/identity/tests/`). GraphQL tests go through the real `/graphql/`
  endpoint.
- `config/settings/production.py` is exercised in subprocesses, so it shows
  0% in the coverage report even though its validation rules are tested.
- Run a subset with `pytest tests/test_graphql.py -k ping`, or get an HTML
  report with `pytest --cov --cov-report=html`.

More detail: [`backend/README.md`](../backend/README.md#testing).

## Frontend

Run from `frontend/` after `npm ci`.

| Tool | Purpose | Command |
| ---- | ------- | ------- |
| Vitest + React Testing Library (jsdom) | Unit and component tests | `npm run test:run` (single run) / `npm run test` (watch) |
| Vitest coverage (V8) | Coverage report in `coverage/` | `npm run test:coverage` |
| Oxlint | Linting; warnings fail the run | `npm run lint` / `npm run lint:fix` |
| oxfmt | Formatting of `src/` and `vite.config.ts` | `npm run format:check` / `npm run format` |
| TypeScript | Type checking | `npm run typecheck` |
| Vite build | Production build (`tsc -b`, then `vite build`) | `npm run build` |

Notes:

- Test files sit beside the code they cover (`Foo.tsx` and `Foo.test.tsx`).
  Shared helpers are in `src/test/`.
- Tests never call a real backend. `VITE_GRAPHQL_URL` is fixed in the Vitest
  config, so results do not depend on your `frontend/.env`.
- `npm run test` starts watch mode and does not exit; use `test:run` in
  scripts and before a pull request.

More detail: [`frontend/README.md`](../frontend/README.md#testing).

## Test locations

| Kind | Location |
| ---- | -------- |
| Backend unit and integration tests | `backend/tests/test_*.py` |
| Frontend component and utility tests | beside the source file, `*.test.ts(x)` |
| End-to-end tests | not automated; a manual walkthrough with three accounts is in [`pipeline-test-guide.pdf`](pipeline-test-guide.pdf) |

## Coverage

Coverage is reported but **no minimum threshold is enforced**, locally or in
CI. A drop in coverage does not fail a build.

## Continuous integration

The workflow is [`.github/workflows/ci.yml`](../.github/workflows/ci.yml),
named `CI`. It runs on pull requests targeting `develop` or `main`, and on
pushes to `develop` or `main`. It has read-only repository permissions and
needs no repository secrets. A newer run on the same pull request or branch
cancels the one still in progress.

Two independent jobs run, `Backend` and `Frontend`. The workflow fails if
either fails.

### Backend job

Ubuntu runner, Python 3.12 (pip cache), a `postgres:16` service container
with a health check. Dependencies come from `backend/requirements-dev.txt`.
Steps, in order:

1. `ruff check .`
2. `ruff format --check .`
3. `python manage.py check`
4. `python manage.py makemigrations --check --dry-run` (migration
   consistency)
5. `python manage.py migrate --noinput` (applies every migration to the CI
   PostgreSQL database)
6. `pytest --cov`

### Frontend job

Ubuntu runner, Node.js 22 (npm cache keyed on `package-lock.json`). Steps, in
order:

1. `npm ci`
2. `npm run lint`
3. `npm run format:check`
4. `npm run typecheck`
5. `npm run test:run`
6. `npm run build`

### CI as the merge validation layer

CI is the shared, reproducible check that a change is safe to merge into
`develop` or `main`. A pull request should not be merged until both jobs pass.
The CI environment itself is described in
[`environments.md`](environments.md#ci-execution-context).

Whether the checks are *required* by GitHub (branch protection) is a
repository setting, not something the workflow file controls.

### Reproducing a CI run locally

Run the commands in the job lists above from `backend/` and `frontend/`; the
[pre-PR checklist in `development.md`](development.md#checks-before-opening-a-pull-request)
is the same set. CI uses a fresh database and a clean checkout, so if a check
passes locally but fails in CI, look for a dependency missing from
`requirements*.txt` / `package-lock.json`, or a dependency on a local `.env`.
