# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — Sprint 3: Review & Validation

- S3-001: Review & Validation domain architecture — `docs/reviews-domain.md`.
- S3-002: Reviewer eligibility and the review-domain foundation.
  - **`idea.review` permission** — the reviewer gate in `ideas.lifecycle`
    moves from "holds a system role" to this permission, checked through
    `organizations.authorization.membership_has_permission` and therefore
    scoped to the idea's own organization. The Owner role holds it, so no one
    who could review before lost the ability. `membership_holds_system_role`,
    whose only callers were the two reviewer checks, is removed.
  - **Reviewer role** — a non-system role provisioned per organization with
    exactly `organization.view` and `idea.review`, granted and removed through
    the existing `assignRoleToMembership` / `removeRoleFromMembership`.
    Migration `organizations/0003` grants `idea.review` to existing Owner roles
    and provisions the Reviewer role for existing organizations, leaving any
    pre-existing role with the `reviewer` slug untouched; it is reversible.
  - **`reviews` app** — `Review` (one row per review round: idea, reviewer
    (`PROTECT`), round, decision, feedback, submission snapshot, timestamps)
    and `ReviewCriterionAssessment` (five fixed criteria, categorical rating,
    no numeric score). Database constraints: unique `(idea, round)`, at most
    one open review per idea (partial unique), `round >= 1`, decided exactly
    when completed, known decision/criterion/rating values, one assessment per
    criterion per review. Completed reviews refuse further saves and new
    assessments; the admin is read-only.
  - **`reviews.eligibility`** — `can_review` (can read the idea and is its
    reviewer under the lifecycle's own rule) and `can_start_review` (the same,
    on a `SUBMITTED` idea). No queue, claiming, decision, GraphQL or UI yet.
  - **Membership prerequisite** — not added. A self-service "add member by
    email" mutation would reveal which emails have accounts to anyone who
    creates an organization; members are added through Django admin until a
    consent-based invitation flow exists.

### Added — Sprint 2: Ideas & Problem Submission

- S2-001: Ideas domain architecture — the `ideas` app, the platform's first
  business domain beyond Identity & Access. Five entities: `Category`
  (platform-wide, flat, reusable, retired rather than deleted once used),
  `Idea`, `Comment`, `Vote` and `Attachment`, plus their migration and admin
  registrations. Scope is deliberately schema-only: no service, selector,
  GraphQL operation or UI ships in this item, and none is stubbed. The
  design and the planned seams for S2-002 are in `docs/ideas-domain.md`.
  - **Lifecycle** — the full vocabulary (`draft`, `submitted`,
    `under_review`, `changes_requested`, `rejected`, `approved`,
    `automation_proposal`) is established so implementing the review
    workflow in Sprint 3 is behaviour, not a migration. Only `DRAFT →
    SUBMITTED` belongs to Sprint 2. `status` and `visibility` are enforced by
    database `CHECK` constraints *as well as* `choices`, because `choices`
    only covers `full_clean()` and `bulk_create`/`QuerySet.update`/a
    management command all bypass it; both are generated from one definition
    so they cannot drift.
  - **Visibility** — `public`/`organization`/`department`/`private`,
    defaulting to `private` so a new submission is visible to nobody until
    somebody deliberately widens it. `department` is a **reserved value**,
    not a half-built feature: the platform has no Department model, and
    inventing one inside `ideas` would have been a different sprint's work.
    What enforcement will need is recorded in the domain doc.
  - **No second authorization system.** `ideas` adds no permission codes and
    no `permissions.py`; membership and organization permissions stay in
    `organizations/authorization.py`. The one Ideas-specific decision -
    which ideas a user may *read* - is a visibility filter in the future
    selector layer, built on `get_membership`, not a parallel permission
    hierarchy.
  - **Tenancy** — `Idea.organization` is required on every row, so there is
    no idea that exists outside a tenant and a forgotten tenant filter cannot
    reach anything. `Category` is deliberately *not* tenant-scoped: it is
    what makes ideas comparable across organizations later.
  - **Storage boundary** — `Attachment` is metadata only
    (`storage_key`, `filename`, `content_type`, `size`) and holds no file
    field of any kind, so attachment bytes cannot end up in PostgreSQL. The
    bucket, presigned uploads and signed downloads are later work. A test
    asserts no `FileField`/`BinaryField`/`DataField` ever appears.
  - **Deletion behaviour** — `Idea.category` is `PROTECT`, because an idea
    that has been reviewed must not vanish when the category list is tidied;
    `is_active=False` is the supported retirement. `organization` and the
    child rows cascade, matching `Membership`'s rule in Sprint 1.
  - **Indexes** — five composite indexes shaped like the queries Sprint 2
    will actually make (organization feed, organization+status,
    category, visibility, author), and the three `Idea` foreign keys that
    lead one of them have Django's automatic single-column index switched
    off, since it would be a strict prefix of an index PostgreSQL could
    already use. Asserted by tests so the two cannot diverge.
  - A test asserts the app's model set is exactly these five, so a review,
    proposal or developer model arriving here fails the build.
- S2-002: Idea creation and submission — the first complete vertical slice of
  the Ideas domain, and the first business-domain operations on the platform.
  A real authenticated user can open the Ideas area, file an idea, edit their
  own draft, and submit it. `ideas/services.py` (create, update, submit),
  `ideas/selectors.py` (the read layer), `ideas/schema.py` (the GraphQL
  adapter), the `createIdea`/`updateIdea`/`submitIdea` mutations with
  `idea`/`ideas`/`organizationIdeas`/`categories` queries, and the
  `/app/ideas` frontend feature module.
  - **Authorization stays Sprint 1's.** `ideas` adds no permission code and
    no `permissions.py`: filing an idea requires an active membership in the
    target organization via `organizations.authorization.get_membership`,
    and editing or submitting requires authorship on top of that. Membership
    is re-checked on *every* write rather than only at creation, so leaving an
    organization takes the ability to write into it with you. An
    `idea.create` permission code was considered and rejected — the question
    is already answered, and a second answer would be free to drift from the
    first.
  - **The client is never trusted with ownership or tenancy.** `organizationId`
    is an input to a *decision* (the server authorizes that organization and
    then uses it) and the author is always the authenticated user, read from
    the access token. Neither appears in any input type, so "file this as
    somebody else" and "move this to another tenant" are not operations the
    API has the vocabulary for. A test asserts the input dataclass has no
    such field, so the property cannot be reintroduced quietly.
  - **Refusals never confirm existence.** A nonexistent idea, another
    author's, another tenant's, and one already submitted are answered
    identically — at the service, the selector and the GraphQL layer — so
    none of these operations can be used to discover which idea ids are real.
  - **Reads go through selectors, and the filter cannot be skipped.** Every
    read passes through `ideas/selectors.py`, which has already applied
    tenancy and `Idea.visibility`; the returned `QuerySet`s mean a caller
    cannot forget the filter by forgetting to apply it. `can_view_idea` and
    the queryset filter are the same rule twice, and a test asserts they
    agree, because a divergence would make the list and the "may I open
    this?" answer contradict each other.
  - **`DEPARTMENT` fails closed.** It is reserved vocabulary with no
    Department model behind it, so the service refuses it as a *choice* and
    the selectors treat it as author-only. Treating it as `ORGANIZATION`
    would mean an idea an author deliberately narrowed to their department
    was readable by their whole team — the exact outcome the reserved value
    exists to prevent. The frontend's picker does not offer it either, and
    the selectors are the one place that changes when the tier arrives.
  - **Submission is a rule of the transition, not of the schema.** A draft is
    incomplete by definition, so completeness — a title, a description of at
    least 20 characters, a category — is checked by `submit_idea`, not by a
    `null=False` column or a database constraint that would make a
    half-written idea unsavable. `submitted_at` is stamped in the same
    transaction as the status change, because the model treats the two as
    inconsistent apart. Only `DRAFT → SUBMITTED` is implemented; the review
    transitions are named in the enum and reachable from no mutation.
  - **The GraphQL type is stricter than the organizations one, deliberately.**
    `IdeaType` carries `authorId` and `organizationId` as ids and no nested
    user object: a `PUBLIC` idea is readable by any signed-in member of the
    platform, so an embedded user would publish a member's email address
    along with it. `status` and `visibility` are enums built from the
    model's own `TextChoices`, so the GraphQL names cannot drift from the
    database's and the review-only statuses are part of the contract from the
    start. A test asserts the field list contains no user or security field.
  - **Frontend**: `features/ideas/` with the API module, `IdeaForm`
    (create *and* edit — one form, because two would drift), `IdeaList`, and
    `IdeasWorkspace`, routed at `/app/ideas` as an `/app` child so it
    inherits `RequireAuth` and the existing `OrganizationProvider`. What the
    UI offers is decided from the signed-in user's id, which is a question of
    presentation and never a control: the server refuses an edit or a
    submission regardless of whether the buttons were rendered. A refused
    token, a rejected field, a field-less refusal and a transport failure are
    four distinct outcomes in the UI, because collapsing them either sends a
    user off to request something for a link that was fine, or tells them
    their idea was accepted when the request never arrived.
  - **No migration.** S2-002 is behaviour over the S2-001 schema;
    `makemigrations --check` reports no changes, which is the intended
    result rather than a lucky one.
  - Not implemented, and deliberately: comments, voting, attachment uploads,
    discovery, the review workflow, approval, automation proposals, developer
    matching, AI and payments. `docs/ideas-domain.md` scoped comments and
    votes into S2-002; this item implements neither, and the document now
    says so.


- S2-003: Idea lifecycle and visibility — the transition rules, and the
  server-side read policy they run inside. `ideas/lifecycle.py` (the matrix),
  `ideas/selectors.get_idea_for_update` (the locked read), the
  `transitionIdea` mutation, `IdeaType.availableTransitions`, and the
  review actions in the Ideas UI. No migration: this is behaviour over the
  S2-001 schema.
  - **The lifecycle is a table, not a chain of conditionals.** Seven
    `(from, to) -> actor` pairs, asserted as a set so a pair added to the code
    without being decided fails the test. The same table is what
    `availableTransitions` renders to the client, so what the UI offers and
    what the server accepts cannot come from two copies of the rules.
  - **A status cannot be set directly.** There is no mutation that takes one
    and no write input with a `status` field, so "promote my own idea to
    Approved" is not an operation the API expresses. A test sends such a
    request and requires the schema to reject it, and another enumerates every
    root mutation to confirm `transitionIdea` is the only place a status may
    be named.
  - **Two actors, and the self-review rule is structural.** The author owns
    `DRAFT → SUBMITTED` and `CHANGES_REQUESTED → SUBMITTED`; a reviewer owns
    everything from `SUBMITTED` on. A reviewer is an active member holding a
    **system role** in the idea's own organization who is **not** the author
    — the exclusion matters because bootstrap makes everybody's own
    organization theirs, so an author who also holds `Owner` genuinely holds
    it and no role check would catch them.
  - **No new permission code, and no `ideas/permissions.py`.** A dedicated
    `idea.review` code is the shape to adopt if a custom Reviewer role is ever
    needed; it is not added now because nothing else needs a second capability
    code, and a code that only ever means "is an Owner" would duplicate
    `Role.is_system` and have to be granted by hand in every organization
    created before it existed.
    `organizations.authorization.membership_holds_system_role` is the single
    function to replace if that trade changes.
  - **Refusals never confirm existence.** An unknown id, another tenant's
    idea, somebody else's `PRIVATE` idea, and one a departed reviewer can no
    longer see are all answered identically at the service, the selector and
    the GraphQL layer.
  - **Visibility and lifecycle agree.** A transition reads through the same
    filter every other read uses, so a reviewer cannot act on a `PRIVATE`
    idea they were never shown — being a reviewer is not a bypass of
    visibility. `DEPARTMENT` continues to fail closed in both directions: the
    service refuses it as a choice and the selectors treat it as author-only.
  - **Transitions are serialized per idea.** The row is read with
    `SELECT … FOR UPDATE` inside the transaction, so "approve" and "reject"
    fired together cannot both apply — the second waits and finds a status
    that no longer permits its move. Tested with two real connections and
    threads, which is the only way the property is actually exercised; and
    `of=('self',)` is what keeps the lock off the nullable `category` join,
    without which PostgreSQL refuses the whole query.
  - **`submitted_at` is written once and never rewritten**, on the first
    `DRAFT → SUBMITTED` only, so a re-submission after review feedback keeps
    the original stamp — which the model requires, since anything past
    `DRAFT` must have one.
  - **Refusals are distinguishable without leaking.** An actor who could not
    have made the move is told they may not; somebody who could is told the
    move does not exist. `submitIdea` keeps S2-002's exact wording ("Only a
    draft can be edited.") for the case it shipped, because the frontend
    shows the backend's message verbatim and a status-pair sentence would be
    worse copy for somebody who has just clicked Submit twice.
  - **Frontend:** status and visibility are rendered from one vocabulary
    table, and the actions on each idea are rendered from
    `availableTransitions` — so there is no client-side rule saying who may do
    what, and an author holding the Owner role is offered nothing on their own
    submitted idea. The `AUTOMATION_PROPOSAL` hand-off is deliberately not
    offered: the transition exists so the lifecycle is complete, and a button
    that leads nowhere would be worse than none.
  - Not implemented, and deliberately: review queues, reviewer assignment,
    reasons against a `CHANGES_REQUESTED` idea, dashboards, comments, voting,
    attachment uploads, the `DEPARTMENT` tier, proposals, developer matching,
    AI, payments and subscriptions. (Discovery arrives in S2-004.)
- S2-005: Comments and discussion — the `Comment` model S2-001 designed,
  implemented as a discussion attached to an idea:
  `add_comment`/`update_comment`/`delete_comment` in `ideas/services.py`,
  `list_comments`/`get_comment` in `ideas/selectors.py`, the
  `comments`/`createComment`/`updateComment`/`deleteComment` operations, and
  the discussion UI on the `/app/ideas` cards. No migration: the S2-001
  `Comment` model is used as designed.
  - **Reading a comment is reading the idea.** A comment has no tenancy, no
    visibility and no state of its own, so `list_comments` resolves the idea
    through `get_idea` and filters comments by the *same* `_visibility_filter`
    every other read uses. There is no comment-shaped version of the rule that
    could be more permissive than the idea's, and an unreadable idea is an
    **empty page** rather than an error — a discussion must not be a better
    oracle than the idea it hangs from.
  - **Commenting follows readability, not membership**, which is the rule
    `docs/ideas-domain.md` already set for voting. It is what lets anybody on
    the platform answer a `PUBLIC` idea without first becoming a member of
    someone else's organization; `PUBLIC` means platform-readable, and making
    it read-only to outsiders would be a different visibility tier.
  - **No elevated path, and no new authorization code.** Edit and delete are
    the comment's author and nobody else — not an administrator, not an
    organization Owner, and not the idea's own author, because ownership of an
    idea is not ownership of the discussion under it. `organizations` has no
    capability meaning "moderate a discussion" to check, and inventing one
    would be the second authorization system S2-001 ruled out. There is also no
    membership re-check on edit or delete, unlike `update_idea`: a comment was
    never a write *into a tenant*, so that gate would strand a comment on a
    `PUBLIC` idea its author may still read after leaving an organization.
  - **Refusals never confirm existence.** An unknown comment id, another
    author's comment and a comment on an unreadable idea all answer
    `"Comment is unavailable."`; an unknown idea and an unreadable one both
    answer `"Idea is unavailable."`
  - **Content is validated, normalized and never truncated.** Rejected when
    blank, whitespace-only, or over `MAX_COMMENT_LENGTH` (2000 — a module
    constant, a judgement call of the same kind as `MIN_DESCRIPTION_LENGTH`).
    Truncation would store text the author did not write and report success, so
    the only honest answer is a refusal the UI can show next to the box.
    Stripped at the ends and CRLF folded to LF, but internal whitespace is left
    alone: collapsing it would destroy the indentation of a pasted code block.
    Content is plain text throughout — stored verbatim, returned as a plain
    GraphQL `String`, and rendered as text by the client. Nothing escapes it in
    storage, because escaping belongs at render time.
  - **A deterministic order, stated where the tie-break is needed.** Oldest
    first, because a discussion is read in the order it happened. `created_at`
    is microsecond-resolution, so `created_at, pk` — and the tie-break is in
    the selector because `Comment.Meta.ordering` is `['created_at']` alone,
    which is arbitrary for two comments written in the same instant and lets a
    page boundary show one twice and skip another.
  - **Paging reuses S2-004's module unchanged**, including the same default of
    20 and maximum of 50, and `CommentPage` reuses the same `PageInfo` type as
    `IdeaPage` — a second pagination type would be a second set of conventions
    to learn. A discussion is a corollary of an idea being readable, so it gets
    the ideas page size rather than one of its own.
  - **The discussion-state rule lives beside the transition matrix.**
    `DISCUSSION_CLOSED_STATUSES = {REJECTED}`, closed because it has no
    outgoing transition and a comment there has no future move to inform.
    `AUTOMATION_PROPOSAL` is terminal in this app too and stays **open** —
    being terminal here is not the same as being finished with, and closing a
    handoff would cut off the conversation it invites. `DRAFT` is open too.
    Editing and deleting stay available in a closed discussion: retracting
    what you wrote is not participating in it. The invariant is asserted
    against `TRANSITIONS` rather than repeated, so the rule and the lifecycle
    cannot drift into two answers.
  - **Additive GraphQL change, reported deliberately:** `IdeaType` gains
    `discussionOpen: Boolean!`, computed from the same rule `createComment`
    enforces. No existing field changed and nothing was removed; the reason for
    adding it rather than having the client re-derive "is this rejected?" is
    the same one `availableTransitions` was added for — a client-side copy of a
    lifecycle rule is a second answer to a question the server owns.
  - **Frontend:** the discussion is a disclosure inside each idea's card,
    fetched when it is opened rather than on mount (a list of twenty ideas
    would otherwise be twenty requests), with at most one open at a time. A
    successful post, edit or delete updates the thread in place and never
    re-fetches the ideas list, so the reader keeps their filters and their
    page. Two submissions in one tick are dropped by a ref rather than by
    state, because state is not readable synchronously from a click handler
    and a double-posted comment is a duplicate somebody has to delete by hand.
    A failed read is reported as a failure rather than as an empty discussion,
    because "nobody has commented" and "we could not ask" are different claims.
  - Not implemented, and deliberately: threading/replies (S2-001 declined a
    parent pointer because "who may see a reply to a private comment" has real
    authorization depth), moderation and soft delete, mentions and
    notifications (Sprint 3), voting (S2-006), attachments (S2-007), and rate
    limiting on comment writes — this project has no throttling mechanism, and
    introducing one here would be a new security framework for a single
    operation.
- S2-007: Attachments & supporting evidence — the `Attachment` model S2-001
  designed, as `upload_attachment`/`delete_attachment` in `ideas/services.py`,
  file validation in `ideas/attachments.py`, a pluggable object-storage
  abstraction in `ideas/storage.py`, `attachments`/`attachment`/
  `deleteAttachment` in the GraphQL schema, two HTTP endpoints for the binary
  transfer GraphQL cannot carry (`ideas/views.py`), and an evidence section
  on the `/app/ideas` cards. No migration: the S2-001 `Attachment` model
  already had every field this needed.
  - **GraphQL is metadata and lifecycle; HTTP is bytes.** `attachments
    (ideaId)`, `attachment(id)` and `deleteAttachment(id)` follow this
    schema's existing conventions; there is no `addAttachment` mutation and
    never will be, because a multipart upload and a streamed download are
    not JSON-in/JSON-out operations. `POST /ideas/<id>/attachments/` and
    `GET /ideas/<id>/attachments/<id>/download/` authenticate with the same
    `Authorization: Bearer` header every GraphQL request carries.
  - **Upload and delete require idea *authorship*, not merely
    readability** — the first write in this domain where the two differ.
    Comments and votes follow readability because participation and interest
    both follow what a reader is shown; evidence is closer to editing the
    idea than to responding to it. Deliberately **no lifecycle gate**
    either, unlike a draft's own content (`update_idea` is `DRAFT`-only):
    evidence accumulates throughout review, so this follows the shape
    S2-006 established for votes (readable/authored, any status) rather
    than S2-002's draft-only edit rule or S2-005's discussion-closes-on-
    rejection rule. Listing and download stay readability-gated, the same
    as comments and votes, so every reader who can see an idea can see and
    fetch its evidence even though only its author can add or remove any.
  - **The client chooses nothing that matters for safety.** The storage key
    (`ideas.storage.generate_storage_key`) is built from the idea's id and a
    random token, never from client input — the actual path-traversal
    defense, not a check run against one. The recorded content type
    (`ideas.attachments.canonical_content_type`) is derived from the file's
    validated extension and confirmed against its own leading bytes; the
    browser's `Content-Type` claim is never read as data. The display
    filename is reduced to its last path segment on both separator styles
    before anything else touches it.
  - **Two independent file-type checks.** An explicit extension allow-list
    (PDF; PNG/JPEG/GIF/WebP; CSV/TXT; DOC/DOCX/XLS/XLSX — nothing executable
    or script-shaped), and the file's own leading bytes checked against the
    signature its extension claims, so a renamed `.exe` wearing a `.pdf`
    name is still refused. A denylist of dangerous signatures (`MZ`, ELF, a
    shebang, Mach-O/Java) is checked against *every* upload regardless of
    extension, including the two extensions (CSV, TXT) with no positive
    signature of their own. Size is capped by `ATTACHMENT_MAX_UPLOAD_BYTES`
    (10 MB default), checked against the upload's own measured size.
  - **Not idempotent, on either operation** — matching `add_comment`/
    `delete_comment`'s shape, not the votes' toggle shape. Uploading twice
    creates two attachments; deleting twice refuses the second time, because
    there is nothing left for it to name. The one race actually handled: a
    database failure *after* a successful storage write deletes the
    now-orphaned object rather than leaving it unreferenced; a storage
    failure *after* a successful database delete is logged and left as an
    orphan rather than resurrecting a row the caller was already told is
    gone — the database, not the storage backend, is authoritative for
    whether an attachment exists.
  - **Storage is a pluggable abstraction, not a hardcoded filesystem path.**
    `ideas/storage.py` calls only `django.core.files.storage`'s registry
    (`STORAGES['attachments']`, `config/settings/base.py`); today that
    resolves to Django's own filesystem backend (the only one installed),
    and nothing in `ideas/services.py` or `ideas/views.py` would need to
    change to point it at an object-storage backend later —
    `ATTACHMENTS_STORAGE_BACKEND` is a settings/environment change once one
    is actually provisioned. Download and upload go through this server for
    the same reason: no bucket exists yet to presign a URL against.
  - **Never rendered inline.** `Content-Disposition: attachment`
    unconditionally, for every accepted content type including images — an
    uploaded file is never trusted content, and this is the one rule that
    makes an allow-list of "safe to render inline" types unnecessary.
  - **Frontend:** a collapsed "Supporting evidence" section per idea card,
    fetched only while open, independent of the discussion disclosure next
    to it. Upload and delete controls render only for the idea's own author
    (`idea.authorId === user.id`) — a courtesy, since the server enforces
    authorship regardless — and download is offered to every reader who can
    see the section. A courtesy client-side check (extension, non-zero size,
    10 MB) rejects an obviously-doomed file before a round trip; the server's
    own message is what is shown for anything that check lets through.
    `uploadAttachmentRequest`/`downloadAttachmentRequest` are the only two
    functions in `ideasApi.ts` that call `fetch` directly instead of
    `graphqlClient`, attaching the same bearer token read fresh from
    `tokenStore`. Neither an upload nor a delete re-fetches the ideas list.
  - Not implemented, and deliberately: an actual object-storage bucket,
    presigned/signed upload or download URLs, virus/malware scanning,
    thumbnails or previews, versioning, per-attachment access grants beyond
    the idea's own visibility, and any AI processing of attachment content —
    those are later work, not omissions.
- S2-008: Ideas domain integration, security and hardening — an audit of
  S2-001 through S2-007 as one system, with fixes limited to what it found.
  No P0 (security or tenant-isolation) defect was found. No new feature, no
  API contract change, no migration and no new lifecycle state.
  - **Security/integration tests** — `ideas/tests/test_integration_security.py`
    walks attack paths across features rather than within one: two
    organizations in both directions, GraphQL and the attachment HTTP
    endpoints answering every (reader, idea) pair identically, another
    tenant's `PUBLIC` idea being readable but not attachable, `DEPARTMENT`
    staying author-only for every attachment operation, an attachment id
    borrowed under a different readable idea's URL, former members, a still-
    valid token for a deactivated account, a `PRIVATE` idea staying invisible
    to reviewers after submission, and comments/votes/attachments of an
    unreadable idea being unreachable by their own ids.
  - **Upload endpoint** — authenticates and authorizes *before* reading
    `request.FILES`, so an anonymous or unauthorized caller is refused without
    Django parsing the multipart body (anonymous with no file is now 401, not
    400). Validation failures are 400 and a storage failure is 502; both used
    to fall through `IdeaError`'s default `'forbidden'` reason to 404.
  - **Frontend** — discovery, discussion and evidence fetches discard a
    superseded response, so an older request settling last (or failing) can no
    longer replace or wipe the current answer; vote controls take the
    server's numbers again whenever the list is re-fetched instead of pinning
    the last vote response; the download blob URL is revoked on a timer rather
    than synchronously after `click()`; and the copy no longer claims drafts
    are always private or that submitting changes who can see an idea.
  - **Documented, deliberately unchanged** — content is not editable in
    `CHANGES_REQUESTED` (`update_idea` stays draft-only; a future refinement),
    and a submitted `PRIVATE` idea cannot be reviewed because visibility and
    status are independent. Stale docstrings in `ideas/models.py` and the
    sprint table in `docs/ideas-domain.md` were corrected.
- S2-006: Voting and engagement — the `Vote` model S2-001 designed, as
  `vote_for_idea`/`remove_vote` in `ideas/services.py`, `voteIdea`/`removeVote`
  and `IdeaVoteState` in the GraphQL schema, and a vote control on the
  `/app/ideas` cards. No migration: the S2-001 `Vote` model and its
  `unique_vote_per_user_idea` constraint are sufficient, and are relied on
  rather than replaced.
  - **One user, one vote per idea — enforced twice, on purpose.** A service
    check answers the common case (a double-clicked button, a retried request);
    the database constraint answers the one a check-then-insert cannot, which
    is two *simultaneous* requests that both read "no vote" and both insert.
    The constraint is the final boundary because it is the only thing that
    cannot be raced. The loser catches `IntegrityError`, reads the row the
    winner inserted, and **returns it as a success** — the state the caller
    asked for does exist, so an error would be a worse answer than the truth.
    An `IntegrityError` with no row behind it is re-raised rather than
    swallowed, so a genuine fault is not dressed up as success. Proven with two
    real connections and threads, and again deterministically so the
    `IntegrityError` branch itself is exercised rather than left to timing.
  - **Idempotency, chosen and asymmetric.** Voting twice succeeds, withdrawing
    twice succeeds, withdrawing a vote that was never cast succeeds — all
    because "no vote of mine" is the same fact either way, and a toggle
    double-clicked should not produce an error about a state the reader already
    reached. The one place it does **not** apply is the visibility gate: an
    unreadable idea is refused rather than silently accepted, or the
    idempotent branch would confirm the operation is available there.
  - **Voting is gated on visibility alone, with no lifecycle condition** — a
    deliberate divergence from S2-005's discussion rule, and the reason is
    written down because it reads like an oversight otherwise. `docs/ideas-domain.md`
    states the rule for votes as "a user may not vote on an idea they cannot
    read", full stop, so a `REJECTED` idea can still be voted on. A comment is
    participation in a *decision*; a vote is interest in the *idea*, which
    outlives its own review state. Pinned by a test so it is not "corrected"
    into a copy of the comment rule.
  - **No elevated path and no new permission code.** Vote and withdraw are the
    authenticated user's and nobody else's; `voteIdea` and `removeVote` take an
    `id` and nothing else, so "vote as somebody else" is not a request this
    API can express. `remove_vote` is scoped to the caller's own vote, so
    another user's row is not even a statement it can name.
  - **An unreadable idea leaks nothing.** The read side resolves through
    `get_idea` first, so an idea the caller cannot read never becomes readable
    enough to have a count; the mutation payloads carry a null `voteState` on
    every refusal rather than a zeroed one, which would still confirm the idea
    exists.
  - **Counts are computed, never stored.** No denormalized column and no
    cache, so there is no invalidation path to get wrong. A stored count would
    have to be maintained on vote, un-vote and idea deletion, and would be
    wrong the moment one of those failed.
  - **No N+1, and S2-004's query count is unchanged.** `IdeaType` gained
    `voteCount` and `viewerHasVoted`, annotated onto the page via
    `Coalesce(Subquery(count), 0)` and `Exists(...)` — so a page of fifty ideas
    still costs the same three queries as one, and Django does not carry
    annotations into `.count()`, which is pinned. `Coalesce` is load-bearing: a
    `COUNT` over an empty group returns SQL `NULL`, and `NULL` into a
    non-nullable `Int!` is a GraphQL error on the overwhelmingly common case
    of an idea nobody has voted on. An unannotated `Idea` (a single idea, or a
    row a mutation just wrote) falls back to two queries, which is why the
    *list* path is annotated and the single path is not.
  - **Additive GraphQL change:** two fields on `IdeaType`, one new type, two
    mutations. Nothing existing changed or was removed, and `ideas`,
    `organizationIdeas`, `IdeaPage` and `PageInfo` are untouched.
  - **Frontend:** the count and the reader's own answer arrive on each card, so
    twenty controls cost no extra requests. Control state is keyed by idea id,
    so a late response writes the idea it was for and never whichever one is on
    screen. **No optimistic update** — the rendered count is the one the
    mutation returned, which already accounts for other people's concurrent
    votes — so a failed vote leaves the number exactly where it was and there
    is nothing to roll back. The in-flight guard is a `useRef` rather than
    state, because state cannot be read synchronously from a click handler, and
    it is not module state because that would be shared by every mounted list
    and would leak between tests. Neither voting nor commenting re-fetches the
    ideas list, so a reader's filters and page survive both.
  - Not implemented, and deliberately: ranking, scoring or trending of the votes
    that now exist, a "who voted" listing (the domain has no policy for that
    aggregate), downvotes or vote weights, attachments (S2-007), proposals,
    developer matching, AI, payments, analytics, and any rate limiting — this
    project has no throttling mechanism, and adding one here would be a new
    security framework for a single operation.
- S2-004: Categories and discovery — browsing ideas by category, search, and
  combined filters over a bounded page.
  `selectors.list_discoverable_ideas`, `selectors.IdeaFilters`,
  `ideas/pagination.py`, `IdeaFiltersInput` and the `IdeaPage`/`PageInfo`
  types on `ideas`/`organizationIdeas`, and the discovery UI on
  `/app/ideas`. No migration: this is behaviour over the S2-001 schema.
  - **One read path.** `list_discoverable_ideas` is the only place the
    visibility filter, the discovery filters and pagination meet, and both
    queries go through it — so there is no second queryset for an unfiltered
    idea to escape through. `organizationIdeas` is a scoped wrapper over the
    same selector rather than a parallel query.
  - **Filters can only remove rows.** `_visibility_filter` is applied first
    and every filter narrows what it allowed; an idea matching the tenant, the
    category, the status and the search *exactly* is still not returned when
    it is private. `IdeaFilters` has no `visibility` and no `author_id`
    field, because a filter that reads like a grant (`visibility=public`)
    behaves like one the day somebody sends it.
  - **An unowned organization answers empty, not forbidden.** An
    `organization_id` the caller has no active membership of yields an empty
    page — the same answer as for an organization that does not exist, so the
    argument cannot be used to probe which ids are real.
  - **A filter that cannot be understood empties the result** rather than
    being ignored. A client that asked for category "abc" and got the
    unfiltered list would read that as "no ideas in this category" when it
    means "that category does not exist".
  - **Search is a bound parameter, over two fields.** `icontains` on
    `title` and `description`; the long-form fields (`problem_statement` and
    friends) are deliberately not searched, because they are not exposed by
    the API and matching text a reader may not read turns search into an
    oracle. Django escapes the pattern metacharacters, so a reader who types
    `%` searches for the character — pinned by a test, because it is a
    property of the ORM rather than of this code, and swapping `icontains`
    for `raw` to get trigram search would quietly turn it into "everything".
  - **Retirement is not deletion.** A retired category leaves the picker but
    keeps its history: an idea filed under it is still readable and still
    matches that category as a filter. S2-001's `PROTECT` is what makes that
    consistent. A retired category cannot reach a *private* idea, because
    visibility is filtered first.
  - **Paging is bounded and deterministic.** `ideas/pagination.py`, default 20
    and maximum 50, clamped rather than refused so a client asking for 1000
    rows gets a page and an error would only tell it to try again.
    `pageInfo` echoes `offset`/`limit` back *as applied*. Ordering is
    `-created_at, -pk` — the tie-breaker is what makes `offset` a correct way
    to page, since `created_at` is microsecond-resolution and two ideas filed
    in the same instant would otherwise let a page boundary repeat one row
    and skip another. Three queries per page (memberships, count, fetch); the
    `COUNT` is a deliberate trade, since `limit + 1` cannot answer "showing
    1-20 of 137".
  - **`ideas` and `organizationIdeas` now return a page, not a list.** A
    shape change to a shipped query, taken deliberately: returning the whole
    filtered table is not a thing this platform can keep doing as ideas
    accumulate, and the alternative — a second, filtered-for-caller's-own-use
    query — would be a second read policy to keep honest. A client that needs
    everything pages through it.
  - **There is no `organizationId` filter.** A tenant is scoped by the query
    that names it, so a request cannot name two tenants at once and leave the
    server to resolve the conflict.
  - **Frontend:** the search box is debounced at 300ms, so a reader typing a
    word sends one query instead of one per keystroke; the text in the box and
    the filter in effect are separate values. A narrowing filter returns to
    page 1 — written once, in the workspace, because it needs the filters and
    the page together. A write reloads the list without discarding the
    reader's place, which is why data loading moved into
    `hooks/useIdeaDiscovery` and the remount-to-reload trick went away. The
    three empty states — nothing here, nothing matches, nothing on this page —
    are kept apart, because they are different facts.
  - Not implemented, and deliberately: comments, voting, attachment uploads,
    a discovery *algorithm* or social feed, AI, payments, subscriptions, a
    marketplace, and Sprint 3's review queue.

### Added — Sprint 1: Identity & Access

- S1-002: User registration — the `identity` app's `User` model (email as
  the login identifier, Django's own password hashing) and a `register`
  GraphQL mutation with email normalization and password/phone validation.
  See `backend/identity/models.py` and `services.py`.
- S1-003: Email/password authentication — `login`/`refreshToken`/`logout`
  mutations, short-lived JWT access tokens held in memory only, and a
  persistent, rotating `RefreshSession` credential delivered as an HttpOnly
  cookie (never `localStorage`). `me` resolves the current user from the
  access token alone. `CORS_ALLOW_CREDENTIALS` is on for the cookie, still
  scoped to an explicit, non-wildcard origin allow-list.
- S1-004: Google sign-in — the `googleLogin` mutation over Google Identity
  Services' ID-token (OIDC) flow rather than an authorization-code exchange,
  so no client secret exists on either side. Credential verification
  (`backend/identity/google_oauth.py`) covers signature, issuer, audience
  and expiry, plus one-time-use replay protection keyed by the token's
  hash. **Account-linking policy: refuse, never link** — only an exact
  `(provider, provider_subject)` match authenticates into an existing
  account; a matching email is refused with the same generic message as an
  invalid credential, *regardless* of `email_verified`. Auto-linking by
  verified email was considered and rejected; the reasoning is recorded in
  `identity/authentication.py`.
- S1-005: Current user and protected application — the `me` query as the
  single authoritative identity source (no arguments, so no client value can
  influence it), `RequireAuth` gating `/app`, and the dashboard shell.
- S1-006: Organization membership — `Organization`/`Membership` models, the
  `createOrganization` bootstrap mutation, and `meOrganizations`/
  `meMemberships`. The bootstrap is deliberately gated on authentication
  alone: requiring a membership permission there would be circular, since
  the membership does not exist until the organization does.
- S1-007: Roles and permissions — `Role`, `Permission` and `MembershipRole`,
  all organization-scoped, with a system `Owner` role granted the full
  permission set at creation.
- S1-008: Authorization and tenant isolation — every organization-scoped
  operation resolves the caller's active membership and derives permission
  from it (`organizations/authorization.py`); nothing reads authorization
  input from the request. Cross-tenant requests return nothing and never
  confirm the target exists.
- S1-009: Sprint 1 integration and security hardening —
  - Fixed the backend Google configuration: `GOOGLE_OAUTH_CLIENT_ID` was
    read by the code while the local `backend/.env` stored the value under
    the wrong name, silently disabling Google sign-in. Corrected locally,
    documented in `backend/.env.example`, and pinned by tests that assert
    the exact variable name and that a misnamed one is ignored.
  - Removed a real Google client id from `frontend/.env.example`; the
    template is a placeholder again and a test rejects any real-looking
    client id in either template.
  - Added a shared cache abstraction: three separated aliases
    (`default`, `replay_protection`, `auth_throttle`) selected by
    `CACHE_URL`, which is now **required** in deployed environments and
    refused if it resolves to a per-process backend. Google replay
    protection and the new rate limits are correct across processes because
    of it, and both fail closed if the cache is unreachable.
  - Added authentication rate limiting (`identity/throttling.py`) on
    `login` (per account *and* per client), `googleLogin` (per client),
    `refreshToken` (per credential *and* per client) and `register` (per
    client), checked before the expensive work, with hashed subjects, a
    shared counter, recovery on window expiry, and no change to the generic
    authentication messages.
  - Reworked the frontend's access-token lifecycle: `tokenStore` holds the
    expiry alongside the token, and the new `tokenRefresh` refreshes
    proactively before expiry through a *single* in-flight promise (the
    refresh cookie is single-use, so overlapping refreshes would revoke
    each other), treats a failed refresh as terminal for the session
    instead of retrying, and writes nothing to browser storage.
  - Wired `SignUpForm` to the real `register` mutation, mapping the
    backend's field-level errors onto the matching fields and replacing the
    fake timeout. Registration does not authenticate, so success shows a
    confirmation leading to sign-in.
  - Added end-to-end HTTP integration suites: the complete authentication
    journey (cookie attributes, refresh rotation, replay rejection, logout,
    post-logout refresh), tenant isolation across two organizations, the
    Google sign-in flow end to end, the organization bootstrap model, the
    CSRF posture of `/graphql/`, and a two-sided GraphQL schema contract
    check shared with the frontend.
  - `.coveragerc` now includes `organizations`, so coverage reflects
    S1-006/S1-007/S1-008.
  - A refused Google account-linking collision is now reported
    specifically: a caller whose credential Google verified as
    `email_verified: true` is told the address already has an account, with
    the two real next steps, instead of a bare "Could not sign in with
    Google." with nothing to act on. The **refusal is unchanged** - Google
    sign-in still never auto-links by email, verified or not. The disclosure
    is gated on `email_verified` precisely so it cannot become an
    account-existence oracle: nobody can present a Google credential for an
    address they do not control, so the only addresses anyone can get an
    answer about are their own. Every other failure keeps the single generic
    message, and one message covers the whole collision class so a caller
    cannot learn whether the existing account is password-registered or
    belongs to a different Google identity. See
    `identity/authentication.py`'s `GoogleEmailInUseError`.
  - A transport failure during sign-in no longer rejects or strands the
    form. `login`/`loginWithGoogle` now always resolve to an outcome, so a
    backend that is simply not running shows an actionable error and
    re-enables the form instead of leaving the button spinning and logging
    an uncaught rejection. The message is deliberately not one of the
    backend's generic authentication messages - those report a decision the
    request never reached.
  - Documentation corrected: `frontend/README.md` described automatic
    Google account linking, a design that was rejected; `docs/architecture.md`
    now reflects S1-005 through S1-009 and `docs/environments.md` documents
    `CACHE_URL`.
- S1-010: Password reset and account activation — the two emailed-link flows,
  and the frontend that consumes them. `EmailToken` (with `EmailTokenPurpose`),
  `identity/email.py` and its two plain-text templates, the
  `requestPasswordReset`/`resetPassword`/`activateAccount`/
  `resendActivationEmail` mutations, the five SMTP/frontend-origin settings,
  and the `/activate-account` route the confirmation link needs.
  - **Neither request side discloses whether an address is registered.**
    `requestPasswordReset` and `resendActivationEmail` take a caller-supplied
    address, so a different answer for a hit than for a miss would make each
    an account-existence oracle. Every outcome — no account, deactivated,
    already verified, or Google-only with no password to reset — returns one
    message, and only the hit side sends anything. The frontend displays that
    message verbatim rather than wording its own, for the same reason.
  - **Only a hash is stored.** The raw token exists in exactly one place,
    the email; `EmailToken.token_hash` is a SHA-256 digest, so a database
    leak yields nothing replayable. A fast hash is right here precisely
    because the value is 384 bits of `secrets` output rather than a
    human-chosen secret — the reasoning `RefreshSession` already used,
    applied again rather than assumed.
  - **One-time, expiring, superseded.** A redeemed token is recorded as used
    rather than deleted, so "already used" stays distinguishable from "never
    existed" in the record even though the caller cannot tell them apart.
    Requesting a new link supersedes any outstanding one for the same
    purpose, so a leaked older email stops being a live credential — and
    superseding cannot erase the record that an earlier link was redeemed.
  - **One message for every unusable token**, and one per flow: never real,
    expired, already used, or belonging to the other operation. A message per
    case would tell whoever holds a stolen link exactly how far it got.
  - **A reset ends every session on the account**, the caller's own browser
    included, and clears its refresh cookie in the same response. Without
    that step a password changed *because it may have been stolen* leaves the
    attacker's existing session signed in until it expires on its own
    schedule.
  - **Verification is not a login requirement.** `login` does not check
    `isVerified`, so an unconfirmed account owns a legitimate account and is
    not locked out of it. Making verification an authorization gate is a
    separate decision, to be taken in one place, once there is a reason to.
  - **Delivery failure is logged, never raised**, at `ERROR` with the user's
    primary key and never the token — raising would either leak account
    existence to fix a mail problem or force every caller to reimplement the
    catch. The cost is that a broken mail setup is found in the logs rather
    than in a support ticket, which is why production settings now *require*
    the five email variables and refuse the console backend outright: both
    flows report generic success whether or not anything was sent, so a
    deployment that cannot send is otherwise indistinguishable from one
    nobody has used yet.
  - **All four operations are throttled** on the S1-009 infrastructure, with
    the per-address limits keyed on the *submitted* address and never
    resolved to a user, so a miss costs the caller the same budget as a hit.
    The password-reset and activation-resend limits are the mail-cannon
    bound; the reset/activation execution limits are about how much work an
    anonymous request can cause, not about guessing an unguessable token.
  - **Frontend wiring**: `ForgotPasswordForm` and `ResetPasswordForm` are no
    longer placeholders, and a new `/activate-account` route exists for the
    confirmation link. The three outcomes are kept apart on purpose — a
    refused token, a rejected password, and an unreachable server are three
    different facts, and treating them as one either sends a user off to
    request a new link for a link that was fine, or tells them their link is
    dead when the request never arrived. Following an activation link is an
    explicit press rather than a request on mount, because mail clients and
    link scanners fetch URLs before a person sees them and the token is
    single-use.
  - Tests: the flows at the service, GraphQL, transport and real-HTTP layers,
    the four throttle policies, the settings guards, and `.env.example`
    completeness. Several were written to fail first — a throttle rendered as
    "check your email" on the forgot-password form, and a permanently
    disabled confirm button on the activation page, were both caught this
    way.

### Added — Sprint 0: Foundation

- S0-001: Repository foundation — `frontend/`, `backend/`, `docs/`,
  `infrastructure/`, `scripts/` directory boundaries; `.github/` workflow,
  issue, and PR templates; root `.gitignore`, `.env.example`, `README.md`,
  `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, and `LICENSE`.
- S0-002: Django backend foundation — bare Django project under
  `backend/config`, environment-driven settings split into
  `base`/`local`/`production` via `django-environ`, SQLite placeholder
  database, and a dependency-free `/health/` endpoint. No business domain
  apps, authentication, PostgreSQL, or GraphQL yet.
- S0-006: Environment configuration — `ENVIRONMENT` convention (local,
  development, staging, production); required `DJANGO_SECRET_KEY` with no
  default; environment-driven CORS (`django-cors-headers`, `/graphql/` only)
  and CSRF trusted origins; fail-fast validation in
  `config.settings.production`; per-app `.env.example` files
  (`backend/`, `frontend/`); `docs/environments.md`.
- S0-007: Testing foundation — backend `pytest` + `pytest-django` +
  `pytest-cov` (`backend/tests/`, `requirements-dev.txt`); frontend Vitest +
  React Testing Library + jsdom with V8 coverage (`test`, `test:run`,
  `test:coverage`); existing GraphQL and settings tests migrated to pytest;
  a blank `DATABASE_URL` now fails at startup.
- S0-008: Code quality foundation — backend Ruff (lint, import sorting,
  format) configured in `backend/ruff.toml`; frontend Oxlint config expanded
  (React hooks, a11y, import, vitest rules; warnings fail CI) plus `oxfmt`
  formatter and `typecheck`/`format`/`format:check`/`lint:fix` scripts;
  existing code made compliant. No pre-commit hooks: local checks (and CI,
  once workflows exist) are the quality gate.
- S0-009: GitHub Actions CI foundation — `.github/workflows/ci.yml` runs
  backend (Ruff, Django checks, migration check, pytest with coverage against
  a PostgreSQL service) and frontend (Oxlint, oxfmt, `tsc`, Vitest, production
  build) jobs on pull requests to and pushes to `develop`/`main`. Read-only
  permissions, no secrets, no deployment.
- S0-010: Documentation foundation — new `docs/development.md`,
  `docs/testing.md` and `docs/git-workflow.md`; `docs/architecture.md`
  current-implementation status and repository structure brought up to date
  (target architecture preserved); `docs/environments.md` documents the CI
  execution context; `README.md` and `CONTRIBUTING.md` corrected for stale
  Sprint 0 statements and linked to the new guides.
- S0-011: Security foundation — explicit shared browser protections in
  `base.py` (nosniff, referrer policy, COOP, `X-Frame-Options: DENY`,
  HttpOnly/SameSite cookies, CORS wildcard and credentials off);
  production HSTS (default one year in production, one hour in
  development/staging, environment-driven, `includeSubDomains`
  and `preload` opt-in and validated) and opt-in proxy HTTPS detection
  (`DJANGO_TRUST_X_FORWARDED_PROTO`); `config.settings.local` now refuses
  deployed `ENVIRONMENT` names so neither local nor CI can run as production;
  `tests/test_security.py` (settings invariants, response headers, HTTPS
  redirect, `check --deploy`); `SECURITY.md` rewritten to match. No
  authentication or authorization yet (Sprint 1).
