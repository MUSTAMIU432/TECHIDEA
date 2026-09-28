# Review & Validation Domain

Architecture for Sprint 3 (S3-001). **Nothing described here is implemented
yet.** This document records what the code does today, the smallest domain
model that turns the existing lifecycle into an auditable review workflow, and
the product decisions that must be made before implementation starts.

Companion to [`ideas-domain.md`](ideas-domain.md), which owns the lifecycle
this domain builds on. Where the two disagree about what exists, the code is
the authority and this document was checked against it on the
`feature/reviews` branch at `45db994`.

## Contents

1. [What exists today](#1-what-exists-today)
2. [Domain overview](#2-domain-overview)
3. [Entities](#3-entities)
4. [Relationships](#4-relationships)
5. [State and decision model](#5-state-and-decision-model)
6. [Reviewer authorization](#6-reviewer-authorization)
7. [Assignment](#7-assignment)
8. [Review history](#8-review-history)
9. [Validation criteria](#9-validation-criteria)
10. [Changes-requested flow](#10-changes-requested-flow)
11. [Approval flow](#11-approval-flow)
12. [Security model](#12-security-model)
13. [GraphQL boundary](#13-graphql-boundary)
14. [Frontend boundary](#14-frontend-boundary)
15. [Notifications and audit](#15-notifications-and-audit)
16. [Test strategy](#16-test-strategy)
17. [Database, indexes and constraints](#17-database-indexes-and-constraints)
18. [Integration points](#18-integration-points)
19. [Out of scope](#19-out-of-scope)
20. [Open product decisions](#20-open-product-decisions)
21. [Implementation plan](#21-implementation-plan)

---

## 1. What exists today

### 1.1 Reviewer authorization

`ideas.lifecycle.is_reviewer` and `_actor_may`: a reviewer is an **active
user** with an **active membership** of the idea's organization, who holds a
**system role** in that same organization
(`organizations.authorization.membership_holds_system_role`), and who is
**not the idea's author**.

- The only system role is `Owner`, created at organization bootstrap
  (`organizations.services.create_organization_for_user`).
- `membership_holds_system_role` filters on
  `role__organization_id = membership.organization_id`, so a role held in
  organization A never authorizes anything in B.
- The author exclusion is structural: an Owner who wrote the idea is refused.
- There is no `idea.review` permission code. The docstring on
  `membership_holds_system_role` names it as the one function to replace if a
  grantable Reviewer capability is needed.

**Finding that affects the whole sprint.** The only code path that creates a
`Membership` is organization bootstrap, which makes the creator its sole
member and Owner. There is no invitation or add-member service or mutation.
Roles likewise exist only through bootstrap and Django admin: there is no
role-creation service. Consequently, in the product as shipped, **every
organization has exactly one member, who is the Owner, and that member can
never review their own ideas**. The review workflow is reachable today only
through Django admin or tests. See [D-1](#20-open-product-decisions).

### 1.2 Lifecycle transition rules

`ideas.lifecycle.TRANSITIONS` is the whole lifecycle, and
`transition_idea` is the only way to change `Idea.status`:

| From | To | Actor |
| ---- | -- | ----- |
| `DRAFT` | `SUBMITTED` | author |
| `SUBMITTED` | `UNDER_REVIEW` | reviewer |
| `UNDER_REVIEW` | `CHANGES_REQUESTED` | reviewer |
| `UNDER_REVIEW` | `APPROVED` | reviewer |
| `UNDER_REVIEW` | `REJECTED` | reviewer |
| `CHANGES_REQUESTED` | `SUBMITTED` | author |
| `APPROVED` | `AUTOMATION_PROPOSAL` | reviewer |

`transition_idea` locks the idea row (`select_for_update`), re-checks
membership, checks the actor before the pair, and validates content on every
move to `SUBMITTED`. `submitted_at` is written once, on the first submission.

What the transitions do **not** record: who made them, when (other than the
first submission), or why. Any eligible reviewer can make any reviewer move;
nothing ties the reviewer who started a review to the one who resolves it.

### 1.3 Organization boundaries

`Organization` is the tenant. `Membership(user, organization, status)` with
`ACTIVE`/`INACTIVE`; `Role` is per organization; `MembershipRole` enforces
role and membership belong to the same organization in `clean()`.
`organizations.authorization` is the single definition of "active member"
(`get_membership`, `active_organization_ids`) and of permission checks
(`require_permission`, `membership_has_permission`). Every Ideas write
re-checks membership at write time rather than trusting a read.

### 1.4 Visibility rules

`ideas.selectors.can_view_idea` / `_visibility_filter`:

| Visibility | Readable by |
| ---------- | ----------- |
| `PUBLIC` | any active user on the platform, any organization |
| `ORGANIZATION` | active members of the idea's organization |
| `DEPARTMENT` | author only (no department tier exists) |
| `PRIVATE` | author only |

Visibility and status are independent. `transition_idea` resolves the idea
through the visibility filter, so **a reviewer cannot act on a `PRIVATE` or
`DEPARTMENT` idea**: a submitted `PRIVATE` idea stays in `SUBMITTED` forever
(documented in S2-008). See [D-6](#20-open-product-decisions).

### 1.5 Idea fields useful for validation

`title`, `description` (≥ 20 chars to submit), `category` (required to
submit), `visibility`, `status`, `submitted_at`, `author`, `organization`.

`problem_statement`, `proposed_solution` and `expected_benefit` exist on the
model but are **not** writable through `IdeaInput` and **not** exposed on
`IdeaType`. They are blank on every idea created through the API. Review
criteria must not depend on them until they are wired (not an S3 task unless
[D-8](#20-open-product-decisions) says otherwise).

### 1.6 Comments and attachments

- **Comments** (S2-005): any reader may comment while the discussion is open
  (closed only in `REJECTED`). Comments are public to every reader of the
  idea, so they are **not** a suitable carrier for review feedback, which is
  addressed to the author and must be tied to a decision.
- **Attachments** (S2-007): readable by anyone who can read the idea;
  uploaded and deleted by the author only, in any status. They are the
  author's supporting evidence for a review and are reused as-is.
- **Votes** (S2-006): an engagement signal only. They are not a review input.

### 1.7 Notification infrastructure

None. `identity/email.py` is a transport for two transactional emails
(password reset, activation) over `django.core.mail`, synchronous, logging
failures rather than raising. There is no Celery, no notification model and
no in-app notification surface (`docs/architecture.md`: asynchronous
processing is "not yet implemented").

### 1.8 Audit infrastructure

None. There is no audit model or audit app, and no structured audit logging.
The only audit-like facts are `Idea.submitted_at` and `created_at`/
`updated_at` timestamps.

### 1.9 Frontend structures

`frontend/src/features/ideas/` with `api/ideasApi.ts` (typed request
functions over the shared GraphQL client), `hooks/` (server-state hooks that
discard superseded responses, per S2-008), `components/IdeasWorkspace.tsx`
(list, form, discussion, attachments and votes in one workspace), and
`utils/lifecycle.ts` (labels only; legality comes from
`Idea.availableTransitions`). The reviewer moves are already rendered today,
as generic transition buttons (`Start review`, `Request changes`, `Approve`,
`Reject`, `Hand off`). Routing is `app/routes.tsx` with `/app/ideas`.

### 1.10 GraphQL conventions

- One `strawberry.Schema`; each domain exposes `Query`/`Mutation` classes
  merged in `graphql_api/schema.py`, and the domain never imports
  `graphql_api`.
- Types have a `from_model` static method; inputs are `@strawberry.input`.
- Mutations return a payload `(success, message, field, <object>)`; services
  raise a domain error subclassing `AuthorizationError` with
  `(message, field, reason)`.
- A missing or unreadable object is returned as `null` / refused identically
  to a nonexistent one, so no operation is an existence oracle.
- Per-viewer capabilities are computed fields (`availableTransitions`,
  `discussionOpen`), a convenience rather than a control.
- Enums are built from the model's `TextChoices`.
- Paging is offset-based and bounded (`ideas.pagination`).
- Users are exposed as ids, never as nested `UserType`, on anything a
  `PUBLIC` idea can reach.

---

## 2. Domain overview

```
Idea (SUBMITTED)
  │  reviewer claims it from the organization's queue
  ▼
Review  (round n, reviewer, in progress)        Idea → UNDER_REVIEW
  │  reviewer records criteria + feedback + decision, atomically
  ▼
Review  (completed, immutable)                  Idea → CHANGES_REQUESTED | APPROVED | REJECTED
  │
  ├─ CHANGES_REQUESTED: author edits, resubmits → SUBMITTED → Review round n+1
  ├─ REJECTED: terminal
  └─ APPROVED: ready for the opportunity track (→ AUTOMATION_PROPOSAL hand-off)
```

A new `reviews` Django app owns **the record of review**, which is who
reviewed, what they assessed, what they decided and what they told the
author. It does **not** own the lifecycle. `ideas.lifecycle` remains the only
code that changes `Idea.status`; the reviews service calls it inside its own
transaction so that a review and the status change it causes commit together
or not at all.

Dependency direction: `reviews → ideas → organizations → identity`. Ideas
never imports reviews.

---

## 3. Entities

The brief lists six candidates. Two are needed.

| Candidate | Verdict | Why |
| --------- | ------- | --- |
| **Review** | **Required** | The auditable record: one row per review round of one idea. Carries the reviewer, the decision, the feedback and the timestamps. Nothing in the codebase records these today. |
| ReviewDecision | Field on `Review` | A decision is a property of exactly one review, made once. A separate table would be a 1:1 join that adds nothing and makes "a review with two decisions" representable. |
| ReviewFeedback | Field on `Review` | One feedback text per decision, written by the reviewer who decides. Discussion after the decision belongs to the existing comments. |
| **ReviewCriterionAssessment** | **Required** (if [D-4](#20-open-product-decisions) confirms criteria) | One row per (review, criterion): a categorical rating plus an optional note. A child table rather than JSON so the database can enforce one assessment per criterion and a known rating value, and so criteria can be queried. |
| ReviewCriterion | Code, not a table | The criteria are a fixed platform vocabulary, like `Idea.Status`: a `TextChoices` enum with a CHECK constraint. A table is only justified if organizations define their own criteria ([D-4](#20-open-product-decisions)). |
| ReviewAssignment | Not needed | Queue-based self-claim is the existing `SUBMITTED → UNDER_REVIEW` move; the claim **is** `Review.reviewer`. See [§7](#7-assignment). |
| ReviewHistory | Not needed | Review rows are append-only and numbered by round; the set of rows for an idea is its history. See [§8](#8-review-history). |

### 3.1 `Review`

| Field | Type | Why |
| ----- | ---- | --- |
| `idea` | FK `ideas.Idea`, `CASCADE` | The review's subject **and its tenant**. There is deliberately no `organization` FK: a review's tenant is its idea's, and a second copy could disagree. |
| `reviewer` | FK `User`, `PROTECT` | Who is accountable. `PROTECT` because an audit record must not vanish with an account; users are deactivated, not deleted, so this never blocks a supported operation. |
| `round` | `PositiveSmallIntegerField` | 1 for the first review, n+1 after each resubmission. Orders history without relying on timestamps, and lets the UI say "second review". |
| `decision` | `CharField`, `ReviewDecision` choices, nullable | `CHANGES_REQUESTED`, `APPROVED`, `REJECTED`. Null while the review is in progress. The values are the target idea statuses, 1:1. |
| `feedback` | `TextField`, blank | The reviewer's message to the author. Required for some decisions ([D-3](#20-open-product-decisions)). |
| `submission_snapshot` | `JSONField` | Title, description and category of the idea **as it was when the review started**. Required once S3-005 makes content editable in `CHANGES_REQUESTED`: without it a completed review points at text that has since changed, and its feedback no longer makes sense. |
| `created_at` | `DateTimeField(auto_now_add)` | When the reviewer claimed the idea (the `SUBMITTED → UNDER_REVIEW` moment). |
| `updated_at` | `DateTimeField(auto_now)` | Convention; only changes before completion. |
| `completed_at` | `DateTimeField`, nullable | When the decision was recorded. Null iff `decision` is null. |

Not added, and why:

- **`status` field.** In-progress vs completed is fully determined by
  `completed_at`; a separate status would be a second source of the same
  fact. If [D-2](#20-open-product-decisions) adds a released/abandoned
  outcome, it becomes a decision value, not a status.
- **Numeric score.** See [§9](#9-validation-criteria).
- **Private reviewer notes.** Not requested by anything in the product
  definition ([D-9](#20-open-product-decisions)).
- **`organization`.** See above: reached through `idea`.

### 3.2 `ReviewCriterionAssessment`

| Field | Type |
| ----- | ---- |
| `review` | FK `Review`, `CASCADE` |
| `criterion` | `CharField`, `ReviewCriterion` choices |
| `rating` | `CharField`, `CriterionRating` choices: `MEETS`, `PARTIALLY_MEETS`, `DOES_NOT_MEET`, `NOT_APPLICABLE` |
| `note` | `TextField`, blank |

Unique `(review, criterion)`. Written only together with the decision (see
[§5](#5-state-and-decision-model)), so it inherits the review's immutability.

---

## 4. Relationships

```
Organization 1──* Membership *──1 User
     │                │
     │                └──* MembershipRole *──1 Role (per organization)
     │
     └──* Idea *──1 User (author)
            │
            ├──* Comment / Vote / Attachment        (S2, unchanged)
            │
            └──* Review *──1 User (reviewer)       (S3)
                   │
                   └──* ReviewCriterionAssessment  (S3)
```

- `Review.idea` gives each review exactly one tenant, the idea's
  organization.
- `Review.reviewer` must hold, at claim time and at decision time, reviewer
  eligibility **in the idea's organization** ([§6](#6-reviewer-authorization)).
- At most one **in-progress** review per idea (partial unique constraint,
  [§17](#17-database-indexes-and-constraints)).

---

## 5. State and decision model

The Review domain adds **no idea status and no lifecycle pair**. It attaches a
record to four existing moves and takes ownership of how they are made:

| Idea move | Made by | Review effect |
| --------- | ------- | ------------- |
| `DRAFT → SUBMITTED` | author, `submitIdea` | none |
| `SUBMITTED → UNDER_REVIEW` | reviewer, **`startReview`** | creates `Review(round=n, reviewer, submission_snapshot)` |
| `UNDER_REVIEW → CHANGES_REQUESTED` / `APPROVED` / `REJECTED` | **the review's reviewer**, **`completeReview`** | sets `decision`, `feedback`, assessments, `completed_at` |
| `CHANGES_REQUESTED → SUBMITTED` | author, `submitIdea` | none; the next `startReview` opens round n+1 |
| `APPROVED → AUTOMATION_PROPOSAL` | see [§11](#11-approval-flow) | none |

A review therefore has two states, **in progress** (`completed_at` null) and
**completed**. There is no separate "draft review" state on the server
([§13](#13-graphql-boundary)).

### 5.1 Closing the bypass

Today `transitionIdea` lets a reviewer move an idea to `APPROVED` with no
record. Once reviews exist, that path must close, or the audit trail is
optional. S3-004 changes `ideas.lifecycle` as follows (an Ideas change, but
the one this domain requires):

- Reviewer-owned pairs (`SUBMITTED → UNDER_REVIEW`,
  `UNDER_REVIEW → CHANGES_REQUESTED|APPROVED|REJECTED`) are refused through
  the public `transition_idea` / `transitionIdea` path.
- `ideas.lifecycle` exposes one internal entry point, for example
  `apply_review_transition(idea, to_status, actor)`, that runs the same actor
  and matrix checks on an **already locked** idea without opening its own
  transaction. The reviews service is its only caller.
- `availableTransitions` stops listing the review-owned moves; the UI gets
  them from review-specific fields instead ([§13](#13-graphql-boundary)).

Ideas does not import reviews. The lifecycle keeps its single table. Reviews
decides **whether a review permits** the move, and lifecycle decides
**whether the move exists**.

### 5.2 Decision rules

| Question | Answer | Source |
| -------- | ------ | ------ |
| Who may create a review (claim)? | An eligible reviewer ([§6](#6-reviewer-authorization)) who can read the idea, on an idea in `SUBMITTED`. | Existing lifecycle actor rule |
| Who may record the decision? | The review's own reviewer, still eligible at decision time. | **Recommendation, [D-2](#20-open-product-decisions)** (today any reviewer may) |
| Can a decision be edited? | No. `completeReview` writes decision, feedback, assessments and `completed_at` in one transaction, and nothing updates a completed review. | Architecture (audit requirement in the brief) |
| Are completed reviews immutable? | Yes: service layer (no update path), model `save()` refusing changes once `completed_at` was set, and read-only admin. | Architecture |
| Can multiple reviewers review the same idea? | Sequentially, one per round: yes. Concurrently: no, one in-progress review per idea. | **Recommendation, [D-5](#20-open-product-decisions)** |
| Is one approval sufficient? | Yes, one completed review resolves `UNDER_REVIEW`. | **Recommendation, [D-5](#20-open-product-decisions)** |
| What if reviewers disagree? | Cannot arise under single-reviewer rounds. A later round supersedes an earlier one; both remain in history. | Follows from D-5 |
| Are multiple reviews required? | No. | **[D-5](#20-open-product-decisions)** |

The existing lifecycle has a single `UNDER_REVIEW` state resolved by a single
move, which fits one reviewer per round without any new state. A panel or
quorum model needs either a new state or an aggregation rule, which is a
product change. The model does not preclude it: dropping the
one-open-review constraint and adding an aggregation step is additive.

### 5.3 A reviewer who stops being eligible mid-review

If the claimant leaves the organization, loses the role, or is deactivated,
the idea would sit in `UNDER_REVIEW` with nobody able to resolve it. The
current lifecycle has no way back to `SUBMITTED`. This needs a product answer
([D-2](#20-open-product-decisions)). The recommended answer adds no lifecycle
pair: another eligible reviewer may **take over** an in-progress review whose
reviewer is no longer eligible. The old row is completed as `WITHDRAWN`, a
non-lifecycle decision value that records who released it and when, and a
new round opens with the new reviewer. The idea stays `UNDER_REVIEW`
throughout.

---

## 6. Reviewer authorization

### Options

| | Mechanism | Consequence |
| - | --------- | ----------- |
| **A** | Keep "system role" (`Owner`) | No schema or data change. But only Owners review, reviewing implies full ownership, and with one member per organization ([§1.1](#11-reviewer-authorization)) nobody can review anything. |
| **B** | Permission code `idea.review` on roles | Fits the existing RBAC exactly: `Permission` → `RolePermission` → `Role` → `MembershipRole` → `Membership`, checked through `organizations.authorization.membership_has_permission`, organization-scoped by construction. Reviewing becomes grantable without ownership. |
| C | Anything else (per-idea ACL, per-category reviewers, global staff flag) | A second authorization system beside `organizations.authorization`. A global flag also breaks tenant scoping. Rejected. |

### Recommendation: B, compatible with A

1. Add `IDEA_REVIEW = 'idea.review'` to the permission set in
   `organizations.services`, and grant it to the `Owner` role at bootstrap.
2. A data migration grants it to every existing `Owner` role, so **every
   Owner who can review today can still review**. No behaviour change for
   current users or current tests.
3. Provision a non-system **`Reviewer`** role per organization (bootstrap +
   migration) carrying only `idea.review` and `organization.view`, so an Owner
   can grant reviewing through the existing `assignRoleToMembership` without
   granting ownership.
4. Replace `membership_holds_system_role` in `ideas.lifecycle._actor_may` /
   `is_reviewer` with `membership_has_permission(membership, IDEA_REVIEW)`,
   as the function's own docstring anticipates. The author exclusion stays
   where it is, in lifecycle, independent of roles.
5. Expose one predicate, `reviews.eligibility.can_review(user, idea)`:
   `can_view_idea` **and** `is_reviewer`. Every review read and write goes
   through it.

**Organization scoping** is inherited: `membership_has_permission` filters
`role__organization_id = membership.organization_id`, and the membership is
looked up for the **idea's** organization. A reviewer role in A authorizes
nothing in B.

**Author ≠ reviewer** stays structural: an author is refused before any role
is consulted.

**The one-member problem** is not solved by B alone: someone must be able to
join an organization. Membership invitation belongs to the Organizations
domain ([D-1](#20-open-product-decisions)).

---

## 7. Assignment

| Model | Needs | Consequences |
| ----- | ----- | ------------ |
| **1. Queue self-claim** | Nothing new. `SUBMITTED → UNDER_REVIEW` already is a claim; `Review.reviewer` records it. | Smallest. Race between two claimants is handled by the row lock plus the one-open-review constraint: the second is refused. No workload balancing. |
| 2. Explicit assignment | `ReviewAssignment` (idea, assignee, assigned_by), an "assign reviewers" permission, assignment UI, reassignment and unassignment rules, and notification of the assignee. | Adds a pre-`UNDER_REVIEW` sub-state that the lifecycle does not have. Worth it only with many reviewers or specialist routing. |
| 3. Automatic assignment | Everything in 2, plus a routing policy (round robin, load, category) and, realistically, background jobs. | Largest. Requires product rules nobody has specified. |
| 4. Hybrid | 1 + 2 | The cost of 2, plus the interaction rules between claiming and assigning. |

**Recommendation: queue-based self-claim for Sprint 3; no assignment
entity.** Nothing in the product definition calls for routing, and with the
membership limits in [§1.1](#11-reviewer-authorization) a typical
organization will have very few reviewers. Explicit assignment can be added
later without migrating `Review`: an assignment row would precede the claim
and constrain who may make it.

---

## 8. Review history

- Rows are **append-only**. `startReview` inserts; `completeReview` sets the
  decision once; nothing deletes a review (no delete service, no delete
  mutation, admin delete disabled). The only removal is cascade from the
  idea's organization, which removes the whole tenant.
- `round` increments per idea: the idea's history is
  `Review.objects.filter(idea=idea).order_by('round')`.
- `submission_snapshot` preserves what each round actually reviewed, so
  editing content after `CHANGES_REQUESTED` never rewrites the meaning of an
  earlier review.
- A resubmission does not touch any existing review.

---

## 9. Validation criteria

No numeric scoring: nothing in the product consumes a number. A score would
invite averaging across reviewers and ranking across ideas, and both are
product features nobody has asked for. Categorical ratings plus written notes
are enough for a decision and are what an author can act on.

Proposed fixed criteria (`ReviewCriterion`), covering the areas in the brief
that current idea content can support:

| Code | Question for the reviewer |
| ---- | ------------------------- |
| `PROBLEM_CLARITY` | Is the problem clearly described? |
| `AUTOMATION_SUITABILITY` | Is this a problem automation can address? |
| `FEASIBILITY` | Is it feasible with reasonable effort? |
| `EXPECTED_BENEFIT` | Is the expected benefit credible and worth the effort? |
| `EVIDENCE` | Is it supported by enough evidence (description, attachments)? |

`impact` and `technical complexity` are folded into `EXPECTED_BENEFIT` and
`FEASIBILITY` to keep the form short. Adding either later is an enum value
and a CHECK constraint change, not a new table. Whether ratings are required
before a decision, and whether any rating forces a decision, are
[D-4](#20-open-product-decisions). No AI assessment of any kind.

---

## 10. Changes-requested flow

```
Review round n completed: decision = CHANGES_REQUESTED, feedback required
        │                                  Idea: UNDER_REVIEW → CHANGES_REQUESTED
        ▼
Author reads feedback (review history on their idea)
        │
        ▼
Author edits content          ← NEW in S3-005: update_idea accepts CHANGES_REQUESTED
Author adds evidence          ← already possible (attachments in any status)
        │
        ▼
Author resubmits (submitIdea) ← existing CHANGES_REQUESTED → SUBMITTED, re-validated
        │
        ▼
Idea back in the queue → startReview → Review round n+1 (new row, new snapshot)
```

What Sprint 3 must add (S3-005). None of it is done in this task:

1. **Content editing in `CHANGES_REQUESTED`.** Widen the status check in
   `ideas.services._load_owned_draft` from `DRAFT` to
   `{DRAFT, CHANGES_REQUESTED}`. Rename it as appropriate and keep its
   single-message refusal.
2. **Visibility is not editable after submission** (recommended,
   [D-6](#20-open-product-decisions)): in `CHANGES_REQUESTED`, `update_idea`
   ignores or refuses `visibility`, so an author cannot hide an idea from its
   own reviewers mid-review.
3. **Feedback visible to the author**: `ideaReviews` returns completed
   reviews to the author ([§13](#13-graphql-boundary)).
4. **Frontend**: enable the form for `CHANGES_REQUESTED`, show the latest
   feedback above it, and correct the `CHANGES_REQUESTED` copy in
   `utils/lifecycle.ts`, which currently says content cannot be edited.
5. **Docs**: `ideas-domain.md`'s "What the author can change while changes
   are requested" section.

`submitted_at` keeps its write-once meaning. Resubmission times are recorded
by the audit trail ([§15](#15-notifications-and-audit)), not by rewriting it.

---

## 11. Approval flow

Options for `APPROVED → AUTOMATION_PROPOSAL`:

| Option | Assessment |
| ------ | ---------- |
| Create an `AutomationOpportunity` entity | Belongs to the Opportunities domain (`docs/architecture.md` lists `opportunities` separately). Creating it here would give Reviews ownership of the next domain's vocabulary. |
| Create a proposal placeholder | A row with no behaviour behind it, and one the proposals domain would have to migrate or adopt. |
| **Expose approved ideas as ready** | **Recommended.** An approved idea is identifiable by status (`ideas(filters: {status: APPROVED})` already works) and carries its approving review. The next domain reads that; Reviews writes nothing for it. |

Recommendation for Sprint 3:

- `completeReview(decision: APPROVED)` moves the idea to `APPROVED` and stops.
- `APPROVED → AUTOMATION_PROPOSAL` stays the existing reviewer transition,
  still available through `transitionIdea`. It is not review-owned, since no
  review is open. It is labelled "Hand off", and nothing acts on the
  resulting state until the Opportunities domain exists. Whether Sprint 3
  offers it in the UI at all is [D-7](#20-open-product-decisions).
- The hand-off, like every transition, is recorded in the audit trail.

The boundary: Reviews ends at "this idea was approved, by whom, when, and
why". Everything after that belongs to Opportunities and Proposals.

---

## 12. Security model

Every rule below is enforced in `reviews/services.py` / `reviews/selectors.py`
through `organizations.authorization` and `ideas.selectors`. The client is
never the control.

**Every review is reached through an authorized idea.** A review selector
first resolves the idea with `ideas.selectors.get_idea` (the visibility
filter), then applies the review read rule. A review id alone is never
enough; `review(id)` resolves the review, then its idea, then re-checks both,
and answers `null` for every failure.

| Surface | Rule |
| ------- | ---- |
| Review queue | Caller must be an eligible reviewer in the requested organization. Returns `SUBMITTED` ideas of **that organization only** that the caller can read, excluding the caller's own. Another tenant's `PUBLIC` ideas never appear. Non-reviewers receive an empty page, which is indistinguishable from an empty queue. |
| Review detail / history (`ideaReviews`) | Readable by the idea's **author** (completed reviews only) and by **eligible reviewers** of the idea's organization (all reviews). Not readable by other readers of the idea, including platform-wide readers of a `PUBLIC` idea ([D-9](#20-open-product-decisions)). |
| Create review (`startReview`) | `can_review(user, idea)`, idea in `SUBMITTED`, no open review. Idea row locked. Membership and eligibility re-checked inside the transaction. |
| Decision (`completeReview`) | Caller is the review's reviewer **and** still `can_review` the idea; review in progress; idea `UNDER_REVIEW`; decision valid; feedback rule met. Idea and review rows locked. |
| Assignment | Not introduced. The only reassignment is the take-over in [§5.3](#53-a-reviewer-who-stops-being-eligible-mid-review), gated on the previous reviewer's **current** ineligibility. |
| Feedback | Stored on the review, returned only under the review read rule. Never copied into comments. Text only, rendered as text; no HTML. |
| Cross-organization | Reviewer eligibility is looked up for the idea's organization; ids from another tenant resolve to `null` / "unavailable", identical to nonexistent ids. |
| Author/reviewer separation | Author refused as reviewer before any role check, at claim and at decision. |
| Historical records | No update after completion, no delete path, `PROTECT` on reviewer, read-only admin. |

**Leak review.** `ReviewType` exposes `reviewerId`, not a nested user. Both
the author-facing and the reviewer-facing read go through the same selector,
which strips in-progress reviews for authors. An unauthorized request for an
idea's reviews answers the same as "no reviews". Errors never distinguish
"exists but hidden" from "does not exist".

---

## 13. GraphQL boundary

The smallest coherent API, merged into `graphql_api/schema.py` as
`reviews.schema.Query` / `Mutation`:

**Queries**

| Field | Returns | Authorization |
| ----- | ------- | ------------- |
| `reviewQueue(organizationId: ID!, page: Int, pageSize: Int)` | `IdeaPage` (existing type) | eligible reviewer in that organization; otherwise an empty page |
| `ideaReviews(ideaId: ID!)` | `[ReviewType!]!`, ordered by round | [§12](#12-security-model) review read rule; otherwise `[]` |

**Additions to `IdeaType`** (computed per viewer, like
`availableTransitions`; convenience, never the control):

- `viewerCanStartReview: Boolean!`
- `viewerActiveReviewId: ID` (the in-progress review this viewer owns, if
  any)

**Mutations**

| Mutation | Input | Payload |
| -------- | ----- | ------- |
| `startReview(ideaId: ID!)` | none | `ReviewPayload { success, message, field, review, idea }` |
| `completeReview(input: CompleteReviewInput!)` | `reviewId: ID!`, `decision: ReviewDecision!`, `feedback: String!`, `assessments: [CriterionAssessmentInput!]!` | `ReviewPayload` |

**Types**

- `ReviewType { id, ideaId, round, reviewerId, decision, feedback,
  assessments, createdAt, completedAt }`. `submissionSnapshot` is exposed to
  reviewers only (see [D-9](#20-open-product-decisions)).
- `CriterionAssessmentType { criterion, rating, note }`.
- Enums `ReviewDecision`, `ReviewCriterion`, `CriterionRating` built from
  `TextChoices`.

**Deliberately not added:** `createReview` separate from `startReview`
(creating a review *is* claiming the idea); `requestChanges` / `approveIdea` /
`rejectIdea` as three mutations (one `completeReview` with a decision enum
keeps the transaction and validation in one place); `updateReview` for
server-side drafts (the frontend keeps the form state until submit; revisit
if reviews routinely span sessions); any assignment mutation.

`transitionIdea` remains for author moves and the hand-off, and refuses
review-owned moves ([§5.1](#51-closing-the-bypass)).

---

## 14. Frontend boundary

New `frontend/src/features/reviews/`, mirroring `features/ideas/`:

```
features/reviews/
  api/reviewsApi.ts          typed requests: reviewQueue, ideaReviews, startReview, completeReview
  hooks/useReviewQueue.ts    paged queue; same superseded-response discipline as useIdeaDiscovery
  hooks/useIdeaReviews.ts    history for one idea
  components/ReviewQueue.tsx     list of SUBMITTED ideas; reuses IdeaList rows + IdeaPagination
  components/ReviewWorkspace.tsx review detail: idea content + IdeaAttachments + IdeaDiscussion (reused)
                                 + ReviewDecisionForm
  components/ReviewDecisionForm.tsx criteria ratings, feedback, decision; one submit
  components/ReviewHistory.tsx   completed rounds; used by reviewers and by authors
  utils/reviewLabels.ts          decision/criterion/rating labels (presentation only)
```

- **Route** `/app/reviews` (queue) and `/app/reviews/:ideaId` (workspace),
  added to `app/routes.tsx`. The nav link is shown when the viewer can review
  in the current organization. Hiding it is presentation; the queue itself
  is authorized server-side.
- **Ideas workspace changes** are limited to three things. Replace the
  generic reviewer transition buttons with an "Open review" link driven by
  `viewerCanStartReview` / `viewerActiveReviewId`. Render `ReviewHistory`
  (completed feedback) on the author's own idea. In S3-005, enable editing
  in `CHANGES_REQUESTED`.
- **Author feedback view** is `ReviewHistory` inside the existing idea detail,
  with the latest `CHANGES_REQUESTED` feedback shown above the edit form.
- **Resubmission** reuses the existing Submit action. Its label becomes
  "Resubmit" when status is `CHANGES_REQUESTED`.
- No redesign of `IdeasWorkspace`. No `dangerouslySetInnerHTML`. No
  client-side authorization decisions beyond showing what the server says.

---

## 15. Notifications and audit

### Audit (required)

Review decisions are audited by the `Review` rows themselves: who, what,
when, why, and what was reviewed. Two gaps remain that `Review` cannot close:

- **Author moves are unrecorded**: resubmission times, and the hand-off to
  `AUTOMATION_PROPOSAL`.
- **Refused attempts** are not recorded anywhere.

Recommendation (S3-007): one append-only **`IdeaTransition`** row per
successful status change, written inside `ideas.lifecycle` in the same
transaction as the status change. Fields: `idea`, `from_status`,
`to_status`, `actor` (`PROTECT`), `created_at`, and an optional `review` FK.
It lives in **ideas**, because the lifecycle is the only writer and ideas
must not import reviews. The optional `review` FK is set by
`apply_review_transition`. Refused attempts are logged with structured
logging (`logger.info` with ids only, no content) rather than stored. This is
not a generic audit framework; the `audit` domain in `architecture.md` can
absorb it later. ([D-10](#20-open-product-decisions))

### Notifications (minimal)

No notification infrastructure exists and Review does not need a generic one.
Required notifications:

| Event | Recipient | Channel |
| ----- | --------- | ------- |
| Review completed (any decision) | idea author | email |
| Idea submitted or resubmitted | eligible reviewers of the organization | email, optional ([D-11](#20-open-product-decisions)) |

Implementation: a `reviews/notifications.py` following `identity/email.py`'s
contract (plain text, `django.core.mail`, failures logged and never raised,
sent after the transaction commits via `transaction.on_commit`). Synchronous
sending is acceptable at current scale. Celery is not introduced for this.
The email carries a link and the decision, not the feedback text, which stays
behind authentication. No in-app notification centre.

---

## 16. Test strategy

Backend, pytest, following the S2 per-file conventions (self-contained
fixtures, the fast password hasher from `backend/conftest.py`):

| Area | File | Key cases |
| ---- | ---- | --------- |
| Eligibility | `reviews/tests/test_eligibility.py` | Owner and Reviewer role may review; plain member may not; author may not even as Owner; inactive membership, deactivated user and former member may not; a role in org A grants nothing in B; `idea.review` data migration grants Owners in existing orgs. |
| Models | `test_models.py` | Constraints: one open review per idea, unique `(idea, round)`, decision iff completed, known enum values, unique `(review, criterion)`; completed review cannot be saved again; `PROTECT` on reviewer. |
| Services | `test_services.py` | `startReview` creates round n with snapshot and moves the idea; concurrent claims (second refused); `completeReview` per decision moves the idea atomically; feedback rule; claimant-only decision; take-over only when claimant ineligible; failure rolls back both review and status. |
| Lifecycle integration | `ideas/tests/test_lifecycle.py` (extended) | Review-owned moves refused via `transition_idea`; `availableTransitions` no longer lists them; matrix unchanged. |
| Changes requested | `test_resubmission.py` | Edit allowed in `CHANGES_REQUESTED`, visibility locked; resubmit re-validates; round n+1 opens with a new snapshot; round n unchanged. |
| Approval/rejection | `test_services.py` | `APPROVED` leaves hand-off available; `REJECTED` closes discussion (existing rule) and is terminal. |
| Selectors | `test_selectors.py` | Queue: own org only, `SUBMITTED` only, no own ideas, no `PRIVATE`, no cross-tenant `PUBLIC`. History: author sees completed only; reviewer sees all; other readers see nothing. |
| GraphQL | `test_schema.py` | Every query and mutation for anonymous, non-member, member, author, reviewer, and other-org reviewer; unauthorized reads identical to nonexistent; no nested user objects. |
| Security integration | `test_integration_security.py` | Two organizations in both directions, the S2-008 pattern extended to reviews; review ids borrowed across tenants; a former reviewer's history access. |
| Audit/notifications | `test_audit.py`, `test_notifications.py` | One `IdeaTransition` per successful move; none on refusal; email sent on commit only, not on rollback; failed send logged, not raised. |

Frontend, Vitest + Testing Library: API request shapes; queue paging and
superseded-response handling; decision form validation and submit; history
rendering for author vs reviewer; "Open review" replaces reviewer buttons;
edit and resubmit in `CHANGES_REQUESTED`.

---

## 17. Database, indexes and constraints

`reviews_review`

- `UniqueConstraint(idea, round)`, `review_round_unique_per_idea`.
- `UniqueConstraint(idea) WHERE completed_at IS NULL`,
  `review_one_open_per_idea` (partial). The database-level backstop for the
  claim race, behind the row lock.
- `CheckConstraint`: `(decision IS NULL) = (completed_at IS NULL)`.
- `CheckConstraint`: `decision IN (...)`, derived from the enum as
  `idea_status_is_known` is.
- `CheckConstraint`: `round >= 1`.
- If [D-3](#20-open-product-decisions) requires feedback for some decisions:
  `CheckConstraint` that `feedback <> ''` when `decision IN
  ('changes_requested','rejected')`.
- Indexes: `(idea, round)` via the unique constraint serves history;
  `(reviewer, -created_at)` for "my reviews"; no separate `idea` index (it is
  the prefix of the unique constraint).
- The queue query uses the existing `ideas_org_status_idx`
  (`organization, status`).

`reviews_reviewcriterionassessment`

- `UniqueConstraint(review, criterion)`; CHECK constraints on `criterion` and
  `rating` enums.

`ideas_ideatransition` (S3-007, if D-10 is accepted)

- Index `(idea, created_at)`; CHECK constraints on both status columns.

Migrations: `organizations` data migration for `idea.review` + `Reviewer`
role (S3-002); `reviews/0001_initial` (S3-002/S3-004); `ideas` migration for
`IdeaTransition` (S3-007). No change to existing `ideas` columns.

---

## 18. Integration points

**Identity.** `User` as reviewer (`PROTECT`); `is_active` checked on every
review operation via `organizations.authorization._active_user`; access-token
authentication unchanged. No Identity changes.

**Organizations.** New permission code `idea.review`, granted to `Owner`;
new non-system `Reviewer` role at bootstrap and by migration; existing
`assignRoleToMembership` grants it. Eligibility uses
`get_membership` + `membership_has_permission`. **Dependency:** a way for a
second person to join an organization ([D-1](#20-open-product-decisions)).

**Ideas.**
- `lifecycle`: reviewer check switched to `idea.review`; review-owned moves
  closed to `transition_idea` and opened through `apply_review_transition`;
  `IdeaTransition` written per move (S3-007).
- `services`: `update_idea` accepts `CHANGES_REQUESTED` (S3-005), with
  visibility locked after submission.
- `selectors`: reused as-is (`get_idea`, `get_idea_for_update`,
  `can_view_idea`); the queue is built on `_base_queryset` + visibility filter
  + organization + status.
- `schema`: `IdeaType` gains `viewerCanStartReview`, `viewerActiveReviewId`.
- Comments, votes and attachments: unchanged, reused in the review workspace.

---

## 19. Out of scope

Developer marketplace and applications, project management, tasks,
milestones, payments, subscriptions, research and consultation marketplaces,
licensing, AI scoring and recommendations, the Department domain, the
`AutomationOpportunity` entity and anything that acts on
`AUTOMATION_PROPOSAL`, explicit or automatic reviewer assignment, review
panels/quorum, numeric scoring, organization-defined criteria, in-app
notification centre, Celery, generic audit framework, membership invitation
(Organizations domain; see D-1), and unrelated Identity or Ideas changes.

---

## 20. Open product decisions

Each needs an answer before the listed task starts. The recommendation is the
smallest option consistent with the existing code. None has been implemented.

| # | Decision | Recommendation | Blocks |
| - | -------- | -------------- | ------ |
| **D-1** | How does a second person join an organization? Without it no real reviewer can exist. | Add a minimal "add existing user as member" or invitation flow **in the Organizations domain**, as a Sprint 3 prerequisite or a parallel task, not inside Reviews. | S3-002 usefulness; S3-008 end-to-end |
| **D-2** | Who may record a decision, and what happens if the claimant becomes ineligible? | Only the claimant; take-over (`WITHDRAWN` + new round) allowed only when the claimant is no longer eligible. | S3-004 |
| **D-3** | Is feedback required? | Required for `CHANGES_REQUESTED` and `REJECTED`, optional for `APPROVED`. | S3-004 |
| **D-4** | Criteria: which ones, required or optional, org-defined or fixed? | Fixed five ([§9](#9-validation-criteria)), all rated before any decision, `NOT_APPLICABLE` allowed; no rating forces a decision. | S3-004 |
| **D-5** | Single reviewer or several? One approval sufficient? | One reviewer per round; one decision resolves the round. | S3-004 |
| **D-6** | Submitted `PRIVATE` ideas are unreviewable. Should submission require `ORGANIZATION`/`PUBLIC` visibility, and is visibility locked after submission? | Refuse submission of `PRIVATE`/`DEPARTMENT` ideas with a clear message; lock visibility outside `DRAFT`. | S3-003, S3-005 |
| **D-7** | Is the `APPROVED → AUTOMATION_PROPOSAL` hand-off offered in the S3 UI, and by whom? | Keep the existing reviewer transition, hidden in the UI until the Opportunities domain consumes it. | S3-006 |
| **D-8** | Should `problem_statement` / `proposed_solution` / `expected_benefit` become editable so criteria have content to assess? | Not in S3; assess `description` and attachments. | S3-004 (criteria wording) |
| **D-9** | Who may read reviews: can other org members see decisions and feedback? Is the reviewer's identity shown to the author? | Author + org reviewers only; reviewer id shown to author (accountability). | S3-003, S3-004 |
| **D-10** | Is an `IdeaTransition` audit row acceptable as the audit mechanism? | Yes, in `ideas`, append-only. | S3-007 |
| **D-11** | Should reviewers be emailed on every submission? | Author on decision: yes. Reviewers on submission: off for S3 (queue is the channel). | S3-007 |

---

## 21. Implementation plan

**S3-002 Reviewer Eligibility & Assignment.** `idea.review` permission code;
Owner grant plus a data migration for existing organizations; `Reviewer`
role at bootstrap plus migration; switch `ideas.lifecycle` reviewer check to
the permission; `reviews` app skeleton with `eligibility.can_review`;
`Review` + `ReviewCriterionAssessment` models, constraints and migration;
admin (read-only for completed). No assignment entity. Tests: eligibility
matrix, tenant scoping, migration. *Needs D-1 at least scheduled.*

**S3-003 Review Queue.** `reviews.selectors.review_queue` (org, `SUBMITTED`,
readable, not own) and `ideaReviews` read rule; `reviewQueue` and
`ideaReviews` queries; `IdeaType.viewerCanStartReview` /
`viewerActiveReviewId`; frontend `features/reviews` api/hooks, `ReviewQueue`,
route and nav link. *Needs D-6, D-9.*

**S3-004 Review Submission & Decision.** `startReview` (claim, snapshot,
`SUBMITTED → UNDER_REVIEW`) and `completeReview` (assessments, feedback,
decision, idea transition, one transaction); `lifecycle.apply_review_transition`;
close review-owned moves on `transitionIdea`; take-over path; immutability
guard; `ReviewWorkspace`, `ReviewDecisionForm`, `ReviewHistory`; replace
reviewer buttons in `IdeasWorkspace`. *Needs D-2, D-3, D-4, D-5, D-8.*

**S3-005 Changes Requested & Resubmission.** `update_idea` accepts
`CHANGES_REQUESTED` with visibility locked; optional submission visibility
rule (D-6); author feedback view and edit/resubmit UI; round n+1 behaviour;
`lifecycle.ts` copy and `ideas-domain.md` updates.

**S3-006 Approval & Automation Opportunity.** Approved-idea handling per
D-7: approving review surfaced on approved ideas; hand-off kept as the
existing transition (UI per D-7); document the boundary to Opportunities. No
new entity.

**S3-007 Notifications & Audit.** `IdeaTransition` append-only audit written
by `ideas.lifecycle` (D-10); structured logging of refusals;
`reviews/notifications.py` email to the author on decision, sent
`on_commit`, failure logged (D-11).

**S3-008 Integration, Security & Testing.** Cross-feature, two-tenant
security suite for reviews (S2-008 pattern); GraphQL authorization sweep;
end-to-end reviewer and author flows in the frontend; full CI; docs
(`architecture.md`, `ideas-domain.md`, this file, `CHANGELOG.md`).
