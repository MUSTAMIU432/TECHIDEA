# Automation lifecycle (Sprint 4)

> **Changed after Sprint 4.** The proposal is now written by the platform review team, released by
> an admin and read by the owner *before* the go-ahead, and the go-ahead opens the opportunity
> ready for assignment. The early opportunity stages and the delivery-team proposal described
> below were replaced; see `collaboration.md`, "The proposal, and the go-ahead after it".

What happens to an idea after the owner's go-ahead: a managed, traceable path from
`READY_FOR_IMPLEMENTATION` to a measured result. This document records what is
**built**, what is **decided**, and what is **still to build** - it is updated with
each phase and does not describe anything that does not exist.

```
IDEA -> OWNER GO-AHEAD -> READY_FOR_IMPLEMENTATION
     -> OPPORTUNITY -> REQUIREMENTS -> SOLUTION -> PROPOSAL
     -> ASSIGNMENT -> PROJECT -> DEVELOPMENT -> TESTING -> UAT
     -> DEPLOYMENT -> IMPACT -> COMPLETED
```

## Status of the build

| Stage | State |
| --- | --- |
| Opportunity, requirements, solution, audit trail | **Built and tested** (backend + GraphQL) |
| Proposal (draft -> ready -> submitted -> accepted / changes requested / rejected) | **Built and tested** |
| Developer queue, assignment (developer or team) | **Built and tested** |
| Project, milestones, tasks, progress from real data | **Built and tested** |
| Testing, UAT (separate rounds, history kept), deployment, verification | **Built and tested** |
| Impact (estimated vs measured, calculated only from real values), completion | **Built and tested** |
| Notifications (in-app + email through the existing system) | **Built** (22 kinds) |
| Frontend: Automation area (opportunities, developer queue, projects), opportunity and project pages, idea -> opportunity panel | **Built and tested** |
| Admin Console inspection (read-only) | **Built and tested** |
| Push notifications | Not built: no push infrastructure exists (see below) |

Backend tests: `backend/automation/tests` (79), including
`test_api_journey.py`, which drives the whole lifecycle - opportunity to completed,
measured project - through the real GraphQL API.

## Architecture (`backend/automation`)

- **`models.py`** - `AutomationOpportunity`, `Requirement`, `SolutionScope`,
  `OpportunityEvent`.
- **`authorization.py`** - who may do what.
- **`services.py`** - every business operation; the only writer.
- **`schema.py`** - GraphQL, explicit actions only.

### Entry gate and duplicates

An opportunity can be created only from an idea whose status is
`READY_FOR_IMPLEMENTATION`; every other status is refused by the service, under a
row lock on the idea. One live opportunity per idea is enforced three ways: the
lock, a check in the service, and a **partial unique constraint**
(`opportunity_one_live_per_idea`, every status except `cancelled`). Cancelling an
opportunity frees the idea to start again.

### Ownership context is copied, never recomputed

`submission_context`, `organization`, `team` and `owner` are copied from the idea
when the opportunity is created and are never writable afterwards. A check
constraint (`opportunity_context_matches_tenant`) guarantees the columns agree
with the context. The idea link is `PROTECT`.

### Lifecycle

`draft -> discovery -> requirements -> solution_design -> proposal`, each edge
owned by one named action (`start_discovery`, `start_requirements`,
`start_solution_design`, `start_proposal`), plus `cancel`. There is no
`updateStatus`. Guards: solution design needs at least one non-rejected
requirement; a proposal needs a written solution summary. Statuses from
`ready_for_assignment` onward exist in the vocabulary but have **no callable path
yet** - they arrive with the proposal and assignment phases.

### Authorization

- **Read:** exactly when the caller may read the source idea
  (`ideas.selectors.can_view_idea`), so there is no second visibility rule. A
  delivery manager reads all opportunities.
- **Owner side** (the idea's author; for a team idea, its active members) may open
  an opportunity and define requirements. Owning the problem does **not** grant the
  solution, proposal or anything after it.
- **Delivery managers** hold the platform permission
  `automation.manage_delivery` (a platform `auth.Permission`, declared on
  `OpportunityEvent`; never an organization role). They run the lifecycle and edit
  the solution.
- Every refusal about an opportunity the caller cannot read is the same message,
  so an id cannot be probed.

### Audit

`OpportunityEvent` is append-only (`save`/`delete` refuse) and written in the same
transaction as the change. See the model docstring for why this is its own table
rather than a reuse of `ideas.IdeaTransition`.

### Notifications

Two new kinds: `automation.opportunity_created` and `automation.requirements_ready`
(migration `notifications/0003`). They are delivered through the existing
`notifications.services.deliver` and link to the source idea.

## Architecture conflicts found (and how they were handled)

- **There is no push-notification infrastructure** in this codebase (no device
  tokens, provider or delivery worker); the notification system is in-app plus
  email. Sprint 4 does **not** fake one. Push, if wanted, is a separate piece of
  work that this lifecycle's notification kinds are ready to feed.
- **There is no Celery/Redis**; background work is `transaction.on_commit`. The
  lifecycle follows that.
- **There is no general audit system**; the existing trails are
  `ideas.IdeaTransition` and `administration.AdminAuditEntry`, each narrow by
  design. Sprint 4 follows the same pattern with `OpportunityEvent`.
- **No developer/team-of-developers model exists**, which assignment (a later
  phase) needs. The smallest safe evolution is a minimal "delivery team" concept
  built then, not a marketplace.

## GraphQL (Phase 1)

Queries: `automationOpportunities`, `automationOpportunity`,
`automationOpportunityForIdea`, `requirements`, `opportunitySolution`,
`opportunityActivity`. Mutations: `createAutomationOpportunity`,
`updateAutomationOpportunity`, `startOpportunityDiscovery`,
`startOpportunityRequirements`, `startOpportunitySolutionDesign`,
`startOpportunityProposal`, `cancelAutomationOpportunity`, `createRequirement`,
`updateRequirement`, `updateOpportunitySolution`. Refusals are
`success: false` payloads with the field they belong to.

## Frontend (`frontend/src/features/automation`)

Routes under `/app/automation`: `opportunities`, `opportunities/:id`, `queue`,
`projects`, `projects/:id`; an **Automation** tab in the app header, and
`/app/admin/automation` in the console (read-only; the server returns nothing to an
administrator who cannot inspect idea content).

- **Opportunity page** - tabs: Overview, Requirements, Solution, Proposal,
  Assignment, Project, Activity. The header always shows the idea type, owner and a
  link back to the original idea.
- **Project page** - the pipeline (drawn from the project's own records), and tabs:
  Overview, Tasks & milestones, Testing, UAT, Deployment, Impact, Activity.
- **Idea page** - "Create Automation Opportunity" for a ready idea, or links to the
  opportunity and project once they exist.
- Every screen has loading, empty, error, unavailable and refusal states; refusals
  show the server's own words next to the field they name. Buttons are drawn from the
  `capabilities` the server returns, which are for display only.

## Developer-side GraphQL additions

`assignableAssignees` (delivery managers only), `adminAutomationOpportunities` and
`adminAutomationProjects` (console only), plus the proposal, project, testing, UAT,
deployment and impact queries and mutations - see `automation/delivery_schema.py`.
