# Collaboration: Teams, Invitations, Messages and Notifications

Four apps that answer four questions about the same thing - **who is working
with you, how they got there, how you talk to them, and what the platform has
told you.** They are small next to `ideas` and `reviews`, and each one exists
because collapsing it into a bigger app would have blurred exactly the
distinction it is about.

| App | The question | The rule it protects |
| --- | ------------ | -------------------- |
| `teams` | Who writes an idea with me? | A team is a collaboration boundary, **not a tenant** |
| `invitations` | How does somebody join? | **Acceptance is the only thing that creates a membership** |
| `messaging` | How do we talk about it privately? | A message is addressed to named people, not to an idea's readers |
| `notifications` | What has the platform decided? | A notification is the platform speaking, never a user |

They depend on `identity`, `organizations` and `ideas`, and depend on nothing
else in the platform: a team cannot review, a message cannot approve, and a
notification cannot move an idea.

## 1. Teams

A team is **a group of people who put an idea forward together**. It is not an
organization: an organization is shared work with roles and a reviewer who
confirms what the organization wants to submit, while a team is simply who writes
the idea with you. That single sentence is why `teams` is a separate app rather
than a flag on an organization:

- a team role **cannot hold a review or approval permission** - no such code
  exists for it (`teams/authorization.ALL_TEAM_PERMISSIONS`);
- a team never appears on the platform side of the journey as a tenant that
  validates anything;
- a reader with **no organization at all** can create a team, file a team idea
  and take it to the platform - the case the whole app exists for.

### Entities

| Model | What it is |
| ----- | ---------- |
| `Team` | `name`, unique `slug`, `description`, `owner` (`PROTECT`), timestamps. Unique per `(owner, name)` - two people in different organizations may each have a "University Automation Team" |
| `TeamMembership` | One user's place in one team: `active` or `inactive`, unique on `(team, user)` forever |
| `TeamRole` | A named set of team-scoped capabilities, per team. `is_system` marks the seeded Owner and Member |
| `TeamRolePermission` | Which `organizations.Permission` a role holds |
| `TeamMembershipRole` | Which roles a membership holds; `clean()` refuses a role from another team |

**Why team roles are their own tables.** `organizations.Role` has a required
`organization` foreign key - every role belongs to one tenant. A team
deliberately has none, so pointing at `Role` would mean inventing a fake
organization to hang team roles on, and a fake organization is a tenant that
could be filtered for and joined. Team roles are therefore a separate set of
tables over the **same** `organizations.Permission` records: the codes have one
definition in the platform, "which roles does this membership hold, and what do
they allow" is one implementation (`teams.authorization.membership_has_permission`),
and a team role cannot invent a permission the platform has never heard of.

### Permissions

| Code | Owner | Member |
| ---- | ----- | ------ |
| `team.view` | yes | yes |
| `team.members.view` | yes | yes |
| `team.ideas.submit` | yes | yes |
| `team.update` | yes | - |
| `team.members.manage` | yes | - |

`seed_team_roles` creates both roles per team and refuses any code
`authorization` has not declared, which is how "a team role cannot hold a review
or approval permission" is enforced at the only place permissions are granted.

### Operations

| Operation | Rule |
| --------- | ---- |
| `createTeam` | No organization required. The caller becomes Owner and first member, and the roles are seeded in the same transaction, so a team exists in a usable state or not at all. Needs only a name (≤ 200 chars) |
| `addTeamMember` | Requires `team.members.manage`. Grants the Member role only, so it cannot mint an Owner. **Re-adding somebody who left reactivates their row** rather than creating a second one - the uniqueness constraint is on `(team, user)`, not on active-ness |
| `leaveTeam` | Refused while the caller is the team's only active member: a team nobody can administer cannot be repaired from the interface, because managing it needs the permission they just gave up. The team and its ideas stay exactly as they are |
| `teams`, `team(id)`, `teamMembers` | Scoped to the caller's own active memberships. `team(id)` is `null` for a team the caller is not in **and** for one that does not exist, so a team id cannot discover teams |

Nothing here filters ideas. A team's ideas are read through
`ideas/selectors.py`, which joins the caller's team ids from
`teams.authorization.active_team_ids` - so "who may see this team's ideas" has
one definition, in the domain that owns ideas.

### Membership is never created by typing an address

There is no `add_member(email)`. A `TeamMembership` means "this person accepted
an invitation to this team" (or "this person created it"); nothing weaker creates
one. An `INACTIVE` row is kept so a team does not re-invite somebody who
already declined, and so `created_at` still says when they joined - it is not a
soft delete of their work, which stays theirs.

### Frontend

`/app/teams` (list of links plus a create form) and `/app/teams/:teamId` (roster,
leave, invitations, and - for the owner - adding a colleague). A team is a place
you go, not a mode you switch into: there is no "active team" and no second
picker in the header, because picking a team does not change what the rest of the
app is pointed at.

The **add-a-colleague** panel is the one operation that is not an invitation.
`addTeamMember` needs a user id and this API has no people directory, so the
candidates are drawn from the reader's **organization roster** - the only list of
people the client can name honestly - minus those already on the team. It is
offered to the owner only (`addTeamMember` needs `team.members.manage`, and
`ownerId` is the only statement of that the client has), which narrows what is
drawn; the server still decides.

## 2. Invitations

One table serves organizations and teams, because the security is the whole of
it and the security is identical.

- **Bound to an email address.** Acceptance requires that the signed-in account's
  own stored address matches, case-insensitively. Signed in as somebody else is
  *refused*, not silently switched.
- **Unguessable.** `token` is 32 bytes of `secrets.token_urlsafe`; only its
  SHA-256 digest is stored. A dump of the table cannot be replayed, and the
  plaintext exists in exactly one place - the email.
- **Single-use.** `PENDING → ACCEPTED` exactly once, claimed with a conditional
  `UPDATE` inside the transaction that grants the membership, so two clicks in two
  tabs produce two UPDATEs and exactly one membership.
- **Expiring** (`INVITATION_TOKEN_LIFETIME`, checked on acceptance, not only on
  issue) and **revocable** (`PENDING → REVOKED`, refused on the same path as an
  expired one).

`scope` says which tenant is named, and `clean()` enforces that exactly one of
`organization`/`team` is set and it is the one the scope says. `role_slug` is
stored rather than a foreign key on purpose: the invitation means "with the role
this tenant calls `reviewer` **at the moment it is accepted**", and acceptance
refuses a role the tenant does not have, so a stale slug fails loudly instead of
granting the wrong permissions.

**Owner is deliberately not an invitable organization role.** Handing out
ownership is a role change (`organizations.services.grant_membership_role`), and
letting an invitation create one would mean an Owner could mint a second Owner
without the last-holder protection. A *team* does offer Owner by invitation,
because a team owner inviting a co-owner is the ordinary thing.

### Refusals on acceptance

An unknown, revoked, expired or already-accepted token, and a token whose address
is not the signed-in account's, all refuse - and the messages are deliberately
uninformative about *which* half failed, so the endpoint cannot be used to
confirm that an invitation existed. "No account yet" is not handled here: the
frontend sends an unauthenticated visitor to register first and back to the link.

### The email

`invitations/email.py`, one message, addressed to the address **on the
invitation** - validated and normalized when the invitation was created, and
exactly the address acceptance requires. It names the tenant, the role, the
inviter, when it expires and a link to accept. It contains nothing else: an
inbox is readable by more than its owner, so the body is a statement about the
invitation only. Sent on `transaction.on_commit`, plain text, and never raises -
a delivery failure is logged, the invitation still stands, and the inviter can
revoke it and send another.

### Frontend

`/invitations/accept?token=…` is a **top-level** route, not an `/app` child: the
recipient may have no account yet, and the invitation may be the reason they
arrive. It shows only what `invitationDetails` returns - scope, tenant name,
role, address, state - so a link that leaked reveals nothing about the tenant.
It refuses politely when the reader is signed in with another address, because it
has no way to switch accounts and saying so beats failing with a redirect.
`TeamInvitations` is the panel that lists what a team has sent and lets an
inviter revoke it.

**Not yet in the UI.** The organization side is fully implemented on the server
(`sendOrganizationInvitation`, `organizationInvitations`, and the same accept
path) and the client's `invitationsApi` has the requests and
`useOrganizationInvitations` beside the team ones, but no component renders them
yet: `OrganizationWorkspace` still has no invitation panel, so an organization
member can be invited over GraphQL but not from a page.

## 3. Messaging

Private messages between named people, optionally about one idea.

**This is not `ideas.Comment`, and the difference is structural rather than a
flag.** A comment is public engagement: everybody who can read the idea can read
it. A message is addressed to people. Turning "Send Message" into a comment would
mean a private conversation posted where the author's colleagues can read it, so
the two are different models with different authorization - access comes from the
participant list on the thread, not from the idea.

| Model | What it is |
| ----- | ---------- |
| `MessageThread` | A conversation. `idea` is nullable **context, not access**; `latest_message_at` is non-null by construction |
| `MessageParticipant` | Who is in a thread, and `last_read_at` - how far **that person** has read |
| `Message` | One message, `MESSAGE_MAX_LENGTH = 5000` |

There is **no query that answers "messages on idea X"**, because "who can see a
message on idea X" is not a question about the idea. There is no `message(id)`
field either: a message is reached through a thread the caller is in.

### Rules

- `startThread` creates the thread, its participants and its first message in one
  transaction, so a thread never exists without a message - which is what lets
  `latest_message_at` be non-null and the thread list a single indexed read.
  Anchored to an idea the caller already has a conversation about, it **continues
  that thread** rather than starting a second one.
- The creator must be able to read the idea it is anchored to, so an idea cannot
  be used as a way to reach people. **The recipients are not required to be able
  to read it** - being told about the conversation directly is the entire point.
- A recipient id that does not resolve to an active account is **refused, not
  skipped**: a silently dropped recipient is a conversation somebody believes
  they are in and is not.
- A thread you are not in is `null`, and its messages are an empty page - the
  same answer as for a thread that does not exist.
- `markThreadRead` moves **your own** cursor to the end of the thread. It is
  personal; no other participant is affected, and a thread opens with the
  author's own first message already read.

### Frontend

`/app/messages` (list with per-thread unread counts, and the total from the same
response) and `/app/messages/:threadId`. Opening a conversation marks it read on
arrival; a posted message is spliced in rather than re-read, so the reader keeps
their place. The header's **Messages** badge is its own one-field query
(`unreadThreadCount`) asked once per mount, because the bar renders on every
authenticated page while the list renders on one.

**Not yet in the UI.** `startMessageThread` is implemented, authorized and
tested on the server and the client has `startMessageThreadRequest`, but no
component calls it: there is no compose screen yet, so a conversation can only
be started over GraphQL. The people a picker would offer are the same open
question the add-a-colleague panel answers - there is no people directory in
this API.

## 4. Notifications

One model, `Notification`: a **delivery of a business fact to one person** -
"your idea was approved", "a reviewer asked for changes". It is deliberately not
any of the other four things a person can receive:

| Thing | What it is |
| ----- | ---------- |
| Comment | Public engagement on an idea, visible to its readers |
| Review feedback | Formal, inside a `Review`, addressed to the author |
| Private message | `messaging.Message`, two named participants |
| **System notification** | **The platform telling somebody what happened** |
| Email | A delivery channel, not a kind of thing |

A comment saying "looks good to me" is not an approval; a vote is not an
approval; an email saying a review is finished is not the report. So this model
stores only things the **platform** decided, never anything a user wrote.

### Kinds

| Group | Kinds |
| ----- | ----- |
| The idea journey | `idea.organization_changes_requested`, `idea.organization_confirmed`, `idea.submitted_to_platform`, `idea.platform_changes_requested`, `idea.platform_rejected`, `idea.platform_approved`, `idea.owner_go_ahead` |
| Review queues | `review.assigned`, `review.organization_queue`, `review.platform_queue` |
| Invitations and messages | `invitation.received`, `invitation.accepted`, `message.received` |

`DECISION_KINDS` is the subset that is *about a decision* rather than a queue.
Only these may render a review report link, and only these may be produced by a
review service - which is what stops a "somebody is waiting for you" nudge from
pointing at an approval report. The client's equivalent is
`hasReportLink`, which reads the server's list rather than testing a string
prefix.

### One business event, two channels

`notifications.services.deliver` writes the row and registers the email **from
the same call**. It is one event with two deliveries, not two events, so nothing
downstream reconciles them and the list can never show an approval the email does
not mention.

**Nothing here runs inside the decision's transaction**, which is what makes "if
email fails, the approval remains valid" structural rather than aspirational:
both the row write and the send are registered on `transaction.on_commit`, the
write is wrapped so a failure is logged and swallowed, and the send never raises.
A caller therefore cannot accidentally make delivery part of the decision.

**`body` is short, non-sensitive and length-bounded here** (`BODY_MAX_LENGTH` =
500, `TITLE_MAX_LENGTH` = 200). The full content of whatever the notification is
about is never copied into it: the email says a review report is ready and links
to it, and the report stays behind authentication.

`Notification` is **not an audit record** - `ideas.IdeaTransition` and
`administration.AdminAuditEntry` are. Nothing is ever updated except
`is_read_at`, which is that reader's own business, and `recipient` is `PROTECT`:
deleting an account must not rewrite the fact that somebody was told something.

### Frontend

`/app/notifications`, plus a bell in the app chrome whose badge is the **server's**
count (`unreadNotificationCount`), asked once per mount so it is right even on a
page where the list has never been opened. Marking read is per notification and
"mark all read" - a person clearing their inbox has one intent, and a partial
clear invites repeated clicking.

## 5. Deliberately not here

- **No team review, approval or platform tenancy.** No code for it exists to
  grant; a team submits, an organization confirms, the platform decides.
- **No messaging by email address.** `startThread` takes user ids; reaching
  somebody who has no account is an invitation, and messaging them about an idea
  they cannot read would be a way around the idea's visibility.
- **No attachments, no threads beyond one idea, and no message deletion or
  editing.** A message is a record of what was said to named people.
- **No notification preferences, digests, batching or per-kind muting.** One
  email per delivery event, and the list is the record.
- **No Celery.** Notifications and emails are delivered synchronously on commit;
  the queue is still the plan for anything that must retry.

## 6. Code map

| Layer | Where |
| ----- | ----- |
| Team models, roles | `backend/teams/models.py` |
| Team authorization | `backend/teams/authorization.py` |
| Team reads / writes | `backend/teams/selectors.py`, `services.py` |
| Invitation model | `backend/invitations/models.py` |
| Invitation flow | `backend/invitations/services.py`, `email.py` |
| Message model, authorization | `backend/messaging/models.py`, `selectors.py` |
| Message writes | `backend/messaging/services.py` |
| Notification model, delivery | `backend/notifications/models.py`, `services.py`, `email.py` |
| GraphQL | `backend/teams/schema.py`, `invitations/schema.py`, `messaging/schema.py`, `notifications/schema.py` |
| Tests | `backend/teams/tests/`, `invitations/tests/`, `messaging/tests/`, `notifications/tests/` |
| Frontend API | `frontend/src/features/{teams,invitations,messaging,notifications}/api/` |
| Frontend pages | `frontend/src/features/{teams,messaging,notifications}/components/`, `frontend/src/routes/TeamsPage.tsx`, `TeamDetailPage.tsx` |