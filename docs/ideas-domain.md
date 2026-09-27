# Ideas Domain

The `ideas` app (S2-001). It owns the platform's first business domain
beyond Identity & Access: the **problem/idea submission foundation**.

S2-001 is architecture and schema. It establishes the entities, the
lifecycle vocabulary, the visibility model, the tenancy and authorization
boundaries, and the storage boundary - and stops there. It deliberately
implements no business operation, no GraphQL operation and no UI. What is
specified but not yet built is written down here so the next sprint has a
contract rather than a guess.

For the whole-platform picture see [`architecture.md`](architecture.md).

## Purpose

The platform turns real-world problems into delivered automation:

```
PROBLEM → IDEA → VALIDATION → AUTOMATION OPPORTUNITY → REQUIREMENTS
→ PROPOSAL → DEVELOPER/TEAM → PROJECT → DEVELOPMENT → TESTING
→ DEPLOYMENT → IMPACT
```

This domain owns step two, `IDEA`, plus the `PROBLEM` it describes. Steps
three onwards belong to later sprints, and none of their vocabulary leaks
into these tables - a change to what a *validation decision* means must never
require a migration of an idea.

## Entities

```
Organization                       User
    │                                 │
    ├── Idea                         ├── authored Ideas      (ideas.ideas)
    │     ├── Category               ├── Comments           (idea_comments)
    │     ├── Comment                ├── Votes              (idea_votes)
    │     ├── Vote                   └── Attachments         (idea_attachments)
    │     └── Attachment
    │
    └── Membership
```

| Entity | Table | Notes |
| ------ | ----- | ----- |
| `Category` | `ideas_category` | Platform-wide, flat, reusable. Not tenant-scoped. |
| `Idea` | `ideas_idea` | The submission, and the tenant boundary. |
| `Comment` | `ideas_comment` | One idea, one authenticated author. Not threaded. |
| `Vote` | `ideas_vote` | One row per (user, idea). No value, no downvotes. |
| `Attachment` | `ideas_attachment` | Metadata only; the bytes live in object storage. |

`Category` is deliberately **not** organization-scoped. It is what makes two
organizations' ideas comparable to each other, which is the input a future
cross-tenant discovery or matching feature needs. An organization-scoped
category would silently make "customer support" in one company unrelated to
"customer support" in another.

Reverse accessors are named for what they are, not for symmetry:
`organization.ideas`, `category.ideas`, `idea.comments`, `user.ideas`,
`user.idea_comments`, `user.idea_votes`, `user.idea_attachments`.

## Lifecycle

| Status | Meaning | Sprint |
| ------ | ------- | ------ |
| `DRAFT` | Being written. May be incomplete. Visible per `visibility`. | S2-002 |
| `SUBMITTED` | Put forward for review. | S2-002 |
| `UNDER_REVIEW` | A reviewer is looking at it. | S2-003 |
| `CHANGES_REQUESTED` | Sent back to the author with reasons. | S2-003 |
| `REJECTED` | Not going forward. | S2-003 |
| `APPROVED` | Accepted as worth automating. | S2-003 |
| `AUTOMATION_PROPOSAL` | Handed off to the opportunity/proposal track. | S2-003 |

The vocabulary was established in S2-001 and every transition above is
implemented in S2-003, so the review workflow is behaviour rather than a
migration. What is *not* implemented is the management of it: there is no
review queue, no reviewer assignment, no reason text on a changes-requested
idea, and nothing acts on an idea once it reaches `AUTOMATION_PROPOSAL`.

### Transition matrix (implemented, S2-003)

The table is `ideas.lifecycle.TRANSITIONS`: `(from, to) -> required actor`.
It is the whole lifecycle — anything not listed is not a transition, and
`transition_idea` refuses it.

| From | To | Actor |
| ---- | -- | ----- |
| `DRAFT` | `SUBMITTED` | author |
| `SUBMITTED` | `UNDER_REVIEW` | reviewer |
| `UNDER_REVIEW` | `CHANGES_REQUESTED` | reviewer |
| `UNDER_REVIEW` | `APPROVED` | reviewer |
| `UNDER_REVIEW` | `REJECTED` | reviewer |
| `CHANGES_REQUESTED` | `SUBMITTED` | author |
| `APPROVED` | `AUTOMATION_PROPOSAL` | reviewer |

`SUBMITTED → REJECTED` is deliberately absent: a submission has to be picked
up before it can be resolved, which is what the `UNDER_REVIEW` state records.

**Nothing returns to `DRAFT`.** A draft is by definition an idea with no
`submitted_at`, so moving back to it would have to either erase an audit fact
or contradict the model's own invariant. The route back is
`CHANGES_REQUESTED`, which the author edits as a draft in spirit without
pretending it was never submitted.

**The two actors.** Before review begins the lifecycle belongs to the author,
who must still hold an active membership; from `SUBMITTED` onward it belongs
to a *reviewer* — an active member holding a **system** role in the idea's own
organization, who is **not** the author. The self-review exclusion is
structural rather than a matter of hoping two roles go to different people:
bootstrap makes everybody's own organization theirs, so an author who also
holds the `Owner` role genuinely holds it, and no role check would catch them.

**Why a system role rather than a new permission code.** A dedicated
`idea.review` code would be more precise, and is the shape to adopt if the
platform grows a custom Reviewer role that should be grantable without full
ownership. It is not added because nothing else needs a second capability
code, and a permission that only ever means "is an Owner" duplicates
`Role.is_system` and would have to be granted by hand in every organization
created before it existed. `organizations.authorization.membership_holds_system_role`
is the single function to replace if that trade changes.

`submitted_at` is set once, by the first `DRAFT → SUBMITTED`, and never
rewritten: it is an audit fact, and `created_at` (the row was written) and
`submitted_at` (somebody deliberately put it forward) are genuinely different
moments. A re-submission after `CHANGES_REQUESTED` keeps the original stamp.
The model enforces that a draft has no `submitted_at` and anything else has one,
which is why a transition writes the two together.

**Concurrency.** Every transition reads its row with `SELECT … FOR UPDATE`
inside the transaction, so two transitions of the same idea cannot both read
the same starting status and both succeed: the second waits, then re-reads a
status that no longer permits its move. Without the lock, "approve" and
"reject" fired together would both apply and the last write would silently
win.

### Why the free-text fields are blank-able

A draft is incomplete by definition - saving part-way is the point. "What
must be filled in to submit" is therefore a rule of the **transition**, owned
by the `submit_idea` service (S2-002), not of the schema. Expressing it as
`null=False` would make drafts unsavable, and as a database `CHECK` would
constrain review states this domain does not own.

## Visibility

| Visibility | Who may read the idea | Enforceable today |
| ---------- | -------------------- | ----------------- |
| `PUBLIC` | Any authenticated platform user, in any organization. | Yes |
| `ORGANIZATION` | Any active member of the idea's organization. | Yes |
| `DEPARTMENT` | Active members of the idea's organization who are also in the author's department. | **No — fails closed** |
| `PRIVATE` | The author only. | Yes |

`DEPARTMENT` fails closed in both directions. The service **refuses it as a
choice** (`ideas.services.SELECTABLE_VISIBILITIES` omits it, so the picker
cannot offer it and the API cannot store it), and the selectors **treat it as
author-only** rather than as `ORGANIZATION`. Honouring it as the nearest thing
available would mean an idea an author deliberately narrowed to their
department was readable by their whole team — the exact outcome a reserved
value exists to prevent. `ideas/selectors.py` is the one function to change
when the department tier arrives.

**One policy, two call sites, and they must agree.** `can_view_idea` is the
predicate and `_visibility_filter` is the queryset form of the same rule; both
are in `ideas/selectors.py` and a test asserts they agree for every
(actor, visibility) pair. The write path reads through
`selectors.get_idea_for_update`, the same filter with `FOR UPDATE` added, so
"you may not see it" and "you may not act on it" cannot come apart.

`visibility` defaults to `PRIVATE`, so a new submission is visible to nobody
but its author until somebody deliberately widens it. Code that forgets to
consider visibility at all therefore leaks nothing.

### DEPARTMENT is a reserved value, not a missing feature

The platform has **no Department model** (see
[`architecture.md`](architecture.md#multi-tenancy-target--organization-tier-implemented)).
The enum value is present so the vocabulary is already settled and adding the
tier is not a data migration, but nothing sets it and nothing filters on it.
Creating a Department app here would have been a feature belonging to a
different sprint, and inventing an unrelated one inside `ideas` would have
been worse.

Enforcing it will need, at minimum: a `Department` model, a
`Membership.department` (or equivalent) reference, and a rule for what happens
when the author has no department or changes it. All three are decisions for
the sprint that introduces the Department tier.

## Authorization boundary

There is **one** authorization mechanism in this platform and it is Sprint
1's. `ideas` adds no permission system and no permission codes.

| Question | Owner | Mechanism |
| -------- | ----- | --------- |
| Who is this? | `identity` | `identity.authentication.get_authenticated_user`, once per request from the access token |
| Is this user a member of that organization? | `organizations.authorization` | `get_membership` (active `Membership` row) |
| May this user act in that organization? | `organizations.authorization` | `has_permission` / `require_permission` against the membership's roles |
| Is this user the author / uploader? | `ideas` | Row comparison, e.g. `idea.author_id == user.pk` |
| May this user *read* this idea? | `ideas` | Visibility filter, in the selector layer |

Every Ideas write operation therefore has this shape:

```python
membership = authorization.require_permission(user, idea.organization, IDEA_UPDATE)
if idea.author_id != user.pk:
    raise ...
```

The chain `User → Membership → Organization → Idea` is enforced by the fact
that `Idea.organization` is **required on every row**: there is no such thing
as an idea that exists outside a tenant, so a forgotten tenant filter cannot
reach anything.

### Why there is no `ideas/permissions.py`

`organizations.authorization` answers "may this user act in this
organization", derived from a membership and the permissions on its roles -
it is act-shaped, and by design reads nothing from the request. Idea
*visibility* is a read policy about a single resource, so it belongs in the
selector layer as a filter built on top of `get_membership`, not as a second
permission system. Adding a permissions module here would create two places
where an answer could be given, and one of them would be wrong within a
sprint.

The frontend may narrow what it displays. That is presentation, never the
control: every idea read goes through a selector that filters first, and an
inaccessible idea is not "hidden", it is absent from the result set - the same
refusal Sprint 1 made for cross-tenant organization reads.

## Storage boundary

Attachment **bytes are never stored in PostgreSQL**. `Attachment` is a
pointer: `storage_key` is the object's key in an S3-compatible bucket, and
the file is served by a short-lived signed URL.

The reason is the storage tier, not taste. PostgreSQL is the transactional
tier; attachments are large, immutable blobs whose read pattern (stream a
whole file, rarely) and whose backup, replica and retention profile (every
backup, every replica, forever) are exactly what a row-oriented database is
worst at.

`storage_key` is unique, so two attachments can never point at the same
object - which would make deleting either a silent data-loss event that
nothing in the database would flag.

`Attachment` has no `organization` column. The tenant filter for an
attachment is `idea__organization_id`; a second copy of the tenant on the row
would be free to disagree with the idea it belongs to.

S2-001 establishes the boundary only. The upload flow itself - a presigned
POST from the browser straight to object storage, then the metadata row
written from the confirmed key - is later work.

## Database design

Migration: `ideas/0001_initial.py`.

### Constraints

| Constraint | Where | Why |
| ---------- | ----- | --- |
| `unique_category_name`, `unique_category_slug` | `Category` | Both are user-facing identifiers; a same-spelled category is a data-entry mistake, not two categories |
| `idea_status_is_known` | `Idea` (`CHECK`) | `choices` only covers `full_clean()`; `bulk_create`, `QuerySet.update` and a management command all bypass it |
| `idea_visibility_is_known` | `Idea` (`CHECK`) | Same |
| `unique_vote_per_user_idea` | `Vote` | A second vote must be impossible regardless of which service or adapter issued it - including two concurrent ones |
| `storage_key` unique | `Attachment` | Two rows pointing at one object would make deleting either silent data loss |

`Idea.category` is `PROTECT`, not `CASCADE`: an idea that has been submitted
and reviewed must not be erased because someone tidied up the category list.
Retirement (`is_active=False`) is the supported operation; deletion is only
for a category that was never used.

`Idea.organization` and `Comment`/`Vote`/`Attachment`'s `idea` are `CASCADE`:
a row that exists only inside a tenant cannot outlive the tenant, which is
the same rule `Membership` follows in Sprint 1.

### Indexes

Django adds an index per foreign key by default. Three of `Idea`'s four FKs
lead a composite index below, so a single-column index on them would be a
strict prefix of an existing index and is switched off - every write to an
idea would otherwise update an index PostgreSQL could never choose over the
composite one. `ideas/tests/test_models.py::TestIdeaIndexes` asserts both the
index set and this, so the two cannot silently diverge.

| Index | Columns | The query it serves |
| ----- | ------- | ------------------- |
| `ideas_org_created_idx` | `organization, created_at DESC` | The organization idea feed, newest first. The most common read. |
| `ideas_org_status_idx` | `organization, status` | A tenant-scoped board filtered by state. Needed separately: a status filter is not a prefix of the index above, so without it PostgreSQL scans every idea in the tenant. |
| `ideas_category_created_idx` | `category, created_at DESC` | Browsing one category, newest first. Leads on the category so a category page is a single index range rather than a filter over the table. |
| `ideas_vis_created_idx` | `visibility, created_at DESC` | Visibility-filtered discovery, newest first. The ordering is part of the access path, not a sort bolted on afterwards. |
| `ideas_author_created_idx` | `author, created_at DESC` | "My ideas" - drafts, submissions, history. |
| `comments_idea_created_idx` | `idea, created_at` | One idea's discussion, oldest first. The FK index cannot order the result. |
| `attachments_idea_created_idx` | `idea, created_at` | One idea's files, oldest first. |

No index on `status` or `visibility` alone: both are low-cardinality columns
that are only ever queried with a tenant or a category alongside them, and the
composites above already cover that.

## Service layer (planned, S2-002)

Business operations go in `ideas/services.py`, never inside a GraphQL
resolver - a resolver translates between the API and a domain operation, and
holding a rule in one makes the operation unreachable from the admin, a
management command, a future REST endpoint, or a test that does not go
through HTTP.

Intended operations, and nothing else until it is needed:

```python
create_idea(user, organization_id, data) -> Idea
update_idea(user, idea_id, data) -> Idea
submit_idea(user, idea_id) -> Idea          # DRAFT -> SUBMITTED
add_comment(user, idea_id, content) -> Comment
update_comment(user, comment_id, content) -> Comment
delete_comment(user, comment_id) -> None
vote_for_idea(user, idea_id) -> Vote
remove_vote(user, idea_id) -> None
```

`create_idea` must require an active membership in the target organization
(there is no idea without a tenant); `update_idea` and `submit_idea` must
require the author; `vote_for_idea` must be idempotent at the service level
and enforced at the database level, and a user may not vote on an idea they
cannot read.

**As implemented (S2-002).** `create_idea`, `update_idea` and `submit_idea`
ship, with `IdeaInput` carrying the four writable content fields and nothing
else. `add_comment`, `update_comment`, `delete_comment`, `vote_for_idea` and
`remove_vote` are still to come, and are not stubbed.

Two decisions the implementation had to make that this document did not
anticipate:

- **"What must be filled in to submit" is a real rule, and it is a threshold
  rather than a boolean.** A title, a description of *at least 20
  characters*, and a category. The description minimum is the one number in
  this domain that is a judgement call rather than a consequence of the
  schema: a blank description cannot be acted on by a reviewer, and a
  one-word description is not a problem statement. It is a module constant, so
  changing it is a visible decision.
- **A submitted idea is no longer editable, by anybody.** `update_idea` and
  `submit_idea` both refuse anything past `DRAFT`. Sprint 3 introduces
  `CHANGES_REQUESTED`, which is the route back to a draft; until it exists,
  refusing is the only answer that is not a lie about what `SUBMITTED` means.

## Selector layer (implemented, S2-002)

Reads live in `ideas/selectors.py`, separate from writes, so the visibility
and tenancy rules are applied in exactly one place:

```python
get_idea(user, idea_id) -> Idea | None          # None for anything unreadable
list_ideas(user, filters) -> QuerySet[Idea]      # already visibility-filtered
list_organization_ideas(user, organization_id, filters) -> QuerySet[Idea]
list_comments(user, idea_id) -> QuerySet[Comment]
can_view_idea(user, idea) -> bool
```

`list_ideas` and `list_organization_ideas` return an already-filtered
`QuerySet`, not a list, so a caller cannot forget the filter by forgetting to
apply it: the only way to get ideas is through a selector that applied it.
The filter is built from `get_membership`, so it is the same membership
Sprint 1 authorizes with.

## GraphQL boundary (implemented, S2-002)

S2-001 added **nothing** to the GraphQL schema. `graphql_api/schema.py` merges
one `Query`/`Mutation` per domain app, so the ideas seam was a single import
and one extra base class:

```python
# graphql_api/schema.py
from ideas.schema import Mutation as IdeasMutation
from ideas.schema import Query as IdeasQuery

class Query(IdentityQuery, OrganizationsQuery, IdeasQuery): ...
class Mutation(IdentityMutation, OrganizationsMutation, IdeasMutation): ...
```

The dependency direction stays as it is today - domain → GraphQL adapter,
never the reverse - so `ideas/schema.py` imports `ideas/selectors.py` and
`ideas/services.py` and imports nothing from `graphql_api`.

Intended operations:

| Queries | Mutations |
| ------- | --------- |
| `idea(id)` | `createIdea(input)` |
| `ideas(filters)` | `updateIdea(input)` |
| `organizationIdeas(organizationId, filters)` | `submitIdea(id)` |
| `categories` | `addComment(input)` |
| `comments(ideaId)` | `updateComment(input)` / `deleteComment(id)` |
| | `voteIdea(id)` / `removeVote(id)` |

The frontend will need `submittedAt` and `status` on the idea type to render
a draft, so both are exposed; the three review-only statuses are exposed as
enum values from the start so the frontend's union type is not wrong by
omission.

## Frontend boundary (planned, S2-002)

```
frontend/src/features/ideas/
    api/          ideasApi.ts - the documents and typed request functions
    components/   IdeaForm, IdeaList, IdeasWorkspace
```

As implemented (S2-002/S2-003), the data loading is held in the components rather
than in the `hooks/` layer sketched above: the workspace owns the mutations
and the list owns its one fetch, and neither needs a shared cache yet — the
app has no React Query and S2-002 has one screen and one list. The `pages/`,
`types/`, `utils/` and `CommentThread`/`VoteButton` parts are still to come,
and a `useIdeas` hook is the natural first extraction if a second screen ever
needs the same list.

This mirrors `features/organizations/` exactly: `api/` holds the documents and
the typed request functions, `context/`-free hooks read them, and
`components/` are presentational.

- **Routes** live in `src/app/routes.tsx`, which already reserves `/app` for
  authenticated routes and its docstring names `ideas` as a coming feature.
  Ideas routes are `/app` children so they inherit `RequireAuth`; they mount
  inside the existing `OrganizationProvider`.
- **Organization context** comes from `useOrganization()`
  (`features/organizations/context`): the active organization supplies the
  `organizationId` argument, so an ideas screen has no organization picker
  of its own and cannot be pointed at an organization the user is not in.
- **Authorization information** comes from the same context's
  `hasPermission(code)` and `activeMembership`. The frontend uses it to decide
  what to *offer* (hide "submit" on an idea you did not author). It is never
  what decides what is *visible* - the server has already filtered.
- **Visibility** is represented in the UI as a badge plus a muted card style,
  not as a client-side filter. `visibility` travels on the idea type so a
  reader can see who an idea is shared with, which is information people need
  before commenting.

## Sprint scope

| Sprint | Scope |
| ------ | ----- |
| S2-001 (this) | `ideas` app, five models, migration, admin, model tests, this document |
| S2-002 | `createIdea`/`updateIdea`/`submitIdea`, selectors, the GraphQL schema, the frontend feature module — implemented. `addComment`/`voteIdea` were scoped into S2-002 in this document and are **not** implemented |
| S3 | Review workflow: `UNDER_REVIEW`, `CHANGES_REQUESTED`, `REJECTED`, `APPROVED` |
| later | Validation, automation opportunities, requirements, proposals, developers, projects, tasks, milestones, deployment, impact, payments, AI analysis |

Not modelled here, and not to be added under this domain: review dashboards,
reviewer assignment, approval UI, automation proposals, a developer
marketplace or matching, project management, tasks, milestones, deployment,
impact analytics, payments, AI analysis, recommendation engines, or
subscriptions. `ideas/tests/test_models.py::TestAppBoundary` asserts the app's
model set, so crossing a sprint boundary here fails a test.
