# Ideas Domain

The `ideas` app (S2-001). It owns the platform's first business domain
beyond Identity & Access: the **problem/idea submission foundation**.

S2-001 is architecture and schema. It establishes the entities, the
lifecycle vocabulary, the visibility model, the tenancy and authorization
boundaries, and the storage boundary - and stops there. It deliberately
implements no business operation, no GraphQL operation and no UI. What is
specified but not yet built is written down here so the next sprint has a
contract rather than a guess.

**Corrected against the code.** The submission-context phase changed this
domain's shape - three contexts instead of one tenant, an organization stage
in front of the platform stage, `TEAM` as a visibility, a frozen submission
version and the owner's go-ahead - and the sections below were written before
it. Lifecycle, Visibility, the tenancy rules, the constraint and index tables,
the selector list and the GraphQL and frontend boundaries have been brought
back into agreement with `backend/ideas/` on `feature/reviews`; the S2 and S3
narratives further down are left as they were written, because a record of what
each sprint decided is worth more than a document that was always current.

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
Organization                       Team                        User
    │                                │                           │
    ├── Idea                        ├── Idea                    ├── authored Ideas      (ideas_ideas)
    │     ├── Category               │     ├── Category          ├── Comments           (idea_comments)
    │     ├── Comment                │     ├── Comment           ├── Votes              (idea_votes)
    │     ├── Vote                   │     ├── Vote              └── Attachments        (idea_attachments)
    │     ├── Attachment             │     ├── Attachment
    │     ├── IdeaTransition         │     └── IdeaTransition
    │     └── IdeaSubmissionVersion  └── IdeaSubmissionVersion
    │
    └── Membership                  TeamMembership

individual: no tenant column at all — the author is the tenant
```

An `Idea` hangs off **one of three** owners, and the diagram above draws only
two branches because an individual idea belongs to neither - see [Submission
context](#submission-context).

| Entity | Table | Notes |
| ------ | ----- | ----- |
| `Category` | `ideas_category` | Platform-wide, flat, reusable. Not tenant-scoped. |
| `Idea` | `ideas_idea` | The submission. Its tenant is the **context**, not a column that is always set. |
| `Comment` | `ideas_comment` | One idea, one authenticated author. A reply names its parent; one level deep. |
| `Vote` | `ideas_vote` | One row per (user, idea). No value, no downvotes. |
| `Attachment` | `ideas_attachment` | Metadata only; the bytes live in object storage. |
| `IdeaTransition` | `ideas_ideatransition` | One append-only row per successful status change: from, to, actor, when. The lifecycle's audit trail (S3-007). |
| `IdeaSubmissionVersion` | `ideas_ideasubmissionversion` | The idea's content **frozen** at the moment it went to the platform. Never written again (see [Locked submissions](#locked-submissions-the-platform-holds-a-copy)). |

`Category` is deliberately **not** organization-scoped. It is what makes two
organizations' ideas comparable to each other, which is the input a future
cross-tenant discovery or matching feature needs. An organization-scoped
category would silently make "customer support" in one company unrelated to
"customer support" in another.

## Submission context

**Every idea has exactly one creation context, and it is stated rather than
inferred.**

| `submission_context` | Owner column | Who may file one | Who validates it first |
| -------------------- | ------------ | ---------------- | ---------------------- |
| `individual` | none - the author *is* the tenant | any active user | the platform |
| `team` | `team` | an active member of that team | the platform |
| `organization` | `organization` | an active member of that organization | **its own organization**, then the platform |

This is the field that replaced "an idea belongs to an organization". The
organization column is **nullable**, because a user with no organization can
file an individual idea and take it all the way through platform review - so
the tenant cannot be inferred from the row and has to be stated.

**One model, three contexts, deliberately.** Not `IndividualIdea` /
`TeamIdea` / `OrganizationIdea`: a comment, a vote, an attachment, a review and
a lifecycle status all mean the same thing in every context, and splitting the
model would mean either duplicating all of them or making them multi-table
foreign keys. What the context changes is *who may see it* and *whose review
comes first* - never the shape of the row.

**Enforced three times, on purpose.** The write path proves it
(`services._resolve_context`, which refuses a mismatched combination with a
message naming the impossible pair), `Idea.clean()` repeats it, and
`idea_submission_context_matches_tenants` enforces it in the database. Three
enforcements of one rule is not redundancy to be tidied away: each catches a
path the other two do not - a service that forgot to ask, a `bulk_create` that
bypasses `full_clean()`, and a future app writing a row directly.

The two contexts are proved differently, and the difference is the point: a
team submission needs an active **team membership** (filing is not submitting,
so it is not `team.ideas.submit`), an organization submission needs an active
**organization membership** through the same
`organizations.authorization.get_membership` every other write uses, and an
individual submission needs nothing beyond being an active user filing their
own idea.

### The problem story (guided intake form)

An idea is written by the person who has the problem - a student, a member
of staff, a manager, a customer - not by a developer. So beyond `title` and
`description` (the problem in the author's own words), `Idea` carries the
answers to a short, plain-language interview about the problem. None of it
asks about technology: the author says what happens and what better would
look like, and review, requirements and later stages decide how.

| Question (as the form asks it) | Column | Type |
| --- | --- | --- |
| How do you currently handle this? | `current_process` | text |
| What do you currently use to handle this work? | `current_tools` (+ `current_tools_other`) | list of `Idea.CurrentTool` (+ ≤200 chars) |
| Who is usually responsible for doing this? | `performed_by` | ≤300 chars |
| Who is affected when this problem happens? | `affected_people` | ≤300 chars |
| How often does this happen? | `frequency` | `Idea.Frequency`, blank = not answered |
| Approximately how much time does this work take? | `time_required` | ≤120 chars, free text ("several days") |
| Approximately how many people are involved? | `people_involved` | positive integer, null = not answered |
| What happens because of the problem? | `impacts` (+ `impact_details`) | list of `Idea.Impact` (+ text) |
| What would you like to be different? | `improvement_goal` | text |
| If the problem were solved, what would you like to happen? | `desired_outcome` | text |
| What should people be able to do more easily? | `easier_for_people` | text |
| How would you know that this problem has been solved? | `expected_benefit` | text |
| Is there anything important we should know? | `important_considerations` | text |

Design decisions:

- **One column per question, not a JSON blob**, so each answer can be read,
  filtered and later assessed on its own. The two multi-selects are
  PostgreSQL arrays of enum codes, stored de-duplicated in the vocabulary's
  own order, and - like `status` and `visibility` - every closed vocabulary
  has a CHECK constraint derived from the same tuple as its `TextChoices`.
- **All optional.** A draft needs only a title. The submission rule is
  unchanged - title, description of at least 20 characters, a category, and a
  visibility a reviewer can read - so the story helps without gatekeeping.
- **Shape-only validation** in `ideas.services`: known values, sane bounds
  (long answers ≤ `MAX_STORY_ANSWER_LENGTH` = 5000, ≤ 1,000,000 people),
  refused per field rather than truncated.
- **`expected_benefit` is the existing column**, now written: "what would be
  better once it is solved" is exactly the benefit the `expected_benefit`
  review criterion assesses. `problem_statement` stays unwritten (it would
  duplicate `description`) and `proposed_solution` is **deliberately never
  asked of the author**.
- **Same rules as all content.** Written only through `create_idea` /
  `update_idea` (author, per-context tenant standing, editable status); read
  through the same visibility-filtered selectors as `description`. An update
  writes the whole input, so an omitted answer is cleared - the form always
  sends every field.
- `IDEA_STORY_FIELDS` lists the columns once; the review snapshot
  (`reviews.services._submission_snapshot`, key `story`) uses it so a
  reviewer's record of what they assessed includes every answer.

The frontend (`IdeaForm`) asks these over eight steps - the problem, what
happens today, the impact, what should improve, how you would know it is
solved, anything important, category & visibility, supporting documents -
with every step reachable at any time and "Save draft" on each. Supporting
documents for a new idea are held in the form and uploaded through the
existing attachment endpoint straight after the draft is created, before it
is submitted; there is no second upload path.

### Development categories (temporary seed)

Until category management in Django Admin is built, a fresh database has no
categories - and an idea cannot be submitted without one. For local
development and end-to-end testing there is a **bootstrap seed**, and only
that:

```bash
cd backend
python manage.py seed_dev_categories
```

It creates twelve sample categories (Finance, Education, Human Resources,
Operations, Information Technology, Customer Service, Healthcare, Procurement,
Logistics, Government & Public Services, Sales & Marketing, Other) with stable
slugs, from `ideas/dev_categories.py`.

- **Same storage.** They are ordinary `Category` rows, served by the existing
  `categories` query. There is no second list: not in the frontend, not in a
  fixture, not in a setting.
- **Additive and idempotent.** A category is created only when neither its
  slug nor its name is taken. Existing rows - seeded or made by an
  administrator - are never updated, reactivated or deleted, so re-running it
  is always safe.
- **Development only.** The command refuses to run with `DEBUG` off unless
  given `--allow-non-debug`, for a deliberately seeded test database.
- **Not the taxonomy.** Nothing may branch on these names or slugs. The
  frontend renders whatever `id`, `name`, `slug` and `description` the query
  returns (`IdeaForm.categories.test.tsx` uses invented categories to keep it
  that way).

The intended architecture, which the seed does not change:

```
Django Admin  ->  Category records  ->  GraphQL (categories)  ->  Idea Intake Form
```

The future Admin UI manages these same records - create, edit, activate,
deactivate - and needs no frontend change. Once it exists, the seed can be
deleted without touching anything that reads categories.

Reverse accessors are named for what they are, not for symmetry:
`organization.ideas`, `category.ideas`, `idea.comments`, `user.ideas`,
`user.idea_comments`, `user.idea_votes`, `user.idea_attachments`.

## Lifecycle

Eleven statuses, in two stages. The **organization stage** exists only for an
organization-context idea; the **platform stage** is identical for all three
contexts, so no code after it has to ask what kind of idea it is looking at.

| Status | Meaning | Reached in |
| ------ | ------- | ---------- |
| `DRAFT` | Being written. May be incomplete. Visible per `visibility`. | any context |
| `SUBMITTED_TO_ORGANIZATION` | Put forward to the idea's **own organization** to confirm. | organization only |
| `ORGANIZATION_CHANGES_REQUESTED` | The organization asked for changes. | organization only |
| `ORGANIZATION_CONFIRMED` | The organization confirmed this is what it wants to submit. Not yet a submission. | organization only |
| `SUBMITTED` | Submitted **to the platform**, and frozen as a version. | any context |
| `UNDER_REVIEW` | A platform reviewer has it. | any context |
| `CHANGES_REQUESTED` | The platform sent it back with reasons. | any context |
| `REJECTED` | Not going forward. Terminal. | any context |
| `APPROVED` | The platform approved it. **Waiting for the owner's confirmation.** | any context |
| `READY_FOR_IMPLEMENTATION` | The owner gave the go-ahead. Handed to the developer track. | any context |
| `AUTOMATION_PROPOSAL` | In the opportunity/proposal track. | any context |

**`organization_review` is deliberately not a status.** "A reviewer has it
open" is a fact about the `reviews.Review` row, and is exactly the duplication
that `Review`'s docstring argues against when it declines to carry a `status`
column. The idea is `SUBMITTED_TO_ORGANIZATION` the whole time somebody is
looking at it.

**`APPROVED` is deliberately one state, not two.** "platform approved" and
"platform approved, awaiting the owner's go-ahead" would be a distinction
without a second possible action: from `APPROVED` the only move in the whole
domain is the owner's explicit go-ahead, and it is guarded the same way either
way. A client renders it as "Platform Approved — Your Confirmation Needed",
which is what `ideas.states.FRIENDLY_LABELS` says and why.

**`READY_FOR_IMPLEMENTATION` means the owner authorized this idea to proceed.**
It does **not** mean a developer was selected, a project exists, or any work
has started.

The vocabulary was established in S2-001 and every transition is implemented,
so the review workflow is behaviour rather than a migration. What is *not*
implemented is the far end: nothing acts on an idea once it reaches
`AUTOMATION_PROPOSAL`.

### Transition matrix

The table is `ideas.lifecycle.TRANSITIONS`: `(from, to) -> required actor`. It
is the whole lifecycle — anything not listed is not a transition, and
`transition_idea` refuses it. There are **three** kinds of actor, and the
matrix names them rather than a bare boolean.

| From | To | Actor | Context |
| ---- | -- | ----- | ------- |
| `DRAFT` | `SUBMITTED_TO_ORGANIZATION` | author | organization |
| `DRAFT` | `SUBMITTED` | author | individual, team |
| `SUBMITTED_TO_ORGANIZATION` | `ORGANIZATION_CHANGES_REQUESTED` | organization reviewer | organization |
| `SUBMITTED_TO_ORGANIZATION` | `ORGANIZATION_CONFIRMED` | organization reviewer | organization |
| `ORGANIZATION_CHANGES_REQUESTED` | `SUBMITTED_TO_ORGANIZATION` | author | organization |
| `ORGANIZATION_CONFIRMED` | `SUBMITTED` | author | organization |
| `SUBMITTED` | `UNDER_REVIEW` | platform reviewer | any |
| `UNDER_REVIEW` | `CHANGES_REQUESTED` | platform reviewer | any |
| `UNDER_REVIEW` | `REJECTED` | platform reviewer | any |
| `UNDER_REVIEW` | `APPROVED` | platform reviewer | any |
| `CHANGES_REQUESTED` | `SUBMITTED` | author | any |
| `APPROVED` | `READY_FOR_IMPLEMENTATION` | **author** | any |
| `READY_FOR_IMPLEMENTATION` | `AUTOMATION_PROPOSAL` | platform reviewer | any |

**The context column is `lifecycle.context_allows`, and it is load-bearing.**
The matrix is decided over statuses, so `DRAFT → SUBMITTED` appears in it once
- as "an individual or team author submits their idea". For an *organization*
author the same pair would be a way to put an idea in front of the platform
without its organization ever seeing it, which would make the entire
organization stage optional and every organization-review guarantee in the
module decorative. So each pair is legal only for the contexts it means, and a
refusal is about the *idea* rather than the caller: the person asking is the
author, who is allowed to submit; the thing they cannot do yet is skip a stage
their organization owns. Checked in the same two places as the actor rule -
`can_transition`, which renders what the UI offers, and the two write paths -
so an idea cannot be offered a move that would be refused, nor moved by one
that would have been.

`SUBMITTED → REJECTED` is deliberately absent, in both stages: a submission has
to be picked up before it can be resolved, which is what the `UNDER_REVIEW`
state (or the open organization review) records.

**Nothing returns to `DRAFT`.** A draft is by definition an idea with no
`submitted_at`, so moving back to it would have to either erase an audit fact
or contradict the model's own invariant. The route back to the author is
`CHANGES_REQUESTED → SUBMITTED`, without pretending the idea was never
submitted.

**Review-owned moves.** Six of the pairs are `lifecycle.REVIEW_OWNED_TRANSITIONS`:
`SUBMITTED_TO_ORGANIZATION → ORGANIZATION_CHANGES_REQUESTED | ORGANIZATION_CONFIRMED`,
and `SUBMITTED → UNDER_REVIEW`, `UNDER_REVIEW → CHANGES_REQUESTED | APPROVED |
REJECTED`. `transition_idea` refuses them (after the actor check, so only a
would-be reviewer learns why) and `availableTransitions` never lists them: each
must leave a `Review` row written in the same transaction as the status change,
so they are made only by `reviews.organization_review` /
`reviews.services` through `lifecycle.apply_review_transition`. The matrix
itself is unchanged - these are still the lifecycle's moves and still need a
reviewer of the right kind; what changes is that they cannot be made without a
review. `TRANSITION_REVIEW_SCOPE` fixes which track each belongs to, so a
platform decision can never be recorded as an organization one. See
[`reviews-domain.md`](reviews-domain.md) §5.

`READY_FOR_IMPLEMENTATION → AUTOMATION_PROPOSAL` is **not** review-owned: no
review is open by then, and `APPROVED → READY_FOR_IMPLEMENTATION` is the
owner's rather than a reviewer's. It is the hand-off to the automation-opportunity track, and the go-ahead that
precedes it survives it - which is why `idea_go_ahead_only_when_ready` names
both states explicitly. Nothing in this
repository acts on the resulting state and no UI offers the move; it exists so a
later sprint is behaviour rather than a migration.

**The three actors, and why the platform one is not an organization role.**

- **Author.** The idea's `author_id`, and they must still hold standing in the
  idea's tenant - an organization membership, a team membership, or, for an
  individual idea, nothing, because the author *is* its tenant. Checked per
  context in `_require_tenant_standing`.
- **Organization reviewer.** An active member of the idea's **own organization**
  holding `idea.review` there, and not the author
  (`lifecycle.is_organization_reviewer`). The self-review exclusion is
  structural rather than a matter of hoping two roles go to different people:
  bootstrap makes everybody's own organization theirs, so an author who also
  holds `Owner` genuinely holds `idea.review`, and no permission check would
  catch them.
- **Platform reviewer.** An account holding the platform-scoped
  `administration.review_platform_submissions` permission, and not the author
  (`lifecycle.is_platform_reviewer`). No organization membership, no
  organization role, no team role - so no code path exists in which holding
  `idea.review` anywhere also confers platform review. Platform review is
  cross-tenant by construction, so a platform move is **exempt** from the
  tenant-standing check: requiring membership would make the platform track
  unreachable for anybody outside the idea's own tenant, which is the track not
  working rather than a restriction. The authorization is the platform
  permission, and `ideas.selectors.can_view_idea` has already established that
  this reviewer may read the submission.

**From system role to `idea.review` (S3-002).** The reviewer gate used to be
"holds a system role", which meant only an Owner could review. It is now the
`idea.review` permission, checked through
`organizations.authorization.membership_has_permission` like every other
capability. The Owner role holds it (at bootstrap, and by migration
`organizations/0003` for organizations that already existed), so nobody who
could review lost the ability; the non-system **Reviewer** role holds it too,
so organization reviewing can be granted without ownership.

**What the author can change while changes are requested (S3-005, extended).**
The content: `update_idea` accepts an idea in `services.EDITABLE_STATUSES` -
`DRAFT`, `CHANGES_REQUESTED` and `ORGANIZATION_CHANGES_REQUESTED` - for its
author, who must still hold tenant standing, with the same validation as a
draft. **Visibility is fixed once submitted**: in either changes-requested
state a different visibility is refused as a field error, because narrowing an
idea under review would hide it from its reviewers
([`reviews-domain.md`](reviews-domain.md) D-6). The author then resubmits with
the existing `submitIdea` (re-running the submission validation;
`submitted_at` keeps its first value), and may attach further evidence at any
point. The flow is

    CHANGES_REQUESTED → author edits → SUBMITTED → a reviewer starts round n+1

and the same shape one stage earlier for the organization. Nothing in it
touches a review: the completed review that asked for the changes is immutable
and keeps its own snapshot, and resubmission creates no review - the next round
exists only once a reviewer claims it.

`submitted_at` is set once, by the first move out of `DRAFT`, and never
rewritten: it is an audit fact, and `created_at` (the row was written) and
`submitted_at` (somebody deliberately put it forward) are genuinely different
moments. The model enforces that a draft has no `submitted_at` and anything
else has one, which is why a transition writes the two together.

**Concurrency.** Every transition reads its row with `SELECT … FOR UPDATE`
inside the transaction, so two transitions of the same idea cannot both read
the same starting status and both succeed: the second waits, then re-reads a
status that no longer permits its move. Without the lock, "approve" and
"reject" fired together would both apply and the last write would silently
win.

### What a status means to the person reading it

`ideas/states.py` is the third question this domain has to answer - *where is
my idea, who is reviewing it, what do I need to do next* - and it answers it
**per (idea, reader)**:

- `FRIENDLY_LABELS` / `SHORT_LABELS`: one label per status, deliberately more
  specific than `Status.label`. "Under review" was true of every review the
  platform did, which is no longer true; `PLATFORM_APPROVED` reading as
  "Approved" is what made a user think the idea was done when it was waiting on
  them.
- `Stage`: six buckets, and `stages_for(idea)` drops the two organization steps
  for an individual or team idea rather than drawing them struck through - a
  progress bar that can never complete is worse than a shorter honest one.
- `primary_action_for(user, idea)`: **one** next action, read from
  `lifecycle.available_transitions` plus the two eligibility predicates. It can
  therefore never offer something the server would refuse, and an author and a
  reviewer looking at the same approved idea see different things - which is
  the whole point of a state-aware UI.
- `TERMINAL_STATUSES = {REJECTED}`. `REJECTED` is the only terminal state the
  platform itself produces; "done" is the word that made people think an
  approved idea was on its way to a developer when it was waiting for its owner.

Nothing in that module mutates and nothing in it authorizes: it is
presentation, and every action it names is enforced by the service behind it.

### Locked submissions: the platform holds a copy

`Idea.is_locked` is `platform_locked_at is not None` and nothing else, so
"locked" has exactly one definition in the codebase.

When - and only when - an idea moves to `SUBMITTED`, `ideas/versions.py`
copies its content into an **`IdeaSubmissionVersion`** inside the same
transaction, and stamps `platform_locked_at` and `platform_version` on the
same save (they are constrained to move together by
`idea_platform_lock_matches_version`, so two saves could not be written at
all). Three consequences, each a requirement the module exists for:

1. **A reviewer approves a specific thing.** The platform reviews the version,
   not the mutable row.
2. **Answering feedback never rewrites history.** The author edits the working
   copy, resubmits, and version n+1 appears beside version n. The old round's
   review still describes the old submission.
3. **"Locked" needs no special case in the edit path.** Because the official
   copy is a different row, `update_idea` does not have to know that locking
   exists in order to be correct - the thing that must not change cannot be
   changed by editing the row that is allowed to change.

A resubmission creates a **new** version; nothing overwrites one, and
`unique_current_version_per_idea` is the backstop for two submissions racing to
be the official one. Unlocking is not an operation this domain has.

### The owner's go-ahead

`ideas/go_ahead.py` keeps three facts apart which a single "approved" state
would collapse:

    PLATFORM_APPROVED  a platform reviewer approved it; a report was generated
                       and delivered to the owner.
    GO-AHEAD           the **owner** has read that report and authorized this
                       idea to proceed toward implementation.
    READY              the idea is handed to the developer track.

Only the second is the owner's to give, and only the author's: not an
organization Owner, not a team Owner, not a platform administrator. A company
cannot approve its own member's idea into implementation, and no role anywhere
can stand in for the person who wrote it. Deliberately unfriendly to
automation:

- **Opening the report is not approval.** `confirm_go_ahead` does not care
  whether the report has been opened; a `platform_report_read_at` column would
  be a temptation to infer consent from a page view, and consent inferred from
  a page view is not consent.
- **Receiving the email or the notification is not approval.** Both are
  delivered by `notifications.services.deliver`, after the commit and never
  raising, and this module has no coupling to it.
- **The click must be the author's**, recorded as `owner_go_ahead_at` /
  `owner_go_ahead_by` (`PROTECT`: an audit fact must not vanish with an
  account) by the transition itself, so an idea cannot reach implementation
  without an attributable moment and an attributable person.

`confirm_go_ahead` is a thin wrapper over the lifecycle's
`apply_owner_go_ahead`, which has exactly one caller. The *meaning* of the
operation lives in `go_ahead`; the *move* stays in the matrix, because that is
the only place in the platform that decides who may take an idea from one
status to another.

### Why the free-text fields are blank-able

A draft is incomplete by definition - saving part-way is the point. "What
must be filled in to submit" is therefore a rule of the **transition**, owned
by the `submit_idea` service (S2-002), not of the schema. Expressing it as
`null=False` would make drafts unsavable, and as a database `CHECK` would
constrain review states this domain does not own.

## Visibility

**Ownership and audience are separate facts.** `submission_context` and its two
nullable tenant columns say *whose* idea this is; `visibility` says *who may read
it*. They are stored separately and no write path sets one from the other, so a
team idea made `PUBLIC` is still that team's - which is the property
`ideas/tests/test_ownership.py` pins from both sides.

> **Superseded in part.** The audience of an idea now follows its level and is not
> chosen; there is no `PUBLIC` option and `ideas.services.AUDIENCE_BY_CONTEXT`
> replaces `SELECTABLE_VISIBILITIES_BY_CONTEXT`. See `collaboration.md` section 0.
> The table below describes the values that exist in the column.

| Visibility | Who may read the idea | Enforceable today |
| ---------- | -------------------- | ----------------- |
| `PUBLIC` | Any authenticated platform user, in any organization. | Yes |
| `ORGANIZATION` | Any active member of the idea's **organization**. Offered only on an organization idea. | Yes |
| `TEAM` | Any active member of the idea's **team**. Offered only on a team idea. | Yes |
| `DEPARTMENT` | Active members of the idea's organization who are also in the author's department. | **No — fails closed** |
| `PRIVATE` | The author only. | Yes |

`TEAM` exists because `visibility=ORGANIZATION` on a team idea used to mean "my
team", which made an idea's owner and its audience the same word — and the one
thing a reader of a list cannot be allowed to confuse. Each value is now scoped to
the tenant it actually names: a team idea is not readable through an organization
membership and an organization idea is not readable through a team one.

**The audience must belong to the owner's kind.** `ideas.services
.SELECTABLE_VISIBILITIES_BY_CONTEXT` offers `private` and `public` in every
context and one tenant audience per context; anything else is refused with a
sentence that says which combination is impossible rather than a generic "choose
who can see this".

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

**One branch is not the visibility rule: a submission the platform was actually
sent.** `platform_reviewer_filter(user)` adds `platform_version >= 1` for an
account holding the platform review permission. It is narrow on purpose -
`platform_version` is stamped by `versions.freeze_submission` in the same
transaction as the move to `SUBMITTED`, so it cannot match a draft, an idea
still with its organization, or anything an author has not deliberately put
forward. Without this branch the platform track would be unreachable for
every reviewer outside the idea's own organization, and a platform reviewer is
by definition not a member of it.

`visibility` defaults to `PRIVATE`, so a new submission is visible to nobody
but its author until somebody deliberately widens it. Code that forgets to
consider visibility at all therefore leaks nothing.

**Visibility and status are independent.** Submitting an idea does not change
who can read it, and nothing in the lifecycle widens (or narrows) visibility.
Two consequences follow, both intended and both pinned by
`ideas/tests/test_integration_security.py`:

- An `ORGANIZATION`, `TEAM` or `PUBLIC` *draft* is readable by its readers
  before it is submitted. Drafts are only author-only when they are `PRIVATE`.
- **What a submission must be readable by now depends on who reviews it**
  (`services._validate_submission_visibility`). A reviewer can only act on an
  idea they can read, so a submission nobody can read would wait in its queue
  for ever:

  | Context | Required to submit | Refusal |
  | ------- | ------------------ | ------- |
  | organization | `ORGANIZATION` or `PUBLIC` (`REVIEWABLE_VISIBILITIES`) | "A private idea cannot be reviewed. Share it with your organization or make it public before submitting." |
  | individual, team | `PUBLIC` only | "An idea you submit on your own, or for a team, is reviewed by the platform. Make it public so platform reviewers can read it before submitting." |

  The second row is the rule that surprises people, and it follows from the
  platform track being cross-tenant: a platform reviewer holds no membership of
  the author's team, so `TEAM` visibility would hide the very submission the
  platform is waiting for. It is also why an individual or team idea has no
  organization stage - there is nothing local to confirm it, and the platform
  is the whole audience.

  The rule applies to every move into a submission status - the first
  submission, the resubmission after either track's changes request, and the
  `ORGANIZATION_CONFIRMED → SUBMITTED` handoff - and is checked with the other
  submission rules rather than as a field error. `PRIVATE` keeps its meaning: a
  private draft stays author-only, and nothing widens visibility on the
  author's behalf. The frontend says so next to the visibility picker. Ideas
  submitted as `PRIVATE` before S3-008 are not changed; they stay out of every
  queue until staff widen them in the Django admin with the author's agreement.

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
_require_membership(user, idea.organization_id)   # organization context
if idea.author_id != user.pk:
    raise ...
```

**Tenant standing is proved per context, and the individual case proves
nothing.** `services._load_editable_idea` and `lifecycle
._require_tenant_standing` each branch three ways, and the two must agree -
otherwise one of them refuses an operation the other allows:

| Context | Proof required on every write |
| ------- | ---------------------------- |
| organization | an active `Membership` in `idea.organization` (`authorization.get_membership`) |
| team | an active `TeamMembership` in `idea.team` (`teams.authorization.is_member_of`) |
| individual | none - the author **is** the tenant |

That branch is not a formality. It used to ask for an organization membership
unconditionally, which meant a team or individual idea could not be edited by
anybody, **its author included**: `organization_id` is null for those two, and
a null tenant is not a membership. A team submission additionally needs
`team.ideas.submit` to *submit* (`services.submission_target`) - filing is not
submitting, which is why a plain member can file for a team while the
submission itself is a permission the team's roles decide. A team can never be
granted an approval permission, so that is the furthest a team reaches in the
lifecycle.

**The chain `User → Membership → Organization → Idea` is still enforced, but by
the context rather than by a required column.** `Idea.organization` is
**nullable** - it has to be, since an individual idea has no organization - so
"there is no such thing as an idea outside a tenant" is now "there is no such
thing as an idea whose tenant is not stated", enforced by
`idea_submission_context_matches_tenants`, by `Idea.clean()` and by
`_resolve_context`. A tenant filter that forgets the context cannot reach
anything, because there is no fourth shape for a row to be in.

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
pointer: `storage_key` is the object's key in whatever backend
`ideas/storage.py` is configured to use, and the bytes themselves live
there.

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

**As implemented (S2-007).** Upload and download go *through* this server,
not straight between the browser and the bucket: the browser `POST`s the
file to `ideas/views.py::upload_attachment_view`, which validates it and
writes it to `storages['attachments']`
(`config/settings/base.py`'s `STORAGES` entry) via `ideas/storage.py`, and
downloads stream back through `download_attachment_view` the same way. This
is a deliberate, current-scale choice rather than the final design: today
the only backend actually installed and configured is Django's own
filesystem `Storage` (see `ATTACHMENTS_STORAGE_BACKEND` in
`backend/.env.example`), and there is no object-storage bucket, no signed-URL
mechanism and no `django-storages` dependency in this codebase to route
around. `ideas/storage.py` is written so that changes: every read and write
goes through Django's pluggable `Storage` API rather than a filesystem path
or a client library, so pointing `ATTACHMENTS_STORAGE_BACKEND` at an
installed object-storage backend later is a settings change, not a rewrite
of `ideas/services.py` or `ideas/views.py`. Presigned, direct-to-bucket
upload/download (skipping this server for the bytes) is a reasonable next
step once such a backend exists, and is exactly the kind of change this
abstraction was built to absorb - it is not implemented now because there is
no bucket yet to presign a URL against.

## Database design

Migrations: `ideas/0001_initial.py` through `ideas/0009_idea_team_visibility.py`.
The later ones are additive — `0005` adds the context and its nullable tenant
columns, `0006` the context CHECK, `0007` widens the go-ahead constraint so the
survives the hand-off, `0008` drops the duplicate `Idea.team` index, and `0009`
adds `TEAM` as a visibility value. **No migration moves a row.**

### Constraints

| Constraint | Where | Why |
| ---------- | ----- | --- |
| `unique_category_name`, `unique_category_slug` | `Category` | Both are user-facing identifiers; a same-spelled category is a data-entry mistake, not two categories |
| `idea_status_is_known` | `Idea` (`CHECK`) | `choices` only covers `full_clean()`; `bulk_create`, `QuerySet.update` and a management command all bypass it |
| `idea_visibility_is_known` | `Idea` (`CHECK`) | Same |
| `idea_submission_context_matches_tenants` | `Idea` (`CHECK`) | Exactly one of the three shapes, and no fourth. Without it an `individual` idea carrying an organization would silently inherit that organization's tenancy on every read, and an organization-context idea with no organization would be a row no reviewer could ever reach |
| `idea_platform_lock_matches_version` | `Idea` (`CHECK`) | Lock and version number are one fact, so "submitted to the platform without a locked submission" is not a state a crash between two statements can leave behind |
| `idea_go_ahead_only_when_ready` | `Idea` (`CHECK`) | The owner's go-ahead is exactly the move into `ready_for_implementation` (or the hand-off that follows it), and belongs to nobody else — so an idea cannot reach implementation without an attributable moment and person |
| `unique_vote_per_user_idea` | `Vote` | A second vote must be impossible regardless of which service or adapter issued it - including two concurrent ones |
| `storage_key` unique | `Attachment` | Two rows pointing at one object would make deleting either silent data loss |
| `idea_transition_from_is_known`, `idea_transition_to_is_known`, `idea_transition_changes_status` | `IdeaTransition` | Both statuses are real and the move actually changed something, so a "transition" to the status it was already in cannot be recorded as history |
| `version_number_unique_per_idea`, `unique_current_version_per_idea` (partial), `version_number_is_positive` | `IdeaSubmissionVersion` | The frozen copy is identified, and at most one version per idea is the current one — the backstop for two submissions racing to be official |

`idea_go_ahead_only_when_ready` names `ready_for_implementation` **and**
`automation_proposal` explicitly rather than testing "not before ready". A
constraint that passes by exclusion stops being a fact about the go-ahead the
moment a fourth state is added, and naming only the first state would have made
the lifecycle's own hand-off unsatisfiable the moment it was taken.

`Idea.category` is `PROTECT`, not `CASCADE`: an idea that has been submitted
and reviewed must not be erased because someone tidied up the category list.
Retirement (`is_active=False`) is the supported operation; deletion is only
for a category that was never used.

`Idea.organization` and `Idea.team` are `CASCADE`, and so are
`Comment`/`Vote`/`Attachment`/`IdeaTransition`/`IdeaSubmissionVersion`'s `idea`:
a row that exists only inside a tenant cannot outlive the tenant, which is the
same rule `Membership` follows in Sprint 1. `IdeaTransition.actor`,
`IdeaSubmissionVersion.submitted_by` and `owner_go_ahead_by` are `PROTECT` -
users are deactivated, not deleted, and an audit fact must not disappear with an
account.

### Indexes

Django adds an index per foreign key by default. Four of `Idea`'s FKs lead a
composite index below, so a single-column index on them would be a strict
prefix of an existing index and is switched off (`db_index=False`) - every
write to an idea would otherwise update an index PostgreSQL could never choose
over the composite one. `ideas/tests/test_models.py::TestIdeaIndexes` asserts
both the index set and this, so the two cannot silently diverge.

| Index | Columns | The query it serves |
| ----- | ------- | ------------------- |
| `ideas_org_created_idx` | `organization, created_at DESC` | The organization idea feed, newest first. The most common read. |
| `ideas_org_status_idx` | `organization, status` | A tenant-scoped board filtered by state - the organization review queue is the first consumer. Needed separately: a status filter is not a prefix of the index above, so without it PostgreSQL scans every idea in the tenant. |
| `ideas_team_created_idx` | `team, created_at DESC` | A team's ideas, newest first. Leads on `team` for the same reason the organization index leads on `organization`: it is the tenant filter every team-scoped read includes. |
| `ideas_author_status_idx` | `author, status, created_at DESC` | The author's action list - "my drafts", "changes requested", "reports awaiting my confirmation". `ideas_author_created_idx` cannot serve it: it has no status column, so the filter would scan that author's whole history. |
| `ideas_category_created_idx` | `category, created_at DESC` | Browsing one category, newest first. Leads on the category so a category page is a single index range rather than a filter over the table. |
| `ideas_vis_created_idx` | `visibility, created_at DESC` | Visibility-filtered discovery, newest first. The ordering is part of the access path, not a sort bolted on afterwards. |
| `ideas_author_created_idx` | `author, created_at DESC` | "My ideas" - drafts, submissions, history. |
| `comments_idea_created_idx` | `idea, created_at` | One idea's discussion, oldest first. The FK index cannot order the result. |
| `attachments_idea_created_idx` | `idea, created_at` | One idea's files, oldest first. |
| `ideas_trans_idea_created_idx` | `idea, created_at` | One idea's lifecycle history, oldest first. |
| `versions_idea_version_idx` | `idea, version` | The submission history, oldest version first, and the "which version is current" probe the review workspace opens with. |

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
create_idea_in_context(user, data, *, submission_context, organization_id, team_id) -> Idea
update_idea(user, idea_id, data) -> Idea
submit_idea(user, idea_id) -> Idea          # -> SUBMITTED_TO_ORGANIZATION or SUBMITTED
submit_to_platform(user, idea_id) -> Idea    # ORGANIZATION_CONFIRMED -> SUBMITTED
add_comment(user, idea_id, content) -> Comment
update_comment(user, comment_id, content) -> Comment
delete_comment(user, comment_id) -> None
vote_for_idea(user, idea_id) -> Vote
remove_vote(user, idea_id) -> None
upload_attachment(user, idea_id, uploaded_file) -> Attachment
delete_attachment(user, attachment_id) -> None
```

`create_idea_in_context` is the one way an idea comes into existence; the
original `create_idea(user, organization_id, data)` survives as a delegation to
it with the organization spelled out, because the `createIdea` mutation's first
signature and the S2 tests call that. Both go through the same validation, so
the older entry point gains the context rules rather than bypassing them.

`update_idea` and `submit_idea` require the author and per-context tenant
standing; `vote_for_idea` must be idempotent at the service level and enforced
at the database level, and a user may not vote on an idea they cannot read.

**`submit_idea` is one operation that resolves its own target.** It asks
`lifecycle.submission_target`, which reads the idea's context and returns
`SUBMITTED_TO_ORGANIZATION` for an organization idea and `SUBMITTED` otherwise.
That is the point: the author does not choose a workflow, they choose *who they
are submitting with*, and the platform decides what "submit" means. The same
call serves a resubmission after either track's changes request, because both
resolve through the lifecycle's `RESUBMISSION_TARGET`.

`submit_to_platform` is named separately **because it is a genuinely different
act, not a variant of the same one**: the organization has already said this is
what it wants to submit, and now the *owner* is the one choosing to submit it.
A separate name is what stops a reader of the file assuming "submit" covers
both.

**As implemented (S2-002).** `create_idea`, `update_idea` and `submit_idea`
ship, with `IdeaInput` carrying the four writable content fields and nothing
else. `add_comment`, `update_comment` and `delete_comment` joined them in
S2-005; `vote_for_idea` and `remove_vote` joined them in S2-006; and the
context-aware pair above arrived with the submission-context phase. Nothing in
this list is stubbed.

### Votes (implemented, S2-006)

The S2-001 `Vote` model is used as designed — one row per `(user, idea)`, no
value, no weight, no downvotes — and `unique_vote_per_user_idea` is the final
integrity boundary.

| Operation | Requires |
| --------- | -------- |
| `vote_for_idea(user, idea_id)` | An active user who may **read** the idea. **No lifecycle gate** |
| `remove_vote(user, idea_id)` | The same, and only ever removes the caller's own vote |

**The one-user-one-vote invariant, twice.** A service-level check answers the
common case — a double-clicked button, a retried request, a client replaying on
a timeout — and the database constraint answers the one a check-then-insert
cannot: two *simultaneous* requests that both read "no vote" and both try to
insert. The constraint is the boundary because it is the only thing that cannot
be raced. So the loser of that race catches `IntegrityError`, reads the row the
winner inserted, and **returns it as a success** — the state the caller asked
for does now exist, and an error would be a worse answer than the truth. An
`IntegrityError` with no row behind it is re-raised rather than swallowed, so a
genuine fault is not dressed up as a success.

**Idempotency, chosen deliberately and asymmetrically.** Voting twice is a
success (the reader already got what they asked for). Withdrawing twice is a
success (a toggle double-clicked). Withdrawing a vote that was never cast is a
success. All three leak nothing, because "no vote of mine" is the same fact
whether the row was never written or was written and removed.

The one place idempotency does **not** apply is the readability gate: an idea
the caller cannot read is *refused*, not silently accepted. Otherwise the
idempotent branch would turn "unreadable idea" into a success and quietly
confirm the operation is available there.

**No lifecycle gate, and that is a decision rather than an omission.** This
document's rule for votes is that "a user may not vote on an idea they cannot
read" — full stop, with no status condition. So a `REJECTED` idea can still be
voted on, and an `APPROVED` one handed to the opportunities track most
certainly can. The reason it differs from S2-005's discussion rule
(`DISCUSSION_CLOSED_STATUSES = {REJECTED}`): a comment is participation in a
*decision*, and a decision that is finished has nothing left to discuss; a vote
is a statement of interest in the *idea*, which is a thing that outlives its own
review state. Closing discussion on rejection while accepting votes on it is not
an inconsistency — they answer different questions about the same object — but it
reads like an oversight, so it is written down here and pinned by
`test_a_rejected_idea_can_still_be_voted_on`.

**Vote state is per-reader and authorization-aware.** `IdeaVoteState` reports
`voteCount` (global) and `viewerHasVoted` (about the caller), because the two
answer different questions and only the second is reader-specific. No vote
operation ever reports either for an idea the caller cannot read: reading is
resolved through `get_idea` first, so an unreadable idea never becomes
readable enough to have a count.

**Counts are computed, never stored.** No denormalized column, no cache, and
`COUNT`/`EXISTS` per read. A stored count would need invalidation on vote,
un-vote, and idea deletion, and would be wrong the moment one of those failed.

### Reading vote counts without an N+1

`selectors.annotate_vote_state` adds two correlated subqueries —
`Coalesce(Subquery(count), 0)` and `Exists(...)` — to the queryset
`list_discoverable_ideas` already fetches. A page of 50 ideas therefore costs
the same three queries it cost in S2-004: the membership lookup, the
`COUNT(*)`, and the page. Django does not carry annotations into `.count()`,
which is pinned, so the count query is untouched and never evaluates a
subquery per row.

`Coalesce` is load-bearing and not decoration: a `COUNT` subquery over an empty
group returns SQL `NULL`, not `0`, and `NULL` into a non-nullable `Int!` is a
GraphQL error. An idea nobody has voted on is the overwhelmingly common case,
so without it the common case fails to serialize.

`IdeaType.from_model` reads those annotations when present, and computes the
two numbers when it is handed an unannotated `Idea` (a single idea, or the row
a mutation just wrote) — two queries for one object, which is why the *list*
path is annotated rather than the single path. A fallback that defaulted to `0`
without asking would show a wrong count rather than an obviously missing one.

### Comments (implemented, S2-005)

A comment has no tenancy, no visibility and no state of its own, so every
question about one is a question about the idea it hangs from.

| Operation | Requires |
| --------- | -------- |
| `add_comment(user, idea_id, content, parent_id=None)` | An active user who may **read** the idea, and an open discussion. With `parent_id`: a readable, top-level comment on that same idea |
| `update_comment(user, comment_id, content)` | The **author** of the comment, and still being able to read its idea |
| `delete_comment(user, comment_id, None)` | The **author** of the comment, and still being able to read its idea |

Three decisions, and why each went the way it did:

- **Readability, not membership.** `add_comment` requires being able to read
  the idea rather than being a member of its organization. That is the same
  rule `docs/ideas-domain.md` set for voting ("a user may not vote on an idea
  they cannot read"), and it is what lets anybody on the platform answer a
  `PUBLIC` idea without first being made a member of someone else's
  organization. `PUBLIC` means platform-readable; making it read-only to
  outsiders would be a different visibility tier.
- **No elevated path.** There is no administrator, moderator or Owner who may
  edit or delete somebody else's comment. `organizations.authorization` has no
  capability meaning "moderate a discussion" to check, and inventing one here
  would be the second authorization system S2-001 ruled out. The idea's own
  author cannot do it either: ownership of an *idea* is not ownership of the
  discussion under it.
- **No membership re-check on edit or delete**, unlike `update_idea`. Editing
  an idea is a write *into a tenant*, so leaving that tenant has to end it; a
  comment never was one — it follows the idea's own visibility — so the same
  gate would strand a comment on a `PUBLIC` idea that its author may still read
  after leaving an organization.

**Refusals never confirm existence.** An unknown comment id, somebody else's
comment and a comment on an idea the caller may not read all answer
`"Comment is unavailable."`; an unknown idea and an unreadable one both answer
`"Idea is unavailable."` A comment id is therefore not an oracle for which
ideas exist, and an idea id is not an oracle for which comments exist.

**Content** is stripped at the ends, has CRLF folded to LF, and is otherwise
untouched: internal whitespace is somebody's content, and collapsing it would
destroy the indentation of a pasted code block. A blank or whitespace-only
comment is refused, and so is one over `MAX_COMMENT_LENGTH` (2000) — **refused,
never truncated**, because truncating would store text the author did not write
and report success. `MAX_COMMENT_LENGTH` is a judgement call of the same kind
as `MIN_DESCRIPTION_LENGTH`, and it is a module constant so changing it is a
visible decision. The model's own `clean()` refuses a contentless row as well;
note that `bulk_create` and `QuerySet.update` bypass `save()` and therefore
bypass it, the same gap that made S2-002 add database CHECK constraints for
`status` and `visibility` on top of `choices`.

**Ordering** is `created_at, pk` — chronological, because a discussion is read
in the order it happened and the opening comment is what the rest answers. The
`pk` tie-breaker is not decoration: `created_at` is microsecond-resolution, so
two comments written in the same instant would otherwise come back in an
arbitrary order and a page boundary could show one twice and skip another.
`Comment.Meta.ordering` is `['created_at']` alone, so the tie-break is stated in
the selector.

**Replies** are a nullable self-reference, `Comment.parent`, not a second table
and not a `parent_id` string. Three rules, all of them refusals rather than
repairs:

- **One level.** `Comment.clean` refuses a parent that is itself a reply, so a
  thread is a comment and its replies and there is no depth to store, bound or
  render. A deeper thread would need a depth field, a recursive read, and a rule
  about when to stop — and would read worse than the same conversation with the
  replies one indent in.
- **Same idea.** A reply is checked against the already-authorized idea, so a
  comment cannot be quoted across from another idea's thread. `clean` repeats it
  for the paths that do not go through the service.
- **Readable by the caller.** `add_comment` resolves `parent_id` through the same
  selector as every other comment read. Without it, a reply could hang off a
  comment on a `PRIVATE` idea and its own text would be visible to people who
  were never shown the thing it was about.

`parent` is `CASCADE`, so deleting a comment takes its replies with it. The
alternative — promoting a reply to top-level — would republish somebody's words
as though they had been their own. The consequence, which is a rule and not an
accident: an author can remove a whole thread by removing its first comment, and
a reply to a reply does not exist.

**Reading** is unaffected by all of this. `comments(ideaId)` still returns one
chronological page; `parentId` on each comment is what lets a client group the
page it already has. There is deliberately no nested `replies` selection: it
would be a second fetch of rows already in that page and free to disagree with
it. Because a page can begin mid-thread, a client must still be able to render a
reply whose parent it has not loaded, rather than dropping it.

**Paging** reuses `ideas/pagination.py` unchanged, including the default of 20
and the maximum of 50: a discussion has no more reason to differ from the list
it hangs under than to have its own conventions. An idea the caller may not
read is an **empty page**, not an error — the same answer as for an idea that
does not exist.

**The discussion-state rule** lives in `ideas.lifecycle` next to the transition
matrix it is derived from: `DISCUSSION_CLOSED_STATUSES` is
`{REJECTED}`, and `discussion_is_open(idea)` is the predicate. `REJECTED` has
no outgoing transition, so it is a state the lifecycle cannot leave and a
comment there has no future move to inform. `AUTOMATION_PROPOSAL` is terminal
in this app too and stays **open** — being terminal here is not the same as
being finished with, and closing a handoff would cut off the conversation it
invites. `DRAFT` is open as well: a draft is the author's working copy, and a
colleague asking a question while it is being written is ordinary. Retracting
or editing a comment already written stays available in a closed discussion,
because taking something back is not participating in the discussion.

Two decisions the implementation had to make that this document did not
anticipate:

- **"What must be filled in to submit" is a real rule, and it is a threshold
  rather than a boolean.** A title, a description of *at least 20
  characters*, and a category. The description minimum is the one number in
  this domain that is a judgement call rather than a consequence of the
  schema: a blank description cannot be acted on by a reviewer, and a
  one-word description is not a problem statement. It is a module constant, so
  changing it is a visible decision.
- **A submitted idea is no longer editable, by anybody** - until a reviewer
  sends it back. `update_idea` refuses everything except `DRAFT`,
  `CHANGES_REQUESTED` and `ORGANIZATION_CHANGES_REQUESTED` (see
  [Lifecycle](#lifecycle)). `submit_idea` is the lifecycle's author move and
  accepts `DRAFT → SUBMITTED_TO_ORGANIZATION | SUBMITTED`, the two
  resubmissions, and `ORGANIZATION_CONFIRMED → SUBMITTED`; anything else is
  refused.

### Attachments (implemented, S2-007)

The S2-001 `Attachment` model is used exactly as designed - see [Storage
boundary](#storage-boundary) for the object-storage side of this. What S2-007
adds is the operations, the authorization rule, and the file itself never
touching PostgreSQL or GraphQL.

| Operation | Requires |
| --------- | -------- |
| `upload_attachment(user, idea_id, uploaded_file)` | The idea's own **author**, with standing in the idea's tenant - an organization membership, a team membership, or nothing for an individual idea - not merely being able to read the idea |
| `delete_attachment(user, attachment_id)` | The same, resolved through the attachment's idea |
| Listing (`attachments(ideaId)`) and download | Anyone who can **read** the idea - the looser, comment/vote-shaped rule |

**Upload and delete require authorship, not readability - the one rule this
domain had not yet had to state.** Every prior write in this app (comments,
votes) followed *readability*, deliberately, because participation and
interest both follow what a reader is shown. Evidence is different: it is
closer to editing the idea than to responding to it, so `_load_attachable_idea`
in `ideas/services.py` requires the same two conditions `update_idea` does -
authorship and per-context tenant standing - while deliberately **not**
requiring `update_idea`'s `DRAFT`-only lifecycle gate, because evidence
accumulates throughout review, not only while an idea is still being written.
This is the vote shape (readability/authorship with no status condition)
applied to
a *write* rule instead of a read one, and it is written down here for the
same reason S2-006's no-lifecycle-gate decision was: it is easy to "fix"
into a copy of a stricter neighbor's rule, and the fix would be wrong.

**The upload endpoint's answers (S2-008).** `POST /ideas/<id>/attachments/`
authenticates and authorizes *before* it reads `request.FILES` (which is what
makes Django parse the multipart body), so a caller who could never upload is
refused without the application processing the file:

| Outcome | Status |
| ------- | ------ |
| No valid token (anonymous, expired, or a deactivated account) | 401 |
| Idea unknown, unreadable, or not the caller's own | 404 - one answer, so the endpoint is not an existence oracle |
| The author, without standing in the idea's tenant any more | 403 |
| No file, or a file that fails validation (type, content, size, name) | 400, with `field: "file"` |
| The storage backend failed | 502 |
| Stored | 201 |

A field-scoped refusal is always 400, never 404: before S2-008 validation and
storage failures fell through to `IdeaError`'s default `'forbidden'` reason
and were reported as a missing idea. A body-size ceiling on the bytes the web
server reads off the socket is a reverse-proxy concern and is not configured
in this repository.

**The client never chooses the storage key, the content type, or a directory
component of the filename.**

- The storage key (`ideas.storage.generate_storage_key`) is built from the
  idea's own id and a random token - never from anything the client sends -
  which is the actual path-traversal defense, not a validation the key is
  checked against afterwards.
- The recorded content type (`ideas.attachments.canonical_content_type`) is
  derived from the file's validated extension and confirmed against its own
  leading bytes; the browser's `Content-Type` claim on the upload is never
  read as data. This is "do not trust the browser-provided MIME type" as a
  structural fact rather than a check that could be forgotten.
- The display filename (`ideas.attachments.safe_display_filename`) is
  reduced to its last path segment on both separator styles before anything
  else touches it, so a client-supplied `../../etc/passwd.pdf` is stored and
  shown as `passwd.pdf` and never becomes a path anywhere in this stack.

**Two independent checks gate what file type is accepted**, because they
defend against different lies: an explicit extension allow-list (PDF; PNG,
JPEG, GIF, WebP; CSV, TXT; DOC, DOCX, XLS, XLSX - deliberately nothing
executable or script-shaped), and the file's own leading bytes checked
against the signature the extension claims (`ideas.attachments.
validate_content`). A renamed executable is caught by the second check even
when the first would have let it through; a set of "dangerous" signatures
(`MZ`, ELF, a shebang, Mach-O/Java) is refused for *every* extension,
including ones with no signature of their own to check (CSV, TXT), as
defense in depth. Size is capped by `ATTACHMENT_MAX_UPLOAD_BYTES` (50 MB by
default), checked against the upload's own measured size, never a
client-supplied header.

**Not idempotent**, on both operations - matching `add_comment`/
`delete_comment`'s shape rather than the votes' toggle shape. Uploading twice
produces two attachments (there is no "this is the same evidence" identity to
collapse them by); deleting twice refuses the second time, because the
attachment named by the second call is no longer there. The one place this
domain still had to decide a race: a database failure *after* a successful
storage write is met by deleting the just-written, now-orphaned object
(`ideas.services.upload_attachment`); a storage failure *after* a successful
database delete is logged and left as an orphaned object rather than
resurrecting a database row the caller was already told is gone
(`ideas.services.delete_attachment`) - the database, not the storage
backend, is this domain's source of truth for whether an attachment exists.

**GraphQL is metadata and lifecycle only; the bytes are HTTP.** `attachments
(ideaId)`, `attachment(id)` and `deleteAttachment(id)` follow this schema's
existing conventions exactly - `AttachmentType` carries `uploaderId`, never an
embedded uploader, for the same reason `CommentType` carries `authorId` alone.
There is no `addAttachment`/`createAttachment` mutation and never will be:
binary content does not fit a JSON-in/JSON-out GraphQL operation, and forcing
it through one would mean base64-encoding a file into a string field,
inflating it by a third for no benefit. `ideas/views.py` is the HTTP surface
instead - `POST /ideas/<idea_id>/attachments/` to upload,
`GET /ideas/<idea_id>/attachments/<attachment_id>/download/` to stream back -
authenticated by the same `Authorization: Bearer` header every GraphQL
request carries, through the same `identity.authentication.
get_authenticated_user`. `AttachmentType.downloadUrl` is a relative path
into that second endpoint, not a full URL and not a signed one: nothing
about the path is secret, because the endpoint re-authenticates and
re-authorizes the request itself when the client fetches it, from the same
header - see [Storage boundary](#storage-boundary) for why a signed URL is
not yet the right call at this project's scale.

**Download is never rendered inline.** `Content-Disposition: attachment`
unconditionally, for every content type this domain accepts, images
included. An uploaded file is never trusted content, and an HTML or SVG file
rendered inline in this app's own origin would be exactly the injection this
line exists to refuse; forcing a download is the one rule that makes an
allow-list of "safe to render inline" content types unnecessary.

## Selector layer (implemented, S2-002; discovery in S2-004)

Reads live in `ideas/selectors.py`, separate from writes, so the visibility
and tenancy rules are applied in exactly one place:

```python
get_idea(user, idea_id) -> Idea | None          # None for anything unreadable
get_idea_for_update(user, idea_id) -> Idea | None
list_discoverable_ideas(user, filters, *, offset, limit) -> Page[Idea]
list_ideas(user) -> QuerySet[Idea]              # already visibility-filtered
list_organization_ideas(user, organization_id) -> QuerySet[Idea]
list_team_ideas(user, team_id) -> QuerySet[Idea]
list_own_ideas(user) -> QuerySet[Idea]
list_active_categories() -> QuerySet[Category]
list_idea_transitions(user, idea_id) -> list[IdeaTransition]
can_view_idea(user, idea) -> bool
platform_reviewer_filter(user) -> Q
```

`list_ideas`, `list_organization_ideas` and `list_team_ideas` return an
already-filtered `QuerySet`, not a list, so a caller cannot forget the filter by
forgetting to apply it: the only way to get ideas is through a selector that
applied it. The filter is built from `get_membership` and
`teams.authorization.is_member_of`, so it is the same membership Sprint 1
authorizes with — and a tenant the caller does not belong to yields an empty
queryset, which is also the answer for a tenant that does not exist.

`list_own_ideas` is deliberately *not* visibility-filtered: an author can
always read their own idea, whatever state it is in. It is the one scope
rather than a filter in the module, and the reason it is safe is that the
author set is exactly the caller's own id.

### Discovery (implemented, S2-004; extended for the contexts)

`list_discoverable_ideas` is the single read path for browsing, and `ideas`,
`organizationIdeas` and `teamIdeas` all go through it. It is the only place the
visibility filter, the discovery filters and pagination meet, so there is no
second queryset for an unfiltered idea to escape through.

```python
filters = selectors.IdeaFilters(
    organization_id=None,   # narrows to a tenant the caller belongs to
    team_id=None,           # likewise, for the team's own feed
    category_id=None,
    status=None,
    search=None,
    submission_context=None,
    visibility=None,
    mine=False,
```

**Every filter can only remove rows, never add them**, because
`_visibility_filter(user)` is applied *before* any of them. That ordering is
the whole design, and `test_every_filter_together_still_excludes_what_it_should`
is the test that says it: an idea matching the tenant, the category, the
status and the search exactly is still not returned when it is private.

`submission_context`, `visibility` and `mine` were added with the contexts, and
all three read like a grant without being one — which is the test they have to
pass:

- `visibility=PUBLIC` returns only ideas the caller could already see *and*
  that happen to be public.
- `mine=True` is the caller's own id rather than an id the caller chose, so it
  cannot become a way of asking for somebody else's rows.
- **`author_id` as a caller-supplied value is deliberately absent**, for
  exactly the reason `mine` exists instead.

`organization_id` and `team_id` look like the same problem and are not: each
can only ever narrow, and both are checked against the caller's own membership
before they filter anything. An organization or team the caller does not belong
to and one that does not exist produce the *same* empty page, so neither
argument can be used to probe which ids are real.

An unusable filter value (a category that is not an id, a status that is not
in the enum) yields an empty page rather than being ignored. A client that
asked for category "abc" and received the unfiltered list would read that as
"no ideas in this category" when it means "that category does not exist".

**Search** is a case-insensitive substring match over `title` and
`description` only, via `icontains`, which binds the term as a parameter.
`problem_statement` and `proposed_solution` are deliberately not searched:
they are not exposed by the API, and searching a field the UI does not show
would let a reader confirm the presence of a phrase in text they cannot
otherwise see. The problem-story answers are exposed but not yet searched -
widening search to them is a product decision for later, not a side effect. Django escapes the pattern metacharacters, so a reader
who types `%` searches for the character rather than matching everything.

**Ordering** is `-created_at, -pk`. The tie-breaker is not decoration:
`created_at` is microsecond-resolution, so two ideas filed in the same instant
would otherwise come back in an arbitrary order and a page boundary could show
one row twice and skip another. A total order is what makes `offset` a correct
way to page.

**Categories** are platform-wide reference data, readable without signing in
so a picker can render before somebody decides to sign in.
`list_active_categories` filters `is_active=True` and orders by name.
Retirement is not deletion (`Idea.category` is `PROTECT`, so a used category
can never be deleted): a retired category keeps its history and disappears
from the picker, but an idea filed under it before it was retired is still a
real idea a reader may see, and still matches that category as a filter. A
retired category cannot be used to reach a *private* idea, because visibility
is filtered first.

### Pagination (implemented, S2-004)

`ideas/pagination.py` holds a small, domain-agnostic offset helper: `Page[T]`,
`clamp_limit`, `clamp_offset`, `paginate`, `empty_page`, default 20, maximum
50.

- **Offset, not cursor.** A cursor is the better answer for a large,
  append-heavy, continuously-ordered dataset. This is not that dataset: the
  ordering is total, the result set is small enough that a count is cheap, and
  the UI needs "page 2 of 7" more than it needs a stable cursor across an
  insert. If that changes, this module is the one place, and `Page` can grow a
  `cursor` field alongside `offset`.
- **Clamped, not rejected.** A client asking for 1000 rows is asking for a
  page, not for an error. The server enforces the ceiling, and `page_info`
  echoes `offset` and `limit` back *as applied* so a client can see it.
- **A float is refused, not truncated.** `3.5` is not a page size somebody
  meant to send, and rounding it invents an answer to a question the caller
  did not ask.
- **Three queries per page**: the membership lookup the visibility filter
  needs, the `COUNT` behind `total_count`, and the page fetch. The count is a
  deliberate trade - the cheaper alternative is to fetch `limit + 1` rows and
  infer "has more" from a short page, but that cannot answer "showing 1-20 of
  137", which is the number people use to decide between refining a search and
  paging.

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

As built:

| Queries | Mutations |
| ------- | --------- |
| `idea(id)` | `createIdea(input)` — `CreateIdeaInput` carries `submissionContext` and the matching tenant id |
| `ideas(filters)` | `updateIdea(input)` |
| `organizationIdeas(organizationId, filters)` | `submitIdea(id)` |
| `teamIdeas(teamId, filters)` | `submitToPlatform(id)` — the owner's handoff of a confirmed idea |
| `categories` | `giveGoAhead(id)` — the owner's confirmation (`ideas.go_ahead`) |
| `comments(ideaId)` | `addComment(input)` — `parentId` makes it a reply |
| `ideaTransitions(ideaId)` — the read-only lifecycle history | `updateComment(input)` / `deleteComment(id)` |
| `attachments(ideaId)` / `attachment(id)` — implemented in S2-007 | `voteIdea(id)` / `removeVote(id)` — implemented in S2-006 |
| | `deleteAttachment(id)` — implemented in S2-007 |
| | `transitionIdea(id, toStatus)` — author moves and the hand-off; refuses the review-owned pairs |

`IdeaType` gained, with the platform track: `submissionContext`,
`organizationId` (**nullable**), `teamId`, `tenantName` (derived, so it cannot
disagree with the row), `platformLockedAt`, `platformVersion`,
`platformApprovedAt`, `ownerGoAheadAt`, `state` (the whole
`ideas.states` summary, resolved per viewer), and the per-viewer capability
fields `viewerCanEdit`, `viewerCanStartReview`,
`viewerCanStartOrganizationReview` and `viewerActiveReviewId`.

**No author name on the idea type.** `authorId` is an id, never a nested user:
a `PUBLIC` idea is readable platform-wide, so a nested user would publish a
member's email address with it. The cost is honest and visible — an individual
idea's card says "you" or "the person who filed it" rather than naming its
author.

`organizationId` being nullable is the client-visible half of the context work:
a client that assumes it is always present is a client that will show an empty
organization to somebody who filed an idea entirely on their own.

There is deliberately no `addAttachment`/`createAttachment` mutation, in
S2-007 or ever: binary content does not fit a JSON-in/JSON-out GraphQL
operation. Uploading and downloading an attachment's bytes are the one
HTTP surface Ideas has beyond `/graphql/` - see [Attachments](#attachments-implemented-s2-007)
and `ideas/views.py`.

## Frontend boundary (implemented, S2-002/S2-004)

```
frontend/src/features/ideas/
    api/          ideasApi.ts - the documents and typed request functions
    components/   IdeaForm, IdeaList, IdeaFiltersBar, IdeaPagination,
                  IdeaDiscussion, IdeaVoteButton, IdeaAttachments,
                  IdeasWorkspace, IdeaContextDialog, IdeaOwnership,
                  IdeaDetailPage, IdeaStory
    hooks/        useIdeaDiscovery, useCategories, useComments, useIdeaVotes,
                  useAttachments
    utils/        lifecycle.ts (labels), ownership.ts, savedIdeaNotice.ts
```

The data loading moved into `hooks/` in S2-004, and for a specific reason
rather than a general one. `IdeaList` had to be re-fetchable from two
directions that are not the same thing - the filters changed, or something was
written - and it also had to keep the reader's place across the second. A
component that owned its own fetch plus a remount-to-reload trick could not do
that without throwing the filters away, so `useIdeaDiscovery` owns the request
and the workspace owns the state. The app still has no React Query and one
screen still has one list.

Three frontend decisions worth stating, because each is the client half of a
backend rule rather than a free choice:

- **The search box is debounced** (`useDebouncedCallback`, 300ms), so a reader
  typing a word sends one query rather than one per keystroke. The text in the
  box and the filter in effect are different values: the box knows what was
  typed, and only a settled term is asked for.
- **A narrowing filter returns to page 1.** Written once, in the workspace,
  because it needs the filters and the page in one place - and it is what stops
  "showing 40-60 of 12" from ever being rendered.
- **The client never filters a list to decide what somebody may see.** It
  renders the server's page, and the three empty states ("nothing here",
  "nothing matches", "nothing on this page") are kept apart because they are
  different facts.
- **A discussion is a disclosure inside the card, fetched when it is opened.**
  The ideas page is a *list*; fetching twenty discussions on mount would be
  twenty requests for content nobody asked to read. At most one is open at a
  time, and the open one cannot outlive the results it belongs to.
- **A comment write updates the thread in place and never re-fetches the
  ideas.** A discussion is append-only in reading order, so a post splices onto
  the end and adjusts the total; the reader keeps their filters and their page.
  Two submissions in one tick are dropped by a ref rather than by state, because
  state is not readable synchronously from a click handler and a
  double-posted comment is a duplicate somebody has to delete by hand.
- **`idea.discussionOpen` decides whether the composer is drawn**, reported by
  the server from the same rule `createComment` enforces — the same reason
  `availableTransitions` exists, so no lifecycle rule is copied onto the
  client. `voteCount`/`viewerHasVoted` are reported the same way, and
  `useIdeaVotes` adds no local arithmetic on top.
- **The vote control's state is keyed by idea id**, so a response that arrives
  late writes the idea it was for and cannot land on whichever idea is on
  screen. **No optimistic update**: the count rendered after a vote is the one
  the mutation returned, which already accounts for other people's concurrent
  votes, so there is nothing to roll back and a failure leaves the number
  exactly where it was. The in-flight guard is a `useRef`, not module state,
  because state cannot be read synchronously from a click handler.
- **Attachments are a disclosure too, fetched only while open** - the same
  reasoning as the discussion, and independent of it: a reader can open
  either, both, or neither. `useAttachments` splices an upload onto the end
  of the loaded list and filters a delete out of it, the same append/remove
  shape `useComments` uses, and never re-fetches the ideas list either way.
  Upload and delete controls render only when `idea.authorId === user.id` -
  a courtesy, since the server enforces authorship regardless - and every
  reader who can see the section at all (because they can read the idea) is
  offered download. `uploadAttachmentRequest`/`downloadAttachmentRequest` are
  the two functions in `ideasApi.ts` that call `fetch` directly instead of
  going through `graphqlClient`, because a multipart upload and a streamed
  download are not GraphQL operations - see `ideas/views.py`'s docstring for
  the backend half of that split. Both attach the same bearer token
  `graphqlClient` does, read fresh from `tokenStore` on every call.

This mirrors `features/organizations/` exactly: `api/` holds the documents and
the typed request functions, `context/`-free hooks read them, and
`components/` are presentational.

- **Routes** live in `src/app/routes.tsx`, which reserves `/app` for
  authenticated routes. Ideas routes are `/app` children so they inherit
  `RequireAuth`; they mount inside the existing `OrganizationProvider`:
  `/app/ideas`, `/app/ideas/:ideaId`, `/app/ideas/new`,
  `/app/ideas/:ideaId/edit`, plus `/app/ideas/:ideaId/report` for the platform
  review report and `/app/organizations/:organizationId` and
  `/app/teams/:teamId` for the tenant-scoped idea panels.
- **The context is chosen before the form opens, and it is never changed
  afterwards.** "File a New Idea" from the ideas list opens
  `IdeaContextDialog` first: *where does this idea belong?* - individual, team
  or organization - and choosing team or organization offers **only the
  tenants the caller may file for**, which the server supplied rather than the
  client filtering a list it does not have. From a team page or an organization
  page, "Create Idea" is a plain link to `/app/ideas/new?context=…&team=…`
  because the owner is already decided. The page then carries a locked context
  banner above the form, so it stays put on every one of the eight steps: it is
  not a field the author can change halfway through, because an idea whose
  owner moves under it is an idea whose reviewers change too. **The URL carries
  the ownership decision, not React state** - `?context=team&team=9` - so a
  reload does not raise a "which context is it again?" question, and a URL
  naming a team the reader is not on cannot put an idea into it.
- **Ownership and audience are two badges, never one.** `IdeaOwnership` renders
  the context ("Your idea", "Team · Payments", "Organization · …") and the
  audience ("Public", "Team only", "Only you") side by side on cards, in both
  lists and in the detail header, because after the `TEAM` split they are
  genuinely different facts and a reader of a list cannot be allowed to
  confuse them.
- **Organization context** comes from `useOrganization()`
  (`features/organizations/context`) for the organization's own screens. It is
  no longer the source of the `organizationId` argument everywhere, because an
  ideas screen is no longer necessarily about one organization.
- **Authorization information** comes from the same context's
  `hasPermission(code)` and `activeMembership`. The frontend uses it to decide
  what to *offer* (hide "submit" on an idea you did not author). It is never
  what decides what is *visible* - the server has already filtered.
- **The detail page exists and is linked from three places.** `/app/ideas/:id`
  used to 404 from every card action; it now renders the ownership header, the
  state summary the server resolved (`state.primaryActionLabel` is the button
  text, and it is empty when there is nothing for this viewer to do), the
  review history and the go-ahead confirmation.
- **Visibility** is represented in the UI as a badge plus a muted card style,
  not as a client-side filter. `visibility` travels on the idea type so a
  reader can see who an idea is shared with, which is information people need
  before commenting.

## Sprint scope

| Sprint | Scope |
| ------ | ----- |
| S2-001 (this) | `ideas` app, five models, migration, admin, model tests, this document |
| S2-002 | `createIdea`/`updateIdea`/`submitIdea`, selectors, the GraphQL schema, the frontend feature module — implemented. `addComment`/`voteIdea` were scoped into S2-002 in this document and are **not** implemented |
| S2-003 | `transitionIdea` and the transition matrix, fail-closed `DEPARTMENT` — implemented |
| S2-004 | `list_discoverable_ideas`, `IdeaFiltersInput`, `IdeaPage`, `ideas/pagination.py`, category/search/status filters, the discovery UI — implemented |
| S2-005 | `add_comment`/`update_comment`/`delete_comment`, `CommentType`/`CommentPage`, `comments`/`createComment`/`updateComment`/`deleteComment`, `IdeaType.discussionOpen`, the discussion UI — implemented |
| S2-006 | `vote_for_idea`/`remove_vote`, `IdeaVoteState`, `voteIdea`/`removeVote`, `IdeaType.voteCount`/`viewerHasVoted` (annotated, no N+1), the vote control — implemented |
| S2-007 | `upload_attachment`/`delete_attachment`, `ideas/storage.py`, `ideas/attachments.py` (file validation), `attachments`/`attachment`/`deleteAttachment`, the HTTP upload/download endpoints (`ideas/views.py`), the evidence UI — implemented |
| S2-008 | Integration and security hardening: cross-feature/two-tenant security tests, upload authenticates before reading the body, storage failures reported as 502, frontend stale-response and vote-state fixes — implemented |
| S3 | The review workflow beyond the S2-003 transitions: review queue, reviewer eligibility on `idea.review`, reasons on a review decision, editing while changes are requested, the append-only `IdeaTransition` trail — implemented; see [`reviews-domain.md`](reviews-domain.md) |
| platform track | Three submission contexts and their database constraint, the organization stage, `TEAM` visibility, frozen `IdeaSubmissionVersion`s, `ideas/states.py`, `ideas/go_ahead.py`, the platform review permission, `submitToPlatform`/`giveGoAhead`, `IdeaContextDialog`, `IdeaDetailPage` — implemented |
| later | Validation, automation opportunities, requirements, proposals, developers, projects, tasks, milestones, deployment, impact, payments, AI analysis |

Not modelled here, and not to be added under this domain: automation
opportunity entities, a developer marketplace or matching, project management,
tasks, milestones, deployment, impact analytics, payments, AI analysis,
recommendation engines, or subscriptions - and, deliberately, **any transfer of
ownership**: no write input anywhere names a new owner, so "whose idea is this"
cannot be reassigned by an API that was never designed to answer the question.
`ideas/tests/test_models.py::TestAppBoundary` asserts the app's model set, so
crossing a sprint boundary here fails a test.
