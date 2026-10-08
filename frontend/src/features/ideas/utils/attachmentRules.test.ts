import { describe, expect, it } from 'vitest'

import { attachmentProblem, COURTESY_MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL } from './attachmentRules'

/**
 * The pre-flight rules a file gets before it costs a round trip.
 *
 * The size boundary is tested here, on the function, rather than only through a
 * component: this is arithmetic against a constant, and the cheap place to pin
 * "the limit is what we say it is" is the code that computes it. A component
 * test that has to allocate 50 MB to reach the boundary is a slow test that
 * nobody wants to keep.
 */
function file(name: string, size: number): File {
  return new File([new Uint8Array(size)], name, { type: 'application/pdf' })
}

describe('attachmentProblem', () => {
  it('accepts a file at the limit', () => {
    expect(attachmentProblem(file('evidence.pdf', COURTESY_MAX_UPLOAD_BYTES))).toBeNull()
  })

  it('refuses one byte over it', () => {
    // "or smaller", checked at the boundary rather than somewhere near it.
    expect(attachmentProblem(file('evidence.pdf', COURTESY_MAX_UPLOAD_BYTES + 1))).toBe(
      `That file is larger than ${MAX_UPLOAD_LABEL}.`,
    )
  })

  it('names the limit it enforces, in the refusal', () => {
    // The message is built from the constant, so the two cannot disagree - which
    // is the failure this guards: a reader told "larger than 10 MB" by a uploader
    // that stops at 50.
    expect(MAX_UPLOAD_LABEL).toBe('200 MB')
  })

  it('refuses an empty file', () => {
    expect(attachmentProblem(file('evidence.pdf', 0))).toBe('That file is empty.')
  })

  it('refuses an unsupported extension whatever its size', () => {
    expect(attachmentProblem(file('payload.exe', 10))).toBe('That file type is not supported.')
  })

  it('accepts an extension on the list at any allowed size', () => {
    expect(attachmentProblem(file('evidence.xlsx', COURTESY_MAX_UPLOAD_BYTES))).toBeNull()
  })
})
