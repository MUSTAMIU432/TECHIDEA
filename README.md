# Automation Platform

Automation Platform is a system for turning real-world problems into
delivered automation:

```
PROBLEM → IDEA → VALIDATION → AUTOMATION OPPORTUNITY → REQUIREMENTS
→ PROPOSAL → DEVELOPER/TEAM → PROJECT → DEVELOPMENT → TESTING
→ DEPLOYMENT → IMPACT
```

Individuals, universities, private institutions, government institutions,
organizations, departments, developers, and development teams can submit
problems and ideas, have them validated into automation opportunities, and
see them through to delivered, measurable impact.

## Project Status

This repository is in **Sprint 0 — Foundation**, and the foundation is
substantially established:

- Django backend
- React + TypeScript + Vite frontend
- PostgreSQL
- GraphQL (foundation schema only)
- Environment configuration
- Testing (backend and frontend)
- Code quality tooling
- GitHub Actions CI (validation only, no deployment)

**Business functionality is not yet implemented.** There are no business
domains, and identity/authentication begins in Sprint 1. The security
baseline (S0-011) and the branch audit (S0-012) are still to come. See
[`docs/architecture.md`](docs/architecture.md) for current vs. target
architecture, and [`CHANGELOG.md`](CHANGELOG.md) for what has landed.

## Target Architecture (summary)

- **Frontend:** React + TypeScript + Vite + Tailwind CSS, GraphQL client
- **Backend:** Django, modular monolith organized by business domain
- **API:** GraphQL primary, REST for infrastructure-only concerns
- **Database:** PostgreSQL
- **Async processing:** Redis + Celery (planned)
- **AI:** centralized gateway/service abstraction (planned)
- **Storage:** S3-compatible object storage (planned)

Full details: [`docs/architecture.md`](docs/architecture.md).

## Repository Structure

```
automation-platform/
├── backend/           # Django project (config, graphql_api, tests)
├── frontend/          # React + TypeScript + Vite app
├── docs/              # Architecture, environments, development, testing, domain docs, Git workflow
├── infrastructure/    # Placeholder: no infrastructure implemented yet
├── scripts/           # Placeholder: no scripts yet
├── .github/           # CI workflow, issue/PR templates
├── .env.example       # Index of the per-app env templates
└── ...                # README, CONTRIBUTING, SECURITY, CHANGELOG, LICENSE
```

A per-file breakdown is in [`docs/architecture.md`](docs/architecture.md#repository-structure).

## Getting Started

The backend and frontend run locally; there is no business functionality to
use yet.

- [`docs/development.md`](docs/development.md) — prerequisites, setup and
  running the backend and frontend
- [`docs/testing.md`](docs/testing.md) — tests, linting, formatting and CI
- [`docs/git-workflow.md`](docs/git-workflow.md) — branches and pull requests
- [`docs/environments.md`](docs/environments.md) — environment variables and
  environments
- [`docs/ideas-domain.md`](docs/ideas-domain.md) — the ideas & problem
  submission domain: entities, lifecycle, visibility and boundaries

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to contribute and
[`docs/git-workflow.md`](docs/git-workflow.md) for the branching model and PR
process.

## Security

See [SECURITY.md](SECURITY.md) for the security policy and how to report
vulnerabilities.

## License

Proprietary — All Rights Reserved. See [LICENSE](LICENSE).
