# Platform Administration Console

The administration console is the platform's **internal control centre**: a
place for platform administrators to see and carefully manage what already
exists - users, organizations, ideas, reviews, decisions and categories -
without reaching for Django Admin in normal operations.

It is not part of the customer application and it is not a way around the
customer application's rules. Everything it reads or changes goes through the
owning domain's rules; the console adds a platform-scoped permission check in
front of them and an audit record behind them.

```
Customer application                  Administration console
--------------------                  ----------------------
User -> Organization -> Idea          Platform administrator
     -> Review -> Approval              -> Users, Organizations, Ideas,
     -> (future) Opportunity ...           Reviews, Approvals, Categories,
                                           Audit trail
```

## 1. Who is a platform administrator

Platform administration is a **separate authorization concern** from
organization authorization, and the two never imply each other.

| Who | Scope | Where it comes from |
| --- | ----- | ------------------- |
| Platform administrator | The whole platform, through the console only | Django `auth.Permission` codes on `administration.AdminAuditEntry` |
| Organization Owner | One organization | The organization's system `Owner` role (`organizations.Permission`) |
| Organization Reviewer | Reviewing one organization's ideas | The `Reviewer` role (`idea.review`) |
| Normal user | Their own work and what their organizations share | Active membership |

There is no `AdminUser`, no second login and no second permission framework.
An administrator is an ordinary `identity.User`. Their platform permissions
use Django's own platform-scoped mechanism, which `User` already has through
`PermissionsMixin`: `user.has_perm`, groups, per-user grants and
`is_superuser`. The organization `Role`/`Permission` tables are deliberately
organization-scoped (every `Role` belongs to one organization), so they could
not express "every tenant" without inventing a fake organization. It also
means an organization Owner never becomes an administrator by holding a role.

### Permissions

All codes are `administration.<codename>`. Every capability other than
`access_console` is honoured **only together with** `access_console`.

| Permission | Grants |
| ---------- | ------ |
| `access_console` | The console at all: dashboard, user and organization listings and details, idea and review **metadata**, decisions, categories list, audit trail |
| `inspect_idea_content` | The content of ideas that are not `PUBLIC` (title included), their discussion, their evidence (download), and review feedback, criteria ratings and submission snapshots |
| `manage_user_accounts` | Activate and deactivate accounts |
| `manage_organization_roles` | Assign and remove an organization's roles on its memberships |
| `manage_categories` | Create, rename and retire/restore categories |

`inspect_idea_content` also gates **private message bodies** - see §3.1. It is
the console's "content a member wrote" permission, and a message is the one place
on the platform where somebody wrote something meant for named people rather
than for the platform. Everything else the console reports about teams,
invitations, conversations and notifications is metadata about the platform and
needs `access_console` alone.

An active superuser holds every permission (Django semantics). `is_staff` on
its own grants nothing here.

### Granting and revoking

```bash
python manage.py grant_platform_admin admin@example.com           # all console permissions
python manage.py grant_platform_admin admin@example.com --revoke
```

The command adds or removes the account from the **"Platform administrators"**
group, which holds every console permission. The grant is audited with no
actor, which marks a command-line operation. For narrower grants, such as a
read-only support role with `access_console` alone, use Django Admin's
group/permission screens. **The console cannot grant platform
administration.** A console that could mint administrators would turn one
compromised administrator account into many.

## 2. Security boundaries

- **The backend decides.** Every `admin*` GraphQL field calls
  `administration.
  authorization.require_admin` (through
  `administration.selectors` or `administration.services`) before touching
  data. Nothing reads a role, flag or route from the client.
- **Unauthorized reads look like "nothing here".** A caller without
  `access_console` gets `null` for single objects and an empty page for
  lists, which is the same answer the rest of the API gives to unauthorized
  reads. `adminCapabilities` is the one field that answers everybody: all
  `false` for a non-administrator.
- **No bypass.** Holding a platform permission changes nothing in the
  member-facing API. An administrator is not a member of any organization's
  ideas, cannot `startReview`, and cannot read `ideaReviews` or
  `organizationMembers` there (`administration/tests/test_access.py::TestNoBypass`).
  The console has its own read path (`administration/selectors.py`) and its
  own write path (`administration/services.py`).
- **Content is a second gate.** Without `inspect_idea_content`, a non-public
  idea is listed by its metadata with `title: null` and
  `contentRestricted: true`. Its content, comments and evidence are withheld,
  review feedback/criteria/snapshots are withheld for every idea, and search
  never matches a title the administrator could not read.
- **Nothing sensitive leaves.** No admin type declares a password, hash,
  token, session or external-identity subject. `signInMethods` names
  providers, and names `password` only as the fact that one is set. This is
  pinned by `test_reads.py::TestNothingSensitiveLeaves`.
- **Evidence downloads** use a separate endpoint,
  `GET /administration/attachments/<id>/download/`. It requires
  `access_console` + `inspect_idea_content`, records an audit entry before
  streaming, and reuses `ideas.views.attachment_file_response`, so the
  forced-download, canonical content type and `nosniff` rules are the same
  single implementation. Every refusal is a 404.
- **The frontend gate is presentation.** `/app/admin` shows a "Not
  authorized" panel to non-administrators and never mounts the console's
  pages. That is UX only.

## 3. What the console does

### Reads

| Area | Route | GraphQL | Notes |
| ---- | ----- | ------- | ----- |
| Dashboard | `/app/admin` | `adminOverview` | Users (active), organizations, ideas, ideas per status, reviews open/completed, recent lifecycle moves, recent admin actions. All live counts |
| Users | `/app/admin/users`, `/users/:id` | `adminUsers`, `adminUser` | Search, active/deactivated filter, platform-admins filter, organization filter. Memberships and roles, sign-in methods, activity counts, admin history |
| Organizations | `/app/admin/organizations`, `/organizations/:id` | `adminOrganizations`, `adminOrganization`, `adminOrganizationMembers` | Member/owner/reviewer/idea counts, roles and holders, ideas per status, paged members (active and inactive) |
| Ideas | `/app/admin/ideas`, `/ideas/:id` | `adminIdeas`, `adminIdea` | Filters: search, status, visibility, organization, category, author, created from/to. Detail: story, evidence, discussion, review rounds, lifecycle history |
| Reviews | `/app/admin/reviews`, `/reviews/:id` | `adminReviews`, `adminReview` | Open/completed, decision, organization, reviewer. Detail shows whether an open round is **stalled** (`isStalled`, computed on request) |
| Approvals | `/app/admin/approvals` | `adminReviews` (completed, verdict decisions) | Decision, round, reviewer, date, and the idea's status now |
| Categories | `/app/admin/categories` | `adminCategories` | Active and retired, with idea counts |
| Teams | `/app/admin/teams`, `/teams/:id` | `adminTeams`, `adminTeam`, `adminTeamInvitations` | Active **and** inactive member counts, owner, ideas, invitations sent. Detail adds roles with their permission codes, the full membership, and ideas by status |
| Invitations | `/app/admin/invitations` | `adminInvitations` | Both scopes, filterable by scope and status, with whether each one could still be accepted |
| Messages | `/app/admin/messages`, `/messages/:id` | `adminMessageThreads`, `adminMessageThread`, `adminThreadMessages` | Metadata only: participants, counts, last activity. Bodies need `inspect_idea_content` (§3.1) |
| Notifications | `/app/admin/notifications` | `adminNotifications` | Filterable by kind and unread, with the recipient. These are the platform's own words |
| Audit trail | Dashboard, user detail | `adminAuditEntries` | Filterable by target |

Every list is paged by the server (`ideas.pagination`, max 50 per page) and
filtered in the database. A page costs a fixed number of queries regardless
of its size, which `test_reads.py::TestQueryCounts` pins.

### 3.1 The collaboration set

Teams, invitations, messages and notifications are **read-only here**, and that
is the owning domains' position rather than an omission in this console:

- **A membership is only ever created by accepting an invitation.** There is no
  console operation that adds somebody to an organization or a team, because the
  only two facts involved - who was asked, and who accepted - are written by the
  invitation flow. An administrator who could write a membership row would be
  recording an acceptance that never happened.
- **A message belongs to its participants.** There is no delete, no edit and no
  "mark read": the read position is per participant, and a message is a record of
  what was said to named people.
- **A team is not a tenant**, so nothing here validates or approves anything on
  its behalf.

So the console's job on these four is to make them *legible*:

| Surface | What it answers |
| ------- | -------------- |
| Teams | Who writes ideas with whom, who has left (their row is kept, so a team does not re-invite them), what each team role may hold |
| Invitations | Who was invited where, by whom, and whether it could still be accepted. `isOpen` rather than `status`, because a pending invitation past its expiry is still "pending" in the database |
| Messages | That a conversation exists, who is in it, how much has been said and when it was last active |
| Notifications | What the platform told each person, and whether they opened it |

**Two redaction rules, both inherited from the idea rules rather than invented
here.** A message body needs `inspect_idea_content`; every body is then `null`
and the page says "restricted" instead of drawing an empty message. And a
**thread subject is withheld with the idea it is anchored to**, because
`messaging.services.start_thread` defaults an anchored thread's subject to that
idea's title - so a subject is sometimes an idea title, and redacting one
without the other would leave the answer in the other. Searching follows the
same rule: the subject of an anchored thread is only searchable by a caller who
may read that idea.

**`staleParticipantCount` is not an unread count.** Unread is a position per
reader and the console has no reader, so what it reports is how many
participants have not opened the thread - a fact about the platform rather than a
fictional reader's state.

**No invitation token is exposed anywhere, so there is nothing to redact.**
`Invitation` stores only the SHA-256 digest of a 32-byte `secrets` token, and
lookup is by digest; the plaintext exists in exactly one place, the email. This
is asserted against the schema itself in
`tests/test_collaboration_reads.py::TestInvitations::test_no_invitation_token_is_ever_exposed`.

### Operations

| Operation | Mutation | Permission | Rules kept |
| --------- | -------- | ---------- | ---------- |
| Deactivate / reactivate an account | `adminSetUserActive` | `manage_user_accounts` | Not your own account; only a superuser changes a superuser; deactivation revokes every refresh session and takes effect on the next request. Nothing is deleted; the account's open review becomes stalled and can be taken over through the normal review flow |
| Assign / remove an organization role | `adminAssignMembershipRole`, `adminRemoveMembershipRole` | `manage_organization_roles` | The organization domain's own rules (`organizations.services.grant_membership_role` / `revoke_membership_role`): active memberships only, the role must belong to the membership's organization, no duplicates, and **the last active Owner keeps the Owner role** |
| Create / rename a category | `adminCreateCategory`, `adminUpdateCategory` | `manage_categories` | Unique name; the slug is kept on rename. The same `ideas.Category` rows the idea form uses |
| Retire / restore a category | `adminSetCategoryActive` | `manage_categories` | Existing ideas keep their category; retired categories are no longer offered for new ideas. No delete (`Idea.category` is `PROTECT`) |

Every destructive or access-changing action is behind a confirmation dialog
that states its consequences and accepts an optional, audited reason.

## 4. Audit trail

`administration.AdminAuditEntry` is a narrow, append-only table in the same
style as `ideas.IdeaTransition`. `save()` refuses to rewrite a row,
`delete()` refuses outright, and the Django admin is read-only. Each entry
records the actor (null only for command-line operations), the action, the
result (`succeeded` / `refused`), the target type/id/label, the organization
id, the refusal message, metadata such as the reason, changed fields or
revoked-session count, and a timestamp. It never records passwords, tokens
or file content.

Recorded:

- every successful console operation, platform-admin grant/revoke and
  evidence download;
- **refusals** of account and role changes the administrator was allowed to
  attempt but a rule stopped (their own account, the last Owner), so repeated
  attempts are visible.

Callers without the permission are logged (`administration.services`
logger) rather than written to the table. Anybody can send a request, and
the table is a record of administrators.

The idea lifecycle keeps its own trail (`IdeaTransition`). The console reads
it; it does not duplicate it.

## 5. Deliberately not here

- **No review or approval override.** Completed reviews are immutable and
  review history must stay trustworthy. The console observes decisions and
  cannot create, change, withdraw or reassign a review, or move an idea's
  status. An override would need to be designed explicitly, separately and
  audited.
- **No password, token or session viewing**, and no password reset on
  someone's behalf. Users reset their own through the emailed link.
- **No deletion** of users, organizations, ideas, reviews or categories.
- **No granting of platform administration from the console** (see §1).
- **No organization editing, member invitation or removal**, and no
  platform-level settings page. The console reads invitations and memberships;
  it cannot write either (see §3.1)
- **No message or notification operations**, and no team membership changes
- **No profile photo**: `User` has none yet.
- **No future domains**: automation opportunities, requirements, proposals,
  developers, projects, payments. When Sprint 4 adds an entity, it gets a
  selector in `administration/selectors.py`, an `admin*` field in
  `administration/schema.py` and a page under `/app/admin`. The permission
  gate and audit trail are already in place.

## 6. Code map

| Layer | Where |
| ----- | ----- |
| Permissions, audit model | `backend/administration/models.py` |
| Authorization | `backend/administration/authorization.py` |
| Cross-tenant reads | `backend/administration/selectors.py` |
| Operations + audit | `backend/administration/services.py` |
| GraphQL (`admin*`) | `backend/administration/schema.py` |
| Evidence download | `backend/administration/views.py` |
| Command | `backend/administration/management/commands/grant_platform_admin.py` |
| Collaboration reads | `administration/selectors.py` (teams, invitations, messages, notifications) |
| Tests | `backend/administration/tests/`, incl. `test_collaboration_reads.py` |
| Frontend API | `frontend/src/features/administration/api/` |
| Capabilities (nav + gate) | `frontend/src/features/administration/context/` |
| Layout, pages | `frontend/src/features/administration/components/`, `pages/` (lazy-loaded) |
