import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api/automationApi'
import type { Idea } from '../../ideas/api/ideasApi'
import { AdminAutomationPage } from '../../administration/pages/AdminAutomationPage'
import { DeveloperQueuePage } from './DeveloperQueuePage'
import { IdeaAutomationPanel } from './IdeaAutomationPanel'
import { ImpactTab } from './ImpactTab'
import { OpportunitiesPage } from './OpportunitiesPage'
import { OpportunityPage } from './OpportunityPage'
import { ProjectOverview } from './ProjectOverview'

vi.mock('../api/automationApi')

const mocked = vi.mocked(api)

const CAPS = { canManageRequirements: true, canEditSolution: false, canTransition: false }

function opportunity(overrides: Partial<api.Opportunity> = {}): api.Opportunity {
  return {
    id: '7',
    ideaId: '3',
    ideaTitle: 'Automate hostel payments',
    title: 'Automate hostel payments',
    summary: '',
    problemStatement: 'Payments are tracked by hand.',
    automationGoal: '',
    expectedBenefit: '',
    priority: 'high',
    status: 'ready_for_assignment',
    submissionContext: 'organization',
    ownerName: 'Ada Lovelace',
    tenantName: 'MUNA',
    approvedAt: null,
    readyAt: null,
    createdAt: '2026-10-01T10:00:00Z',
    updatedAt: '2026-10-01T10:00:00Z',
    capabilities: CAPS,
    ...overrides,
  }
}

function project(overrides: Partial<api.Project> = {}): api.Project {
  return {
    id: '9',
    opportunityId: '7',
    ideaId: '3',
    title: 'Automate hostel payments',
    description: '',
    status: 'testing',
    submissionContext: 'organization',
    ownerName: 'Ada',
    tenantName: 'MUNA',
    assignedName: 'Dev Team',
    startDate: null,
    targetDate: null,
    completedAt: null,
    progress: {
      stages: [],
      taskCompletionPercent: null,
      tasksTotal: 0,
      tasksDone: 0,
      tasksBlocked: 0,
    },
    capabilities: { canManage: true, canPerformUat: false, canRecordImpact: true },
    ...overrides,
  }
}

beforeEach(() => {
  mocked.opportunitiesRequest.mockResolvedValue([])
  mocked.developerQueueRequest.mockResolvedValue([])
  mocked.opportunityForIdeaRequest.mockResolvedValue(null)
  mocked.traceabilityRequest.mockResolvedValue(null)
})

describe('lists', () => {
  it('says what an empty opportunity list means', async () => {
    render(
      <MemoryRouter>
        <OpportunitiesPage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('No automation opportunities yet.')).toBeInTheDocument()
    expect(
      screen.getByText(/Approved ideas will appear here when they are ready for implementation/),
    ).toBeInTheDocument()
  })

  it('shows words, never the raw status values', async () => {
    mocked.opportunitiesRequest.mockResolvedValue([opportunity()])
    render(
      <MemoryRouter>
        <OpportunitiesPage />
      </MemoryRouter>,
    )

    const card = await screen.findByRole('link', { name: /Automate hostel payments/ })
    expect(within(card).getByText('Ready for assignment')).toBeInTheDocument()
    expect(within(card).queryByText('ready_for_assignment')).not.toBeInTheDocument()
    expect(within(card).getByText(/Organization idea · MUNA/)).toBeInTheDocument()
  })

  it('filters by stage through the server', async () => {
    render(
      <MemoryRouter>
        <OpportunitiesPage />
      </MemoryRouter>,
    )
    await screen.findByText('No automation opportunities yet.')

    fireEvent.change(screen.getByLabelText('Stage'), { target: { value: 'proposal' } })

    await waitFor(() => expect(mocked.opportunitiesRequest).toHaveBeenLastCalledWith('proposal'))
  })

  it('explains an empty developer queue', async () => {
    render(
      <MemoryRouter>
        <DeveloperQueuePage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Nothing is waiting for a developer.')).toBeInTheDocument()
  })

  it('reports a failed load with a way to retry', async () => {
    mocked.opportunitiesRequest.mockRejectedValue(new Error('offline'))
    render(
      <MemoryRouter>
        <OpportunitiesPage />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not load the opportunities',
    )
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('OpportunityPage', () => {
  function renderPage() {
    return render(
      <MemoryRouter initialEntries={['/app/automation/opportunities/7']}>
        <Routes>
          <Route
            path="/app/automation/opportunities/:opportunityId"
            element={<OpportunityPage />}
          />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('does not say whether an unavailable opportunity exists', async () => {
    mocked.opportunityRequest.mockResolvedValue(null)
    renderPage()

    expect(await screen.findByText('This opportunity is not available.')).toBeInTheDocument()
  })

  it('links back to the original idea and keeps its ownership context', async () => {
    mocked.opportunityRequest.mockResolvedValue(opportunity())
    renderPage()

    expect(await screen.findByRole('link', { name: /View original idea/ })).toHaveAttribute(
      'href',
      '/app/ideas/3',
    )
    expect(screen.getByText('Organization idea')).toBeInTheDocument()
    expect(screen.getByText('MUNA')).toBeInTheDocument()
  })

  it('says what happens next without offering any early-stage button', async () => {
    mocked.opportunityRequest.mockResolvedValue(opportunity())
    renderPage()

    await screen.findByText('What happens next')
    expect(screen.getByText(/A developer or team is assigned next/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /discovery|solution design/i })).toBeNull()
  })

  it('lets a delivery manager cancel it, and says why when the server refuses', async () => {
    mocked.opportunityRequest.mockResolvedValue(
      opportunity({ capabilities: { ...CAPS, canTransition: true } }),
    )
    mocked.cancelOpportunity.mockResolvedValue({
      success: false,
      message: 'This opportunity is closed and can no longer be changed.',
      field: null,
      opportunity: null,
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: 'Cancel opportunity' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('closed')
    expect(mocked.cancelOpportunity).toHaveBeenCalledWith('7')
  })

  it('shows the proposal the owner accepted, read-only', async () => {
    mocked.opportunityRequest.mockResolvedValue(opportunity())
    mocked.proposalRequest.mockResolvedValue({
      id: '1',
      opportunityId: '7',
      title: 'Payments dashboard',
      executiveSummary: 'Stop re-typing payments.',
      problem: '',
      proposedSolution: '',
      requirementsSummary: '',
      scope: 'Import, match, report.',
      deliverables: '',
      risks: '',
      assumptions: '',
      estimatedEffort: '',
      estimatedTimeline: '6 weeks',
      acceptanceCriteria: '',
      status: 'accepted',
      reviewFeedback: '',
      submittedAt: null,
      reviewedAt: null,
    })
    renderPage()

    fireEvent.click(await screen.findByRole('tab', { name: 'Proposal' }))

    expect(await screen.findByText('Payments dashboard')).toBeInTheDocument()
    expect(screen.getByText('Import, match, report.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save|submit|accept/i })).toBeNull()
  })
})

describe('IdeaAutomationPanel', () => {
  const idea = (status: string) =>
    ({
      id: '3',
      title: 'Automate hostel payments',
      status,
      submissionContext: 'ORGANIZATION',
      tenantName: 'MUNA',
    }) as unknown as Idea

  it('is silent for an idea that is not ready', async () => {
    render(
      <MemoryRouter>
        <IdeaAutomationPanel idea={idea('APPROVED')} />
      </MemoryRouter>,
    )

    await waitFor(() => expect(mocked.opportunityForIdeaRequest).toHaveBeenCalled())
    expect(screen.queryByText('Create Automation Opportunity')).not.toBeInTheDocument()
  })

  it('offers no way to create one: the go-ahead opens it', async () => {
    render(
      <MemoryRouter>
        <IdeaAutomationPanel idea={idea('READY_FOR_IMPLEMENTATION')} />
      </MemoryRouter>,
    )

    expect(await screen.findByText(/The delivery team will assign a developer/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /create/i })).toBeNull()
  })

  it('links to the opportunity once one exists', async () => {
    mocked.opportunityForIdeaRequest.mockResolvedValue(opportunity())
    render(
      <MemoryRouter>
        <IdeaAutomationPanel idea={idea('READY_FOR_IMPLEMENTATION')} />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('link', { name: 'View opportunity' })).toHaveAttribute(
      'href',
      '/app/automation/opportunities/7',
    )
    expect(screen.queryByRole('button', { name: 'Create Opportunity' })).not.toBeInTheDocument()
  })
})

describe('project', () => {
  it("explains why UAT is blocked in the server's words", async () => {
    mocked.moveProject.mockResolvedValue({
      success: false,
      message: 'UAT cannot begin because 2 required test cases have failed.',
      field: null,
      project: null,
    })
    render(
      <MemoryRouter>
        <ProjectOverview project={project()} onChanged={() => undefined} />
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Submit for UAT' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'UAT cannot begin because 2 required test cases have failed.',
    )
    expect(mocked.moveProject).toHaveBeenCalledWith('uat', '9')
  })

  it('shows no action to someone who cannot manage the project', () => {
    render(
      <MemoryRouter>
        <ProjectOverview
          project={project({
            capabilities: { canManage: false, canPerformUat: false, canRecordImpact: false },
          })}
          onChanged={() => undefined}
        />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('button', { name: 'Submit for UAT' })).not.toBeInTheDocument()
  })

  it('does not invent progress when there are no tasks', () => {
    render(
      <MemoryRouter>
        <ProjectOverview project={project()} onChanged={() => undefined} />
      </MemoryRouter>,
    )

    expect(screen.getByText('No tasks have been added yet.')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })
})

describe('ImpactTab', () => {
  it('says nothing was recorded rather than showing numbers', async () => {
    mocked.impactRequest.mockResolvedValue(null)
    render(<ImpactTab project={project({ status: 'uat' })} onChanged={() => undefined} />)

    expect(await screen.findByText('No impact data yet.')).toBeInTheDocument()
    expect(screen.getByText('Impact measurement has not been recorded.')).toBeInTheDocument()
  })

  it("shows 'Not enough data' for a result the server could not calculate, and keeps estimated apart from measured", async () => {
    mocked.impactRequest.mockResolvedValue({
      id: '1',
      status: 'recorded',
      qualitativeOutcome: '',
      notes: '',
      usersAffected: null,
      beforeProcessingMinutes: '240',
      afterProcessingMinutes: null,
      beforePeopleInvolved: null,
      afterPeopleInvolved: null,
      beforeErrorRate: null,
      afterErrorRate: null,
      beforeCost: null,
      afterCost: null,
      estimatedHoursSavedPerWeek: '20',
      measuredHoursSavedPerWeek: null,
      estimatedCostSavings: null,
      measuredCostSavings: null,
      results: {
        timeSavedPercent: null,
        peopleReduced: null,
        errorReductionPercent: null,
        workloadReductionPercent: null,
        costDifference: null,
        requestsDifference: null,
        estimatedHoursSavedPerWeek: '20',
        measuredHoursSavedPerWeek: null,
        hoursSavedVariance: null,
      },
    })
    render(<ImpactTab project={project({ status: 'deployed' })} onChanged={() => undefined} />)

    expect((await screen.findAllByText('Not enough data')).length).toBe(3)
    expect(screen.getByText('20 hours/week')).toBeInTheDocument()
    expect(screen.getByText('Not recorded')).toBeInTheDocument()
    expect(screen.queryByText(/Measured vs estimated/)).not.toBeInTheDocument()
  })

  it('only offers the impact form to someone who may record it', async () => {
    mocked.impactRequest.mockResolvedValue(null)
    render(
      <ImpactTab
        project={project({
          status: 'deployed',
          capabilities: { canManage: false, canPerformUat: false, canRecordImpact: false },
        })}
        onChanged={() => undefined}
      />,
    )

    await screen.findByText('No impact data yet.')
    expect(screen.queryByRole('form', { name: 'Record impact' })).not.toBeInTheDocument()
  })
})

describe('console', () => {
  it('lists opportunities and projects read-only, in words', async () => {
    mocked.adminOpportunitiesRequest.mockResolvedValue([opportunity()])
    mocked.adminProjectsRequest.mockResolvedValue([project()])
    render(
      <MemoryRouter>
        <AdminAutomationPage />
      </MemoryRouter>,
    )

    const opportunities = await screen.findByRole('table', { name: 'Automation opportunities' })
    expect(within(opportunities).getByText('Ready for assignment')).toBeInTheDocument()
    expect(within(opportunities).getByText('Organization idea')).toBeInTheDocument()
    const projects = await screen.findByRole('table', { name: 'Automation projects' })
    expect(within(projects).getByText('Testing')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says plainly when there is nothing to inspect', async () => {
    mocked.adminOpportunitiesRequest.mockResolvedValue([])
    mocked.adminProjectsRequest.mockResolvedValue([])
    render(
      <MemoryRouter>
        <AdminAutomationPage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('No automation opportunities.')).toBeInTheDocument()
    expect(await screen.findByText('No projects.')).toBeInTheDocument()
  })
})
