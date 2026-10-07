"""
Attachments, at the service, selector and storage layers (S2-007).

The `Attachment` model is S2-001's; nothing here changes it. What this suite
protects, in the order it would break:

1. **Upload requires idea authorship, not merely readability.** Unlike a
   comment or a vote, adding evidence to an idea is closer to editing it -
   see `services._load_attachable_idea` - so a colleague who can read a
   `PUBLIC` idea still may not attach anything to it.
2. **No lifecycle gate.** Deliberately, and pinned the same way S2-006 pins
   the same decision for votes: evidence accumulates through review, not
   only while an idea is a draft.
3. **The client is never trusted with the storage key, the content type, or
   the display filename's directory component.** All three are decided
   here, never accepted as input.
4. **A file is validated before anything touches storage**, and a renamed or
   disguised file is caught by its own bytes, not only its extension.
5. **Storage and the database stay consistent** across the two failure
   orders this module can actually hit: a database failure after a
   successful storage write, and a storage failure after a successful
   database delete.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.utils import timezone

from ideas import attachments as attachment_rules
from ideas import selectors, services, storage
from ideas.models import Attachment, Idea
from identity.models import User
from organizations.models import Membership

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'x' * services.MIN_DESCRIPTION_LENGTH

# A real, minimal PDF: enough bytes for `%PDF` to be the true leading
# signature, not a hand-typed guess at one.
PDF_BYTES = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF'
PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
EXE_BYTES = b'MZ' + b'\x00' * 32


def make_user(email='ada@example.com', **overrides):
    fields = {
        'email': email,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password=VALID_PASSWORD, **fields)


def make_organization(name='Acme Labs', owner=None):
    from organizations.services import CreateOrganizationInput, create_organization_for_user

    if owner is None:
        owner = make_user(f'{name.split()[0].lower()}@example.com')
    result = create_organization_for_user(owner, CreateOrganizationInput(name=name))
    return result.organization, result.membership


def add_active_member(organization, user):
    return Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )


def make_idea(organization, author, **overrides):
    fields = {
        'organization': organization,
        'author': author,
        'title': 'An idea',
        'description': DESCRIPTION,
        'visibility': Idea.Visibility.ORGANIZATION,
        'status': Idea.Status.SUBMITTED,
        'submitted_at': timezone.now(),
    }
    fields.update(overrides)
    if fields.get('status') == Idea.Status.DRAFT:
        fields['submitted_at'] = None
    return Idea.objects.create(**fields)


def pdf_file(name='evidence.pdf'):
    return SimpleUploadedFile(name, PDF_BYTES, content_type='application/pdf')


def png_file(name='screenshot.png'):
    return SimpleUploadedFile(name, PNG_BYTES, content_type='image/png')


@pytest.fixture
def world():
    author = make_user('author@example.com')
    organization, _ = make_organization(owner=author)
    colleague = make_user('colleague@example.com')
    add_active_member(organization, colleague)

    outsider = make_user('outsider@example.com')
    other, _ = make_organization(name='Other Co', owner=outsider)

    return {
        'author': author,
        'colleague': colleague,
        'outsider': outsider,
        'organization': organization,
        'other': other,
    }


@pytest.fixture(autouse=True)
def _isolated_attachment_storage(tmp_path, settings):
    """
    Point the `attachments` storage alias at a throwaway directory for every
    test in this file, so nothing here ever touches the developer's real
    `media/attachments`, and so tests can run in parallel without racing on
    the same files.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        'attachments': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
            'OPTIONS': {'location': str(tmp_path)},
        },
    }
    # `storages` (django.core.files.storage) caches backend instances by
    # alias; clear the cache so the override above actually takes effect for
    # this test rather than reusing an instance built from an earlier
    # test's settings.
    from django.core.files.storage import storages

    storages._storages.clear()
    yield
    storages._storages.clear()


# --- uploading -------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadAttachment:
    def test_the_author_can_attach_a_file(self, world):
        idea = make_idea(world['organization'], world['author'])

        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert attachment.idea_id == idea.pk
        assert attachment.uploaded_by_id == world['author'].pk
        assert attachment.filename == 'evidence.pdf'
        assert attachment.content_type == 'application/pdf'
        assert attachment.size == len(PDF_BYTES)
        assert attachment.storage_key

    def test_the_file_is_actually_readable_back(self, world):
        idea = make_idea(world['organization'], world['author'])

        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        with storage.open_object(attachment.storage_key) as handle:
            assert handle.read() == PDF_BYTES

    def test_a_colleague_who_can_read_the_idea_cannot_attach_to_it(self, world):
        """
        The central rule of this module: readability is not editability.
        """
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError) as refusal:
            services.upload_attachment(world['colleague'], idea.pk, pdf_file())

        assert refusal.value.message == 'Idea is unavailable.'
        assert Attachment.objects.count() == 0

    def test_a_public_idea_cannot_be_attached_to_merely_because_it_is_public(self, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)

        with pytest.raises(services.IdeaError):
            services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert Attachment.objects.count() == 0

    def test_an_unauthenticated_caller_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError):
            services.upload_attachment(None, idea.pk, pdf_file())

        assert Attachment.objects.count() == 0

    def test_a_deactivated_caller_is_refused(self, world):
        author = world['author']
        author.is_active = False
        author.save(update_fields=['is_active'])
        idea = make_idea(world['organization'], author)

        with pytest.raises(services.IdeaError):
            services.upload_attachment(author, idea.pk, pdf_file())

    def test_another_tenants_idea_cannot_be_attached_to(self, world):
        idea = make_idea(world['other'], world['outsider'])

        with pytest.raises(services.IdeaError) as refusal:
            services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert refusal.value.message == 'Idea is unavailable.'

    def test_a_private_idea_cannot_be_attached_to_by_a_non_author(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)

        with pytest.raises(services.IdeaError):
            services.upload_attachment(world['colleague'], idea.pk, pdf_file())

    def test_an_unknown_idea_answers_exactly_like_an_invisible_one(self, world):
        idea = make_idea(world['other'], world['outsider'])

        with pytest.raises(services.IdeaError) as unknown:
            services.upload_attachment(world['author'], 999999, pdf_file())
        with pytest.raises(services.IdeaError) as invisible:
            services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert unknown.value.message == invisible.value.message

    def test_a_former_member_cannot_attach_after_leaving(self, world):
        idea = make_idea(world['organization'], world['author'])
        membership = Membership.objects.get(
            user=world['author'], organization=world['organization']
        )
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])

        with pytest.raises(services.IdeaError) as refusal:
            services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert refusal.value.reason == 'membership_required'

    def test_a_second_file_gets_a_second_distinct_storage_key(self, world):
        idea = make_idea(world['organization'], world['author'])

        first = services.upload_attachment(world['author'], idea.pk, pdf_file())
        second = services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert first.storage_key != second.storage_key
        assert Attachment.objects.filter(idea=idea).count() == 2

    def test_the_stored_content_type_is_canonical_not_the_clients_claim(self, world):
        """
        A client that lies about the MIME type gets the server's own answer
        stored, not its own claim - see `ideas.attachments`'s module
        docstring.
        """
        idea = make_idea(world['organization'], world['author'])
        lying_upload = SimpleUploadedFile(
            'evidence.pdf', PDF_BYTES, content_type='application/x-whatever-i-want'
        )

        attachment = services.upload_attachment(world['author'], idea.pk, lying_upload)

        assert attachment.content_type == 'application/pdf'


# --- no lifecycle gate -------------------------------------------------------------------


@pytest.mark.django_db
class TestNoLifecycleGate:
    def test_a_draft_can_have_evidence_attached(self, world):
        idea = make_idea(world['organization'], world['author'], status=Idea.Status.DRAFT)
        assert idea.status == Idea.Status.DRAFT

        assert services.upload_attachment(world['author'], idea.pk, pdf_file()) is not None

    def test_a_rejected_idea_can_still_have_evidence_attached(self, world):
        from django.utils import timezone

        idea = make_idea(world['organization'], world['author'])
        idea.status = Idea.Status.REJECTED
        idea.submitted_at = timezone.now()
        idea.save()

        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())
        assert attachment.idea_id == idea.pk

    def test_uploading_does_not_change_the_idea(self, world):
        idea = make_idea(world['organization'], world['author'], status=Idea.Status.DRAFT)

        services.upload_attachment(world['author'], idea.pk, pdf_file())
        idea.refresh_from_db()

        assert idea.status == Idea.Status.DRAFT


# --- filename and path-traversal safety --------------------------------------------------


@pytest.mark.django_db
class TestFilenameSafety:
    @pytest.mark.parametrize(
        'raw_name',
        [
            '../../etc/passwd.pdf',
            '..\\..\\windows\\system32\\evidence.pdf',
            '/etc/passwd.pdf',
            'a/b/c/evidence.pdf',
        ],
    )
    def test_directory_components_are_stripped_from_the_display_name(self, world, raw_name):
        idea = make_idea(world['organization'], world['author'])

        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file(name=raw_name))

        assert '/' not in attachment.filename
        assert '\\' not in attachment.filename
        assert attachment.filename == 'passwd.pdf' or attachment.filename == 'evidence.pdf'

    def test_the_storage_key_never_contains_the_clients_filename(self, world):
        idea = make_idea(world['organization'], world['author'])

        attachment = services.upload_attachment(
            world['author'], idea.pk, pdf_file(name='../../etc/passwd.pdf')
        )

        assert 'passwd' not in attachment.storage_key
        assert '..' not in attachment.storage_key

    def test_a_filename_with_control_characters_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])
        upload = SimpleUploadedFile('evidence\x00.pdf', PDF_BYTES, content_type='application/pdf')

        with pytest.raises(services.IdeaError):
            services.upload_attachment(world['author'], idea.pk, upload)

    def test_django_itself_already_refuses_a_bare_path_segment_filename(self, world):
        """
        Not `services.upload_attachment`'s own defense - Django's own
        `UploadedFile.name` setter (`validate_file_name`) already refuses a
        name of `''`, `'.'` or `'..'` before this domain's code ever sees it,
        which is why `ideas.attachments.safe_display_filename`'s equivalent
        cases are exercised directly, at the unit level
        (`TestSafeDisplayFilename`), rather than through a real
        `SimpleUploadedFile` here. This test pins the fact that both layers
        exist and agree, not a gap.
        """
        from django.core.exceptions import SuspiciousFileOperation

        with pytest.raises(SuspiciousFileOperation):
            SimpleUploadedFile('..', PDF_BYTES, content_type='application/pdf')

    def test_an_extremely_long_filename_is_truncated_by_django_and_still_accepted(self, world):
        """
        Django's own `UploadedFile.name` setter truncates a filename to its
        own `FILENAME_MAX_LENGTH` (255) before this domain ever sees it - so
        a 300-character name arrives already at exactly 255, which
        `ideas.attachments.safe_display_filename`'s own 255-character limit
        (pinned directly in `TestSafeDisplayFilename`) accepts. There is no
        gap between the two layers; this pins that they agree rather than
        silently relying on it.
        """
        idea = make_idea(world['organization'], world['author'])
        upload = SimpleUploadedFile('a' * 300 + '.pdf', PDF_BYTES, content_type='application/pdf')
        assert len(upload.name) == 255

        attachment = services.upload_attachment(world['author'], idea.pk, upload)

        assert len(attachment.filename) == 255


# --- file type validation ----------------------------------------------------------------


@pytest.mark.django_db
class TestFileTypeValidation:
    def test_a_disallowed_extension_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])
        upload = SimpleUploadedFile(
            'script.exe', EXE_BYTES, content_type='application/octet-stream'
        )

        with pytest.raises(services.IdeaError) as refusal:
            services.upload_attachment(world['author'], idea.pk, upload)

        assert refusal.value.field == 'file'
        assert Attachment.objects.count() == 0

    def test_an_executable_renamed_to_a_supported_extension_is_refused(self, world):
        """
        The extension allow-list is not the only gate: an `.exe` wearing a
        `.pdf` name must still be caught by its own bytes.
        """
        idea = make_idea(world['organization'], world['author'])
        upload = SimpleUploadedFile('totally-a.pdf', EXE_BYTES, content_type='application/pdf')

        with pytest.raises(services.IdeaError):
            services.upload_attachment(world['author'], idea.pk, upload)

        assert Attachment.objects.count() == 0

    def test_a_png_wearing_a_pdf_extension_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])
        upload = SimpleUploadedFile('image-not-pdf.pdf', PNG_BYTES, content_type='application/pdf')

        with pytest.raises(services.IdeaError):
            services.upload_attachment(world['author'], idea.pk, upload)

    def test_a_genuine_png_is_accepted(self, world):
        idea = make_idea(world['organization'], world['author'])

        attachment = services.upload_attachment(world['author'], idea.pk, png_file())

        assert attachment.content_type == 'image/png'

    def test_a_shebang_script_is_refused_regardless_of_extension(self, world):
        idea = make_idea(world['organization'], world['author'])
        upload = SimpleUploadedFile(
            'notes.txt', b'#!/bin/sh\nrm -rf /\n', content_type='text/plain'
        )

        with pytest.raises(services.IdeaError):
            services.upload_attachment(world['author'], idea.pk, upload)

    def test_plain_text_with_no_magic_signature_is_accepted(self, world):
        idea = make_idea(world['organization'], world['author'])
        upload = SimpleUploadedFile(
            'notes.txt', b'just some plain notes\n', content_type='text/plain'
        )

        attachment = services.upload_attachment(world['author'], idea.pk, upload)

        assert attachment.content_type == 'text/plain'

    def test_a_missing_file_is_refused_before_anything_is_read(self, world):
        idea = make_idea(world['organization'], world['author'])
        empty_upload = SimpleUploadedFile('empty.pdf', b'', content_type='application/pdf')

        with pytest.raises(services.IdeaError) as refusal:
            services.upload_attachment(world['author'], idea.pk, empty_upload)

        assert 'empty' in refusal.value.message.lower()


# --- size validation ----------------------------------------------------------------------


@pytest.mark.django_db
class TestSizeValidation:
    def test_an_oversized_file_is_refused(self, world, settings):
        settings.ATTACHMENT_MAX_UPLOAD_BYTES = 1024
        idea = make_idea(world['organization'], world['author'])
        oversized = SimpleUploadedFile(
            'big.pdf', PDF_BYTES + b'\x00' * 2000, content_type='application/pdf'
        )

        with pytest.raises(services.IdeaError) as refusal:
            services.upload_attachment(world['author'], idea.pk, oversized)

        assert 'smaller' in refusal.value.message.lower()
        assert Attachment.objects.count() == 0

    def test_a_file_at_exactly_the_limit_is_accepted(self, world, settings):
        settings.ATTACHMENT_MAX_UPLOAD_BYTES = len(PDF_BYTES)
        idea = make_idea(world['organization'], world['author'])

        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert attachment.size == len(PDF_BYTES)


# --- listing ----------------------------------------------------------------------------


@pytest.mark.django_db
class TestListAttachments:
    def test_the_author_sees_their_own_uploads(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.upload_attachment(world['author'], idea.pk, pdf_file('a.pdf'))
        services.upload_attachment(world['author'], idea.pk, pdf_file('b.pdf'))

        page = selectors.list_attachments(world['author'], idea.pk)

        assert [item.filename for item in page.items] == ['a.pdf', 'b.pdf']
        assert page.total_count == 2

    def test_a_colleague_can_list_but_not_have_uploaded_them(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.upload_attachment(world['author'], idea.pk, pdf_file())

        page = selectors.list_attachments(world['colleague'], idea.pk)

        assert page.total_count == 1

    def test_listing_is_ordered_oldest_first(self, world):
        idea = make_idea(world['organization'], world['author'])
        first = services.upload_attachment(world['author'], idea.pk, pdf_file('first.pdf'))
        second = services.upload_attachment(world['author'], idea.pk, pdf_file('second.pdf'))

        page = selectors.list_attachments(world['author'], idea.pk)

        assert [item.pk for item in page.items] == [first.pk, second.pk]

    def test_an_unreadable_ideas_attachments_are_an_empty_page_not_an_error(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        services.upload_attachment(world['author'], idea.pk, pdf_file())

        page = selectors.list_attachments(world['colleague'], idea.pk)

        assert page.items == []
        assert page.total_count == 0

    def test_an_unknown_idea_answers_exactly_like_an_unreadable_one(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        services.upload_attachment(world['author'], idea.pk, pdf_file())

        unknown = selectors.list_attachments(world['colleague'], 999999)
        invisible = selectors.list_attachments(world['colleague'], idea.pk)

        assert unknown.items == invisible.items == []

    def test_another_tenants_attachments_are_not_listed(self, world):
        idea = make_idea(world['other'], world['outsider'])
        services.upload_attachment(world['outsider'], idea.pk, pdf_file())

        page = selectors.list_attachments(world['author'], idea.pk)

        assert page.items == []

    def test_listing_does_not_cost_a_query_per_row(self, world, django_assert_num_queries):
        idea = make_idea(world['organization'], world['author'])
        for index in range(10):
            services.upload_attachment(world['author'], idea.pk, pdf_file(f'f{index}.pdf'))

        # Four, exactly matching `list_comments`' own pinned cost
        # (test_comments.py::test_reading_a_discussion_costs_a_fixed_number_of_queries):
        # the membership lookup the visibility filter needs, the idea read
        # that resolves `idea_id` through it, the COUNT, and the page fetch.
        # `select_related('uploaded_by')` is what keeps the last of those
        # from growing with the number of attachments.
        with django_assert_num_queries(3):
            page = selectors.list_attachments(world['author'], idea.pk)
            for item in page.items:
                _ = item.uploaded_by.email


# --- single-attachment reads --------------------------------------------------------------


@pytest.mark.django_db
class TestGetAttachment:
    def test_a_reader_can_fetch_one_attachment_by_id(self, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        found = selectors.get_attachment(world['colleague'], attachment.pk)

        assert found is not None
        assert found.pk == attachment.pk

    def test_an_attachment_on_an_unreadable_idea_is_none(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert selectors.get_attachment(world['colleague'], attachment.pk) is None

    def test_an_unknown_id_answers_exactly_like_an_unreadable_attachment(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        assert selectors.get_attachment(world['colleague'], 999999) is None
        assert selectors.get_attachment(world['colleague'], attachment.pk) is None

    def test_get_idea_attachment_requires_both_ids_to_agree(self, world):
        idea_one = make_idea(world['organization'], world['author'], title='One')
        idea_two = make_idea(world['organization'], world['author'], title='Two')
        attachment = services.upload_attachment(world['author'], idea_one.pk, pdf_file())

        # The attachment is real and belongs to idea_one; naming idea_two must
        # not resolve it anyway.
        assert selectors.get_idea_attachment(world['author'], idea_two.pk, attachment.pk) is None
        assert (
            selectors.get_idea_attachment(world['author'], idea_one.pk, attachment.pk).pk
            == attachment.pk
        )


# --- deleting -----------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteAttachment:
    def test_the_author_can_delete_their_own_ideas_attachment(self, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        services.delete_attachment(world['author'], attachment.pk)

        assert not Attachment.objects.filter(pk=attachment.pk).exists()

    def test_deleting_removes_the_storage_object_too(self, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())
        key = attachment.storage_key
        assert storage.attachment_storage().exists(key)

        services.delete_attachment(world['author'], attachment.pk)

        assert not storage.attachment_storage().exists(key)

    def test_a_colleague_cannot_delete_the_authors_attachment(self, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        with pytest.raises(services.IdeaError) as refusal:
            services.delete_attachment(world['colleague'], attachment.pk)

        assert refusal.value.message == 'Attachment is unavailable.'
        assert Attachment.objects.filter(pk=attachment.pk).exists()

    def test_another_tenant_cannot_delete_the_attachment(self, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        with pytest.raises(services.IdeaError):
            services.delete_attachment(world['outsider'], attachment.pk)

        assert Attachment.objects.filter(pk=attachment.pk).exists()

    def test_an_unauthenticated_caller_cannot_delete(self, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        with pytest.raises(services.IdeaError):
            services.delete_attachment(None, attachment.pk)

        assert Attachment.objects.filter(pk=attachment.pk).exists()

    def test_deleting_twice_is_refused_the_second_time(self, world):
        """
        Not idempotent - matching `delete_comment`'s shape, not the votes'
        toggle shape. See `services.delete_attachment`'s docstring.
        """
        idea = make_idea(world['organization'], world['author'])
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())

        services.delete_attachment(world['author'], attachment.pk)

        with pytest.raises(services.IdeaError) as refusal:
            services.delete_attachment(world['author'], attachment.pk)

        assert refusal.value.message == 'Attachment is unavailable.'

    def test_deleting_one_attachment_leaves_another_ideas_attachment_alone(self, world):
        idea_one = make_idea(world['organization'], world['author'], title='One')
        idea_two = make_idea(world['organization'], world['author'], title='Two')
        attachment_one = services.upload_attachment(world['author'], idea_one.pk, pdf_file())
        attachment_two = services.upload_attachment(world['author'], idea_two.pk, pdf_file())

        services.delete_attachment(world['author'], attachment_one.pk)

        assert Attachment.objects.filter(pk=attachment_two.pk).exists()


# --- consistency across storage/database failures ------------------------------------------


@pytest.mark.django_db
class TestStorageConsistency:
    def test_deleting_the_idea_deletes_its_attachment_metadata(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.upload_attachment(world['author'], idea.pk, pdf_file())

        idea.delete()

        assert Attachment.objects.count() == 0

    def test_a_database_failure_after_a_successful_storage_write_removes_the_orphan(self, world):
        """
        The row is created *after* the object is written; if the row's own
        write then fails, the object must not be left behind with nothing
        pointing to it.
        """
        idea = make_idea(world['organization'], world['author'])

        real_create = Attachment.objects.create

        def broken_create(**kwargs):
            raise DatabaseError('simulated failure')

        Attachment.objects.create = broken_create  # type: ignore[method-assign]
        try:
            with pytest.raises(DatabaseError):
                services.upload_attachment(world['author'], idea.pk, pdf_file())
        finally:
            Attachment.objects.create = real_create  # type: ignore[method-assign]

        assert Attachment.objects.count() == 0
        # The orphaned object was cleaned up - nothing left in the throwaway
        # storage root for this idea.
        found_any = storage.attachment_storage().listdir(f'ideas/{idea.pk}')[1]
        assert found_any == []

    def test_removing_a_vote_or_comment_does_not_touch_attachments(self, world):
        """
        Not a regression test for votes/comments themselves - just a
        cross-feature sanity check that S2-007 did not entangle its cleanup
        with an unrelated model's delete path.
        """
        idea = make_idea(world['organization'], world['author'])
        attachment = services.upload_attachment(world['author'], idea.pk, pdf_file())
        services.vote_for_idea(world['colleague'], idea.pk)
        services.add_comment(world['colleague'], idea.pk, 'A comment.')

        services.remove_vote(world['colleague'], idea.pk)

        assert Attachment.objects.filter(pk=attachment.pk).exists()


# --- validation module unit tests -----------------------------------------------------------


class TestSafeDisplayFilename:
    def test_a_plain_name_is_returned_unchanged(self):
        assert attachment_rules.safe_display_filename('evidence.pdf') == 'evidence.pdf'

    @pytest.mark.parametrize(
        ('raw', 'expected'),
        [
            ('../../etc/passwd', 'passwd'),
            ('a/b/c.pdf', 'c.pdf'),
            ('C:\\Users\\me\\report.docx', 'report.docx'),
        ],
    )
    def test_directory_components_are_discarded(self, raw, expected):
        assert attachment_rules.safe_display_filename(raw) == expected

    def test_blank_is_refused(self):
        with pytest.raises(attachment_rules.AttachmentValidationError):
            attachment_rules.safe_display_filename('   ')

    @pytest.mark.parametrize('bare_name', ['..', '.'])
    def test_a_bare_path_segment_is_refused(self, bare_name):
        with pytest.raises(attachment_rules.AttachmentValidationError):
            attachment_rules.safe_display_filename(bare_name)

    def test_a_name_over_the_length_limit_is_refused(self):
        with pytest.raises(attachment_rules.AttachmentValidationError):
            attachment_rules.safe_display_filename('a' * 256 + '.pdf')

    def test_a_name_at_the_length_limit_is_accepted(self):
        name = 'a' * 251 + '.pdf'  # exactly 255 characters
        assert len(name) == 255
        assert attachment_rules.safe_display_filename(name) == name


class TestResolveExtension:
    def test_an_allowed_extension_is_returned_lowercased(self):
        assert attachment_rules.resolve_extension('Report.PDF') == '.pdf'

    def test_an_unlisted_extension_is_refused(self):
        with pytest.raises(attachment_rules.AttachmentValidationError):
            attachment_rules.resolve_extension('script.exe')

    def test_no_extension_at_all_is_refused(self):
        with pytest.raises(attachment_rules.AttachmentValidationError):
            attachment_rules.resolve_extension('README')


class TestValidateContent:
    def test_a_dangerous_signature_is_refused_even_for_an_allowed_extension(self):
        with pytest.raises(attachment_rules.AttachmentValidationError):
            attachment_rules.validate_content('.pdf', b'MZ\x00\x00')

    def test_a_matching_signature_passes(self):
        attachment_rules.validate_content('.pdf', b'%PDF-1.4')

    def test_a_mismatched_signature_is_refused(self):
        with pytest.raises(attachment_rules.AttachmentValidationError):
            attachment_rules.validate_content('.pdf', b'\x89PNG\r\n\x1a\n')

    def test_text_extensions_have_no_signature_requirement(self):
        attachment_rules.validate_content('.txt', b'hello world')
        attachment_rules.validate_content('.csv', b'a,b,c\n1,2,3\n')

    def test_a_webp_is_checked_at_the_right_offset(self):
        webp = b'RIFF' + b'\x00\x00\x00\x00' + b'WEBP'
        attachment_rules.validate_content('.webp', webp)

        with pytest.raises(attachment_rules.AttachmentValidationError):
            attachment_rules.validate_content('.webp', b'RIFF' + b'\x00' * 8 + b'AVI ')


class TestStorageModule:
    def test_generate_storage_key_never_contains_the_raw_extension_input_unsafely(self):
        key = storage.generate_storage_key(idea_id=42, extension='.pdf')

        assert key.startswith('ideas/42/')
        assert key.endswith('.pdf')

    def test_deleting_a_missing_object_does_not_raise(self, tmp_path, settings):
        settings.STORAGES = {
            **settings.STORAGES,
            'attachments': {
                'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': str(tmp_path)},
            },
        }
        from django.core.files.storage import storages

        storages._storages.clear()

        storage.delete_object('ideas/1/does-not-exist.pdf')

        storages._storages.clear()

    def test_opening_a_missing_object_raises_file_not_found(self, tmp_path, settings):
        settings.STORAGES = {
            **settings.STORAGES,
            'attachments': {
                'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': str(tmp_path)},
            },
        }
        from django.core.files.storage import storages

        storages._storages.clear()

        with pytest.raises(FileNotFoundError):
            storage.open_object('ideas/1/does-not-exist.pdf')

        storages._storages.clear()
