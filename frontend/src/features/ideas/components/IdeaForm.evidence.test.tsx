import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  attachmentsRequest,
  categoriesRequest,
  createIdeaRequest,
  submitIdeaRequest,
  uploadAttachmentRequest,
  type Idea,
  type IdeaAttachment,
} from '../api/ideasApi'
import { useAuth } from '../../identity/auth/AuthContext'
import { IdeaForm } from './IdeaForm'
import { makeIdea } from '../../../test/idea'
import { pageOf } from '../../../test/ideaPage'
import {
  CLASSIFY_STEP,
  EVIDENCE_STEP,
  contextChoice,
  fillProblem,
  goToStep,
} from '../../../test/ideaForm'

/**
 * The last step: supporting documents.
 *
 * Uses the existing attachment endpoint and nothing else. A new idea does not
 * exist until it is saved, so the files chosen here are held and uploaded,
 * one by one, straight after the draft is created and before it is
 * submitted; an idea being edited already exists, so the step shows its live
 * evidence instead.
 */

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(),
  createIdeaRequest: vi.fn(),
  submitIdeaRequest: vi.fn(),
  uploadAttachmentRequest: vi.fn(),
  attachmentsRequest: vi.fn(),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))

const categoriesMock = vi.mocked(categoriesRequest)
const createMock = vi.mocked(createIdeaRequest)
const submitMock = vi.mocked(submitIdeaRequest)
const uploadMock = vi.mocked(uploadAttachmentRequest)
const attachmentsMock = vi.mocked(attachmentsRequest)

const CATEGORIES = [{ id: '11', name: 'Finance', slug: 'finance', description: '' }]

const CREATED: Idea = makeIdea({
  id: '42',
  title: 'A new idea',
  description: 'A description long enough.',
  visibility: 'ORGANIZATION',
  category: CATEGORIES[0],
  availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
})

const SUBMITTED: Idea = {
  ...CREATED,
  status: 'SUBMITTED',
  submittedAt: '2026-01-01T00:01:00Z',
}

function attachment(filename: string): IdeaAttachment {
  return {
    id: filename,
    ideaId: CREATED.id,
    uploaderId: '7',
    filename,
    contentType: 'application/pdf',
    size: 2048,
    createdAt: '2026-01-01T00:00:00Z',
    downloadUrl: `/ideas/42/attachments/${filename}/download/`,
  }
}

function file(name: string, size = 1024): File {
  return new File(['x'.repeat(size)], name, { type: 'application/pdf' })
}

function chooseFiles(...files: File[]) {
  fireEvent.change(screen.getByLabelText('Choose files'), {
    target: { files },
  })
}

/** A complete, submittable idea, waiting on the last step. */
async function readyToSubmit() {
  fillProblem()
  goToStep(CLASSIFY_STEP)
  await screen.findByRole('option', { name: 'Finance' })
  fireEvent.change(screen.getByLabelText('Category'), {
    target: { value: '11' },
  })
  fireEvent.click(contextChoice('Organization level'))
  goToStep(EVIDENCE_STEP)
}

describe('IdeaForm — supporting documents', () => {
  beforeEach(() => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    vi.mocked(useAuth).mockReturnValue({
      user: { id: '7' },
    } as unknown as ReturnType<typeof useAuth>)
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it('is the last step, optional, and asks in plain words', async () => {
    render(<IdeaForm organizationId="3" onSaved={vi.fn()} onSubmitted={vi.fn()} />)

    goToStep(EVIDENCE_STEP)

    expect(screen.getByText('Step 8 of 8')).toBeInTheDocument()
    expect(
      screen.getByText('Do you have anything that can help us understand the problem?'),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/screenshots, photos, spreadsheets, reports, forms/),
    ).toBeInTheDocument()
    expect(screen.getByText('No files chosen. This step is optional.')).toBeInTheDocument()
    // The end of the flow: no Next, and the two ways to finish.
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit for review' })).toBeInTheDocument()
    await waitFor(() => expect(categoriesMock).toHaveBeenCalled())
  })

  it('offers Submit for review only on the last step', async () => {
    render(<IdeaForm organizationId="3" onSaved={vi.fn()} onSubmitted={vi.fn()} />)

    expect(screen.queryByRole('button', { name: 'Submit for review' })).not.toBeInTheDocument()
    goToStep(CLASSIFY_STEP)
    expect(screen.queryByRole('button', { name: 'Submit for review' })).not.toBeInTheDocument()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('lists chosen files, and lets one be removed', async () => {
    render(<IdeaForm organizationId="3" onSaved={vi.fn()} onSubmitted={vi.fn()} />)
    goToStep(EVIDENCE_STEP)

    chooseFiles(file('form.pdf'), file('sheet.xlsx'))

    const list = screen.getByRole('list', { name: 'Files to attach' })
    expect(within(list).getByText('form.pdf')).toBeInTheDocument()
    expect(within(list).getByText('sheet.xlsx')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove form.pdf' }))
    expect(within(list).queryByText('form.pdf')).not.toBeInTheDocument()
    // Chosen files count as an answer to the step, and survive leaving it.
    goToStep(CLASSIFY_STEP)
    expect(stepButtonState()).toMatch(/\(started\)$/)
    goToStep(EVIDENCE_STEP)
    expect(screen.getByText('sheet.xlsx')).toBeInTheDocument()
    await waitFor(() => expect(categoriesMock).toHaveBeenCalled())
  })

  it('takes files dropped on the drop zone', async () => {
    render(<IdeaForm organizationId="3" onSaved={vi.fn()} onSubmitted={vi.fn()} />)
    goToStep(EVIDENCE_STEP)
    expect(screen.getByText('Drag files to upload')).toBeInTheDocument()

    fireEvent.drop(screen.getByTestId('file-drop-zone'), {
      dataTransfer: { files: [file('dropped.pdf')] },
    })

    const list = screen.getByRole('list', { name: 'Files to attach' })
    expect(within(list).getByText('dropped.pdf')).toBeInTheDocument()
    expect(within(list).getByText('Attached when you save or submit')).toBeInTheDocument()
    await waitFor(() => expect(categoriesMock).toHaveBeenCalled())
  })

  it('shows each file uploading, one at a time, while the idea is saved', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: CREATED,
    })
    let finishFirst: () => void = () => {}
    uploadMock
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = () =>
              resolve({
                success: true,
                message: 'ok',
                field: null,
                attachment: attachment('a.pdf'),
              })
          }),
      )
      .mockResolvedValueOnce({
        success: true,
        message: 'ok',
        field: null,
        attachment: attachment('b.pdf'),
      })
    const onSaved = vi.fn()
    render(<IdeaForm organizationId="3" onSaved={onSaved} onSubmitted={vi.fn()} />)
    fillProblem()
    goToStep(EVIDENCE_STEP)
    chooseFiles(file('a.pdf'), file('b.pdf'))

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    const list = await screen.findByRole('list', { name: 'Files to attach' })
    await waitFor(() => expect(within(list).getByText('Uploading…')).toBeInTheDocument())
    // The second waits its turn.
    expect(within(list).getByText('Attached when you save or submit')).toBeInTheDocument()
    expect(uploadMock).toHaveBeenCalledOnce()

    finishFirst()
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED))
    expect(uploadMock).toHaveBeenCalledTimes(2)
  })

  it('refuses an unsupported or empty file before anything is sent', async () => {
    render(<IdeaForm organizationId="3" onSaved={vi.fn()} onSubmitted={vi.fn()} />)
    goToStep(EVIDENCE_STEP)

    chooseFiles(file('script.exe'), new File([], 'empty.pdf'), file('ok.pdf'))

    expect(screen.getByText('script.exe: That file type is not supported.')).toBeInTheDocument()
    expect(screen.getByText('empty.pdf: That file is empty.')).toBeInTheDocument()
    expect(
      within(screen.getByRole('list', { name: 'Files to attach' })).getAllByRole('listitem'),
    ).toHaveLength(1)
    expect(uploadMock).not.toHaveBeenCalled()
    await waitFor(() => expect(categoriesMock).toHaveBeenCalled())
  })

  it('creates the idea, attaches each file, then submits - in that order', async () => {
    const calls: string[] = []
    createMock.mockImplementation(async () => {
      calls.push('create')
      return { success: true, message: 'ok', field: null, idea: CREATED }
    })
    uploadMock.mockImplementation(async (_id, picked) => {
      calls.push(`upload ${picked.name}`)
      return {
        success: true,
        message: 'ok',
        field: null,
        attachment: attachment(picked.name),
      }
    })
    submitMock.mockImplementation(async () => {
      calls.push('submit')
      return { success: true, message: 'ok', field: null, idea: SUBMITTED }
    })
    const onSubmitted = vi.fn()
    render(<IdeaForm organizationId="3" onSaved={vi.fn()} onSubmitted={onSubmitted} />)
    await readyToSubmit()
    const form = file('form.pdf')
    const photo = file('photo.png')
    chooseFiles(form, photo)

    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    await waitFor(() =>
      expect(onSubmitted).toHaveBeenCalledWith({
        submitted: true,
        idea: SUBMITTED,
      }),
    )
    expect(calls).toEqual(['create', 'upload form.pdf', 'upload photo.png', 'submit'])
    // Into the idea just created, through the ordinary upload request.
    expect(uploadMock).toHaveBeenNthCalledWith(1, '42', form)
    expect(uploadMock).toHaveBeenNthCalledWith(2, '42', photo)
  })

  it('attaches the files to a draft saved from the last step too', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: CREATED,
    })
    uploadMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      attachment: attachment('form.pdf'),
    })
    const onSaved = vi.fn()
    render(<IdeaForm organizationId="3" onSaved={onSaved} onSubmitted={vi.fn()} />)
    fillProblem()
    goToStep(EVIDENCE_STEP)
    chooseFiles(file('form.pdf'))

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED))
    expect(uploadMock).toHaveBeenCalledOnce()
    expect(submitMock).not.toHaveBeenCalled()
  })

  it('still submits, and names the file, when an upload is refused', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: CREATED,
    })
    uploadMock
      .mockResolvedValueOnce({
        success: false,
        message: 'That file type is not supported.',
        field: 'file',
        attachment: null,
      })
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({
        success: true,
        message: 'ok',
        field: null,
        attachment: attachment('fine.pdf'),
      })
    submitMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: SUBMITTED,
    })
    const onSubmitted = vi.fn()
    render(<IdeaForm organizationId="3" onSaved={vi.fn()} onSubmitted={onSubmitted} />)
    await readyToSubmit()
    chooseFiles(file('fake.pdf'), file('lost.pdf'), file('fine.pdf'))

    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    await waitFor(() =>
      expect(onSubmitted).toHaveBeenCalledWith({
        submitted: true,
        idea: SUBMITTED,
        failedUploads: ['fake.pdf', 'lost.pdf'],
      }),
    )
  })

  it('uploads nothing when the idea could not be created', async () => {
    createMock.mockResolvedValue({
      success: false,
      message: 'You must be an active member of this organization to file ideas here.',
      field: null,
      idea: null,
    })
    render(<IdeaForm organizationId="3" onSaved={vi.fn()} onSubmitted={vi.fn()} />)
    await readyToSubmit()
    chooseFiles(file('form.pdf'))

    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('You must be an active member')
    expect(uploadMock).not.toHaveBeenCalled()
    expect(submitMock).not.toHaveBeenCalled()
    // Still chosen, ready for another try.
    expect(screen.getByText('form.pdf')).toBeInTheDocument()
  })

  it('uploads straight to an existing idea, one file after another', async () => {
    attachmentsMock.mockResolvedValue(pageOf([attachment('existing.pdf')]))
    uploadMock
      .mockResolvedValueOnce({
        success: true,
        message: 'ok',
        field: null,
        attachment: attachment('first.pdf'),
      })
      .mockResolvedValueOnce({
        success: false,
        message: 'That file does not match its type.',
        field: 'file',
        attachment: null,
      })
    render(<IdeaForm organizationId="3" idea={CREATED} onSaved={vi.fn()} />)
    goToStep(EVIDENCE_STEP)
    await screen.findByText('existing.pdf')

    chooseFiles(file('first.pdf'), file('fake.pdf'))

    // Both settled: the refused one says so, the accepted one is attached.
    expect(await screen.findByText('Could not be attached')).toBeInTheDocument()
    const list = screen.getByRole('list', { name: 'Attached files' })
    expect(within(list).getByText('first.pdf')).toBeInTheDocument()
    expect(screen.getByText('That file does not match its type.')).toBeInTheDocument()
    expect(uploadMock).toHaveBeenNthCalledWith(
      1,
      '42',
      expect.objectContaining({ name: 'first.pdf' }),
    )
    expect(uploadMock).toHaveBeenNthCalledWith(
      2,
      '42',
      expect.objectContaining({ name: 'fake.pdf' }),
    )
    // Both earlier files are still listed: each upload lands after the last.
    expect(within(list).getByText('existing.pdf')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss fake.pdf' }))
    expect(within(list).queryByText('fake.pdf')).not.toBeInTheDocument()
  })

  it('shows an existing idea its live evidence instead of holding files', async () => {
    attachmentsMock.mockResolvedValue(pageOf([attachment('existing.pdf')]))
    render(<IdeaForm organizationId="3" idea={CREATED} onSaved={vi.fn()} onSubmitted={vi.fn()} />)

    goToStep(EVIDENCE_STEP)

    expect(await screen.findByText('existing.pdf')).toBeInTheDocument()
    expect(attachmentsMock.mock.calls[0][0]).toBe('42')
    expect(screen.queryByText('No files chosen. This step is optional.')).not.toBeInTheDocument()
    // Editing never submits from the form: that stays the list's own action.
    expect(screen.queryByRole('button', { name: 'Submit for review' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeInTheDocument()
  })
})

function stepButtonState(): string {
  return screen.getByRole('button', { name: /^Step 8:/ }).getAttribute('aria-label') ?? ''
}
