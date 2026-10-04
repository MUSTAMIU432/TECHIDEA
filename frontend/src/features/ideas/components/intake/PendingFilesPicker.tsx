import { useState } from 'react'

import { attachmentProblem } from '../../utils/attachmentRules'
import { FileUploadPanel, type UploadStatus } from './FileUploadPanel'

/**
 * Supporting documents for an idea that does not exist yet.
 *
 * The attachment endpoint needs an idea to attach to, and a new idea is only
 * created when the author saves - so the files chosen here are held in the
 * form and uploaded, through the same endpoint and the same checks as any
 * other upload, straight after the draft is created (and before it is
 * submitted, so a reviewer sees them). Nothing is sent from this component,
 * and there is no second upload path: this is a list of `File`s, and
 * `statuses` is the form telling it how each upload is going.
 */
export function PendingFilesPicker({
  files,
  onChange,
  statuses = [],
  disabled = false,
}: {
  files: readonly File[]
  onChange: (files: File[]) => void
  /** Per file, by position, while the form is attaching them. */
  statuses?: readonly UploadStatus[]
  disabled?: boolean
}) {
  const [problems, setProblems] = useState<string[]>([])

  function add(picked: File[]) {
    const accepted: File[] = []
    const refused: string[] = []
    for (const file of picked) {
      // A courtesy check only; the server checks the bytes whatever passes here.
      const problem = attachmentProblem(file)
      if (problem === null) accepted.push(file)
      else refused.push(`${file.name}: ${problem}`)
    }
    setProblems(refused)
    if (accepted.length > 0) onChange([...files, ...accepted])
  }

  return (
    <FileUploadPanel
      listLabel="Files to attach"
      emptyText="No files chosen. This step is optional."
      disabled={disabled}
      busy={statuses.includes('uploading')}
      problems={problems}
      onFiles={add}
      rows={files.map((file, index) => ({
        // Two picks of one file are two entries; the index keeps them apart.
        key: `${file.name}-${file.size}-${index}`,
        name: file.name,
        size: file.size,
        status: statuses[index] ?? 'ready',
        note:
          (statuses[index] ?? 'ready') === 'ready' ? 'Attached when you save or submit' : undefined,
        onRemove: disabled ? undefined : () => onChange(files.filter((_, i) => i !== index)),
      }))}
    />
  )
}
