import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForge } from '../state/store'
import type {
  EvaluationDetail, EvaluationSummary, ForgeConfig, LeaderboardEntry,
} from '../protocol'
import SubagentLeaderboard from './SubagentLeaderboard'
import ConfigDrawer from './ConfigDrawer'

const MODELS = [
  { id: 'opus-5', display_name: 'Opus 5', context_window: 1 },
  { id: 'sonnet-5', display_name: 'Sonnet 5', context_window: 1 },
]

const board: LeaderboardEntry[] = [
  {
    model: 'opus-5', avg_overall: 88.4, avg_work_quality: 90, avg_information_delivery: 84,
    avg_efficiency: 79, sample_count: 6, error_count: 0, last_timestamp: 100,
  },
  {
    model: 'sonnet-5', avg_overall: 0, avg_work_quality: 0, avg_information_delivery: 0,
    avg_efficiency: 0, sample_count: 0, error_count: 1, last_timestamp: 90,
  },
]

const evals: EvaluationSummary[] = [
  {
    id: 'ev1', timestamp: 100, status: 'success', subagent_model: 'opus-5',
    grader_model: 'sonnet-5', orchestrator_model: 'sonnet-5', orchestrator_model_inferred: false,
    session_id: 'aa', project_id: null, call_id: 'sp1',
    worker_index: 1, mode: 'read', task: 'audit the reducer', overall: 88,
  },
]

const detail: EvaluationDetail = {
  ...evals[0],
  turn_count: 4, tool_call_count: 12, usage_tokens: 4210, duration_ms: 8400,
  parent_context: 'Parent asked to audit.', worker_messages: [{ role: 'user', content: 'go' }],
  final_report: 'Found three call sites.', raw_grader_response: '{"overall":88}',
  grade: {
    work_quality: 90, information_delivery: 84, efficiency: 79, overall: 88,
    rationale: 'Thorough and precise work.', strengths: ['clear report'], issues: ['missed edge case'],
  },
  error: null,
}

const CONFIG: ForgeConfig = {
  base_url: 'http://x', api_key: '', default_model: 'opus-5', default_autonomy: 'yolo',
  max_concurrent: 2, max_resident_sessions: 4, serper_api_key: '', firecrawl_api_key: '',
  openrouter_api_key: '', embedding_model: 'e', image_model: 'i',
  memory_similarity_threshold: 0.5, max_subagents: 4, subagent_max_turns: 8,
  subagent_model: '', memory_model: '', compaction_model: '',
  models: MODELS,
}

// Route by URL, honoring method; each route is a producer so retry tests can
// flip behavior between calls. Unknown routes reject loudly.
type Producer = () => { ok: boolean; status?: number; body?: unknown }
function mockFetch(routes: { match: (url: string, init?: RequestInit) => boolean; produce: Producer }[]) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const r = routes.find(x => x.match(url, init))
    if (!r) throw new Error(`unhandled fetch: ${url}`)
    const { ok, status = ok ? 200 : 500, body } = r.produce()
    return { ok, status, json: async () => body }
  })
  vi.stubGlobal('fetch', fn as unknown as typeof fetch)
  return fn
}

const leaderboardRoutes = (opts: {
  boardProducer?: Producer
  evaluationsProducer?: Producer
  detailProducer?: Producer
  orchestratorsProducer?: Producer
} = {}) => [
  {
    match: (u: string) => u.includes('/api/subagents/orchestrators'),
    produce: opts.orchestratorsProducer ?? (() => ({ ok: true, body: [
      { model: 'sonnet-5', record_count: 7, sample_count: 6, error_count: 1, last_timestamp: 100 },
      { model: null, record_count: 2, sample_count: 2, error_count: 0, last_timestamp: 90 },
    ] })),
  },
  {
    match: (u: string) => u.includes('/api/subagents/leaderboard'),
    produce: opts.boardProducer ?? (() => ({ ok: true, body: board })),
  },
  {
    match: (u: string) => /\/api\/subagents\/evaluations\/[^?]/.test(u),
    produce: opts.detailProducer ?? (() => ({ ok: true, body: detail })),
  },
  {
    match: (u: string) => u.includes('/api/subagents/evaluations?'),
    produce: opts.evaluationsProducer ?? (() => ({ ok: true, body: evals })),
  },
]

beforeEach(() => {
  useForge.setState(useForge.getInitialState(), true)
  useForge.setState({ models: MODELS })
  vi.restoreAllMocks()
})

describe('SubagentLeaderboard', () => {
  it('shows a loading state then the empty ranking/recent messages', async () => {
    mockFetch([
      { match: u => u.includes('/leaderboard'), produce: () => ({ ok: true, body: [] }) },
      { match: u => u.includes('/evaluations'), produce: () => ({ ok: true, body: [] }) },
    ])
    render(<SubagentLeaderboard onClose={vi.fn()} />)
    expect(screen.getByText('Loading benchmark…')).toBeInTheDocument()
    expect(await screen.findByText(/Scores appear here/)).toBeInTheDocument()
    expect(screen.getByText(/Graded runs appear here/)).toBeInTheDocument()
  })

  it('shows an error with a working retry', async () => {
    let fail = true
    mockFetch(leaderboardRoutes({
      boardProducer: () => (fail ? { ok: false } : { ok: true, body: board }),
    }))
    render(<SubagentLeaderboard onClose={vi.fn()} />)
    expect(await screen.findByText(/Couldn’t load benchmark data\./)).toBeInTheDocument()
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect((await screen.findAllByText('Opus 5')).length).toBeGreaterThan(0)
  })

  it('maps model display labels and renders every dimension rail', async () => {
    mockFetch(leaderboardRoutes())
    render(<SubagentLeaderboard onClose={vi.fn()} />)
    expect((await screen.findAllByText('Opus 5')).length).toBeGreaterThan(0)
    expect(screen.getByText('Sonnet 5')).toBeInTheDocument()
    for (const label of ['Overall', 'Work', 'Delivery', 'Efficiency']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
    // Model with no samples shows the empty-rail note, not a score.
    expect(screen.getByText('No successful grades yet')).toBeInTheDocument()
    expect(screen.getByText('1 failed evaluation')).toBeInTheDocument()
  })

  it('defaults overall and scopes both lists when an orchestrator is selected', async () => {
    const fetchMock = mockFetch(leaderboardRoutes())
    render(<SubagentLeaderboard onClose={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Overall' })).toHaveAttribute('aria-pressed', 'true')
    await screen.findByText('audit the reducer')
    await userEvent.click(screen.getByRole('button', { name: 'By orchestrator' }))
    expect(await screen.findByRole('button', { name: /Sonnet 5/ })).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(c => String(c[0]))
      expect(urls).toContain('/api/subagents/leaderboard?orchestrator_model=sonnet-5')
      expect(urls).toContain('/api/subagents/evaluations?limit=50&offset=0&orchestrator_model=sonnet-5')
    })
    expect(screen.getByRole('button', { name: /Unknown orchestrator/ })).toBeDisabled()
  })

  it('keeps overall data and retries separately when orchestrator groups fail', async () => {
    let fail = true
    const fetchMock = mockFetch(leaderboardRoutes({
      orchestratorsProducer: () => (fail ? { ok: false } : { ok: true, body: [
        { model: 'sonnet-5', record_count: 7, sample_count: 6, error_count: 1, last_timestamp: 100 },
      ] }),
    }))
    render(<SubagentLeaderboard onClose={vi.fn()} />)
    await screen.findByText('audit the reducer')

    await userEvent.click(screen.getByRole('button', { name: 'By orchestrator' }))
    expect(screen.getByText('Couldn’t load orchestrator groups.')).toBeInTheDocument()
    expect(screen.queryByText(/Historical runs have no filterable orchestrator metadata/)).not.toBeInTheDocument()
    expect(screen.getByText('audit the reducer')).toBeInTheDocument()

    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: /Sonnet 5/ })).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(c => String(c[0]))
      expect(urls.filter(u => u.includes('/api/subagents/orchestrators'))).toHaveLength(2)
      expect(urls).toContain('/api/subagents/leaderboard?orchestrator_model=sonnet-5')
    })
  })

  it('selecting a recent evaluation loads and renders its detail provenance', async () => {
    mockFetch(leaderboardRoutes())
    render(<SubagentLeaderboard onClose={vi.fn()} />)
    await userEvent.click(await screen.findByText('audit the reducer'))
    expect(await screen.findByText('Thorough and precise work.')).toBeInTheDocument()
    expect(screen.getByText('Found three call sites.')).toBeInTheDocument()
    expect(screen.getByText('clear report')).toBeInTheDocument()
    expect(screen.getByText('missed edge case')).toBeInTheDocument()
    expect(screen.getByText('Orchestrator')).toBeInTheDocument()
    // Worker transcript is serialized JSON.
    expect(screen.getByText(/"role": "user"/)).toBeInTheDocument()
  })

  it('detail fetch error offers a retry that recovers', async () => {
    let fail = true
    mockFetch(leaderboardRoutes({
      detailProducer: () => (fail ? { ok: false } : { ok: true, body: detail }),
    }))
    render(<SubagentLeaderboard onClose={vi.fn()} />)
    await userEvent.click(await screen.findByText('audit the reducer'))
    expect(await screen.findByText(/Couldn’t load this evaluation\./)).toBeInTheDocument()
    fail = false
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Thorough and precise work.')).toBeInTheDocument()
  })

  it('Escape backs out of the detail first, then closes', async () => {
    mockFetch(leaderboardRoutes())
    const onClose = vi.fn()
    render(<SubagentLeaderboard onClose={onClose} />)
    await userEvent.click(await screen.findByText('audit the reducer'))
    expect(await screen.findByText('Thorough and precise work.')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    // Back to the recent list, not closed.
    expect(onClose).not.toHaveBeenCalled()
    expect(await screen.findByText('RECENT EVALUATIONS')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('ConfigDrawer ↔ leaderboard', () => {
  const openDrawer = async () => {
    mockFetch([
      { match: u => u.includes('/api/config'), produce: () => ({ ok: true, body: CONFIG }) },
      ...leaderboardRoutes(),
    ])
    render(<ConfigDrawer onClose={vi.fn()} />)
    await screen.findByText('Subagent leaderboard')
  }

  it('opens the leaderboard from the settings button', async () => {
    await openDrawer()
    await userEvent.click(screen.getByRole('button', { name: 'Subagent leaderboard' }))
    expect(await screen.findByRole('dialog', { name: 'Subagent leaderboard' })).toBeInTheDocument()
  })

  it('interacting with and closing the leaderboard leaves settings open', async () => {
    const onClose = vi.fn()
    mockFetch([
      { match: u => u.includes('/api/config'), produce: () => ({ ok: true, body: CONFIG }) },
      ...leaderboardRoutes(),
    ])
    render(<ConfigDrawer onClose={onClose} />)
    await screen.findByText('Subagent leaderboard')
    await userEvent.click(screen.getByRole('button', { name: 'Subagent leaderboard' }))
    const dialog = await screen.findByRole('dialog', { name: 'Subagent leaderboard' })

    // A click inside the leaderboard must not trip the drawer's outside handler.
    await userEvent.click(await within(dialog).findByText('audit the reducer'))
    expect(onClose).not.toHaveBeenCalled()

    // One Escape backs out of detail, another closes the leaderboard only.
    await userEvent.keyboard('{Escape}')
    await userEvent.keyboard('{Escape}')
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Subagent leaderboard' })).not.toBeInTheDocument())
    // Settings drawer is still mounted and open.
    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('closing the leaderboard via its ✕ returns to open settings', async () => {
    const onClose = vi.fn()
    mockFetch([
      { match: u => u.includes('/api/config'), produce: () => ({ ok: true, body: CONFIG }) },
      ...leaderboardRoutes(),
    ])
    render(<ConfigDrawer onClose={onClose} />)
    await screen.findByText('Subagent leaderboard')
    await userEvent.click(screen.getByRole('button', { name: 'Subagent leaderboard' }))
    await screen.findByRole('dialog', { name: 'Subagent leaderboard' })
    await userEvent.click(screen.getByRole('button', { name: 'Close leaderboard' }))
    expect(screen.queryByRole('dialog', { name: 'Subagent leaderboard' })).not.toBeInTheDocument()
    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })
})
