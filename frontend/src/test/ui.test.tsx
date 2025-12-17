import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { Badge, EmptyState, EvidenceBadge, MetricCard, StatusBadge } from '../components/ui'
import RunsPage from '../pages/RunsPage'

function mockFetch(payload: unknown, ok = true) {
  return vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok,
    status: ok ? 200 : 500,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => payload,
    text: async () => JSON.stringify(payload),
    blob: async () => new Blob([]),
  }))
}

describe('UI primitives', () => {
  it('renders status and evidence badges with the right tone', () => {
    render(
      <>
        <StatusBadge status="failure" />
        <EvidenceBadge status="Inferred Hypothesis" />
        <Badge tone="ok">custom</Badge>
      </>,
    )
    expect(screen.getByText('Failure')).toBeInTheDocument()
    expect(screen.getByText('Inferred Hypothesis')).toBeInTheDocument()
    expect(screen.getByText('custom')).toBeInTheDocument()
  })

  it('renders an empty state with an action', async () => {
    const onClick = vi.fn()
    render(
      <EmptyState
        title="No runs yet"
        description="Import a trace to begin."
        action={
          <button onClick={onClick} type="button">
            Import
          </button>
        }
      />,
    )
    expect(screen.getByText('No runs yet')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    expect(onClick).toHaveBeenCalled()
  })

  it('renders a metric card', () => {
    render(<MetricCard label="Runs" value="12" note="analysed" icon="◫" />)
    expect(screen.getByText('Runs')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
  })
})

describe('RunsPage', () => {
  const payload = {
    items: [
      {
        run_id: 'run-1',
        trace_id: 'trace-1',
        task: 'First task',
        status: 'success',
        started_at: new Date().toISOString(),
        agent_id: 'agent-a',
        provider: 'offline-fixture',
        model: 'demo-model',
        span_count: 4,
        token_total: 100,
        cost_usd: 0,
        duration_ms: 1200,
        source_format: 'json',
        imported_at: new Date().toISOString(),
      },
      {
        run_id: 'run-2',
        trace_id: 'trace-2',
        task: 'Second task',
        status: 'failure',
        started_at: new Date().toISOString(),
        agent_id: 'agent-b',
        provider: 'offline-fixture',
        model: 'demo-model',
        span_count: 7,
        token_total: 240,
        cost_usd: 0.01,
        duration_ms: 4200,
        source_format: 'json',
        imported_at: new Date().toISOString(),
      },
    ],
    total: 2,
    limit: 25,
    offset: 0,
    workspace_id: 'default',
  }

  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch(payload))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists stored executions from the API', async () => {
    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('First task')).toBeInTheDocument())
    expect(screen.getByText('Second task')).toBeInTheDocument()
    expect(screen.getByText(/2 matching run/)).toBeInTheDocument()
  })

  it('sends the status filter to the API', async () => {
    const fetchMock = mockFetch(payload)
    vi.stubGlobal('fetch', fetchMock)
    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('First task')).toBeInTheDocument())
    await userEvent.selectOptions(screen.getByLabelText('Filter by status'), 'failure')
    await userEvent.click(screen.getByRole('button', { name: 'Apply filters' }))
    await waitFor(() => {
      const called = fetchMock.mock.calls.map((call) => String(call[0]))
      expect(called.some((url) => url.includes('status=failure'))).toBe(true)
    })
  })

  it('shows an error state when the API fails', async () => {
    vi.stubGlobal('fetch', mockFetch({ error: { message: 'boom' } }, false))
    render(
      <MemoryRouter>
        <RunsPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('Could not load this view')).toBeInTheDocument())
    expect(screen.getByText('boom')).toBeInTheDocument()
  })
})
