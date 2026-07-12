import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'
import { formatLastTs } from '../lib/time'
import { useForge } from '../state/store'
import type {
  EvaluationDetail, EvaluationSummary, LeaderboardEntry, ModelInfo, OrchestratorFacet,
} from '../protocol'
import s from './SubagentLeaderboard.module.css'

const DIMS = [
  { key: 'overall', label: 'Overall', avg: 'avg_overall' },
  { key: 'work_quality', label: 'Work', avg: 'avg_work_quality' },
  { key: 'information_delivery', label: 'Delivery', avg: 'avg_information_delivery' },
  { key: 'efficiency', label: 'Efficiency', avg: 'avg_efficiency' },
] as const

function nameFor(models: ModelInfo[], id: string): string {
  return models.find(m => m.id === id)?.display_name ?? id
}

const round = (n: number) => Math.round(n)

function ScoreRail({ entry }: { entry: LeaderboardEntry }) {
  if (entry.sample_count === 0) {
    return <div className={s.railEmpty}>No successful grades yet</div>
  }
  return (
    <>
      {DIMS.map(d => {
        const v = entry[d.avg] as number
        return (
          <div key={d.key} className={s.rail}>
            <span className={s.railLabel}>{d.label}</span>
            <span className={s.railTrack}>
              <span
                className={s.railFill}
                data-dim={d.key}
                style={{ width: `${Math.max(0, Math.min(100, v))}%` }}
              />
            </span>
            <span className={s.railVal}>{round(v)}</span>
          </div>
        )
      })}
    </>
  )
}

function Ranking({ board, models }: { board: LeaderboardEntry[]; models: ModelInfo[] }) {
  if (board.length === 0) {
    return (
      <div className={s.state}>
        <div className={s.stateMsg}>
          Scores appear here after a subagent report is graded.
        </div>
      </div>
    )
  }
  return (
    <>
      {board.map((e, i) => (
        <div key={e.model} className={s.rankRow}>
          <span className={s.rank}>{i + 1}</span>
          <div className={s.rankMain}>
            <div className={s.rankName}>{nameFor(models, e.model)}</div>
            <div className={s.rankMeta}>
              <span>{e.sample_count} graded</span>
              {e.error_count > 0 && (
                <span className={s.rankErr}>{e.error_count} failed evaluation{e.error_count > 1 ? 's' : ''}</span>
              )}
            </div>
          </div>
          <span className={e.sample_count > 0 ? s.rankScore : s.rankScoreEmpty}>
            {e.sample_count > 0 ? round(e.avg_overall) : '—'}
          </span>
          <ScoreRail entry={e} />
        </div>
      ))}
    </>
  )
}

function Detail({ id, models }: { id: string; models: ModelInfo[] }) {
  const [rec, setRec] = useState<EvaluationDetail | null>(null)
  const [error, setError] = useState(false)
  // Monotonic token: only the latest request (per id, or after a retry) may
  // set state. Bumping it on cleanup invalidates any in-flight fetch.
  const reqRef = useRef(0)

  const load = useCallback(() => {
    const token = ++reqRef.current
    setError(false)
    setRec(null)
    api.subagentEvaluation(id)
      .then(r => { if (reqRef.current === token) setRec(r) })
      .catch(() => { if (reqRef.current === token) setError(true) })
  }, [id])

  useEffect(() => {
    load()
    return () => { reqRef.current++ }
  }, [load])

  if (error) {
    return (
      <div className={s.state}>
        <div className={`${s.stateMsg} ${s.stateErr}`}>Couldn’t load this evaluation.</div>
        <button className={s.retry} onClick={load}>Try again</button>
      </div>
    )
  }
  if (!rec) return <div className={s.inlineState}>Loading evaluation…</div>

  const meta: [string, string][] = [
    ['Turns', String(rec.turn_count)],
    ['Tool calls', String(rec.tool_call_count)],
    ['Tokens', rec.usage_tokens.toLocaleString()],
    ['Duration', `${(rec.duration_ms / 1000).toFixed(1)}s`],
    ['Grader', nameFor(models, rec.grader_model)],
    ['Orchestrator', `${rec.orchestrator_model ? nameFor(models, rec.orchestrator_model) : 'Unknown orchestrator'}${rec.orchestrator_model_inferred ? ' · historically inferred' : ''}`],
    ['Worker', `#${rec.worker_index} · ${rec.mode}`],
    ['Session', rec.session_id],
    ['Call', rec.call_id],
  ]

  return (
    <div className={s.detail}>
      <div>
        <div className={s.detailHeadName}>{nameFor(models, rec.subagent_model)}</div>
        <div className={s.detailHeadSub}>{formatLastTs(rec.timestamp)} · {rec.task}</div>
      </div>

      {rec.status === 'error' || !rec.grade ? (
        <div className={s.block}>
          <div className={s.blockTitle}>Grading error</div>
          <div className={`${s.prose} ${s.stateErr}`}>{rec.error ?? 'Grade unavailable.'}</div>
        </div>
      ) : (
        <>
          <div className={s.scoreGrid}>
            {DIMS.map(d => (
              <div key={d.key} className={s.scoreCell} data-dim={d.key}>
                <div className={s.scoreCellVal}>{rec.grade![d.key]}</div>
                <div className={s.scoreCellLabel}>{d.label}</div>
              </div>
            ))}
          </div>
          <div className={s.block}>
            <div className={s.blockTitle}>Rationale</div>
            <div className={s.prose}>{rec.grade.rationale}</div>
          </div>
          {rec.grade.strengths.length > 0 && (
            <div className={s.block}>
              <div className={s.blockTitle}>Strengths</div>
              <ul className={s.list}>{rec.grade.strengths.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </div>
          )}
          {rec.grade.issues.length > 0 && (
            <div className={s.block}>
              <div className={s.blockTitle}>Issues</div>
              <ul className={`${s.list} ${s.listIssue}`}>{rec.grade.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>
            </div>
          )}
        </>
      )}

      <div className={s.block}>
        <div className={s.blockTitle}>Run</div>
        <div className={s.meta}>
          {meta.map(([k, v]) => (
            <div key={k} className={s.metaItem}>
              <span className={s.metaKey}>{k}</span>
              <span className={s.metaVal} title={v}>{v}</span>
            </div>
          ))}
        </div>
      </div>

      {rec.final_report && (
        <div className={s.block}>
          <div className={s.blockTitle}>Final report</div>
          <pre className={s.pre}>{rec.final_report}</pre>
        </div>
      )}
      {rec.parent_context && (
        <div className={s.block}>
          <div className={s.blockTitle}>Parent context</div>
          <pre className={s.pre}>{rec.parent_context}</pre>
        </div>
      )}
      {rec.worker_messages.length > 0 && (
        <div className={s.block}>
          <div className={s.blockTitle}>Worker transcript</div>
          <pre className={s.pre}>{JSON.stringify(rec.worker_messages, null, 2)}</pre>
        </div>
      )}
      {rec.raw_grader_response && (
        <div className={s.block}>
          <div className={s.blockTitle}>Raw grader response</div>
          <pre className={s.pre}>{rec.raw_grader_response}</pre>
        </div>
      )}
    </div>
  )
}

export default function SubagentLeaderboard({ onClose }: { onClose(): void }) {
  const models = useForge(st => st.models)
  const [board, setBoard] = useState<LeaderboardEntry[] | null>(null)
  const [evals, setEvals] = useState<EvaluationSummary[] | null>(null)
  const [error, setError] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [view, setView] = useState<'overall' | 'orchestrator'>('overall')
  const [facets, setFacets] = useState<OrchestratorFacet[]>([])
  const [orchestrator, setOrchestrator] = useState<string | null>(null)
  const [facetsLoaded, setFacetsLoaded] = useState(false)
  const [facetsError, setFacetsError] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  const reqRef = useRef(0)
  const facetsReqRef = useRef(0)
  const invalidateRequests = useCallback(() => {
    reqRef.current++
    facetsReqRef.current++
  }, [])

  const load = useCallback((scope?: string) => {
    const token = ++reqRef.current
    setError(false)
    setBoard(null)
    setEvals(null)
    Promise.all([api.subagentLeaderboard(scope), api.subagentEvaluations(50, 0, scope)])
      .then(([b, e]) => { if (reqRef.current === token) { setBoard(b); setEvals(e) } })
      .catch(() => { if (reqRef.current === token) setError(true) })
  }, [])

  const loadFacets = useCallback(() => {
    const token = ++facetsReqRef.current
    setFacetsError(false)
    setFacetsLoaded(false)
    api.subagentOrchestrators()
      .then(f => {
        if (facetsReqRef.current !== token) return
        setFacets(f)
        setFacetsLoaded(true)
      })
      .catch(() => {
        if (facetsReqRef.current !== token) return
        setFacetsError(true)
        setFacetsLoaded(true)
      })
  }, [])

  useEffect(() => {
    load()
    loadFacets()
    return invalidateRequests
  }, [invalidateRequests, load, loadFacets])

  const chooseView = (next: 'overall' | 'orchestrator') => {
    if (next === view) return
    setView(next)
    setSelected(null)
    if (next === 'overall') {
      setOrchestrator(null)
      load()
      return
    }
    if (!facetsLoaded || facetsError) return
    const firstKnown = facets.find(f => f.model !== null)?.model ?? null
    setOrchestrator(firstKnown)
    if (firstKnown) load(firstKnown)
    else { reqRef.current++; setBoard([]); setEvals([]); setError(false) }
  }

  const chooseOrchestrator = (model: string) => {
    if (model === orchestrator) return
    setOrchestrator(model)
    setSelected(null)
    load(model)
  }

  useEffect(() => {
    if (view !== 'orchestrator' || orchestrator || !facetsLoaded || facetsError) return
    const firstKnown = facets.find(f => f.model !== null)?.model
    if (firstKnown) { setOrchestrator(firstKnown); load(firstKnown) }
  }, [facets, facetsError, facetsLoaded, load, orchestrator, view])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      if (selected) setSelected(null)
      else onClose()
    }
    window.addEventListener('keydown', onKey)
    closeRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, selected])

  const loading = !error && (board === null || evals === null)

  return createPortal(
    <div
      className={s.overlay}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className={s.card} role="dialog" aria-modal="true" aria-label="Subagent leaderboard">
        <header className={s.header}>
          <div className={s.title}>Subagent leaderboard</div>
          <div className={s.subtitle}>DYNAMIC BENCHMARK</div>
          <button ref={closeRef} className={s.close} aria-label="Close leaderboard" onClick={onClose}>✕</button>
        </header>

        <div className={s.filters}>
          <div className={s.segmented} role="group" aria-label="Leaderboard view">
            <button aria-pressed={view === 'overall'} onClick={() => chooseView('overall')}>Overall</button>
            <button aria-pressed={view === 'orchestrator'} onClick={() => chooseView('orchestrator')}>By orchestrator</button>
          </div>
          {view === 'orchestrator' && (
            <div className={s.chips} aria-label="Orchestrators">
              {facets.map(f => f.model === null ? (
                <button key="unknown" className={s.chip} disabled aria-pressed={false}>
                  <span>Unknown orchestrator</span><small>{f.sample_count} samples · {f.record_count} runs</small>
                </button>
              ) : (
                <button key={f.model} className={s.chip} aria-pressed={orchestrator === f.model} onClick={() => chooseOrchestrator(f.model!)}>
                  <span>{nameFor(models, f.model)}</span><small>{f.sample_count} samples · {f.record_count} runs</small>
                </button>
              ))}
            </div>
          )}
          {view === 'orchestrator' && facetsLoaded && facetsError ? (
            <div className={s.unknownNote}>
              Couldn’t load orchestrator groups.{' '}
              <button className={s.retry} onClick={loadFacets}>Try again</button>
            </div>
          ) : view === 'orchestrator' && facetsLoaded && !facets.some(f => f.model !== null) ? (
            <div className={s.unknownNote}>Historical runs have no filterable orchestrator metadata.</div>
          ) : null}
        </div>

        {error ? (
          <div className={s.state}>
            <div className={`${s.stateMsg} ${s.stateErr}`}>Couldn’t load benchmark data.</div>
            <button className={s.retry} onClick={() => load(view === 'orchestrator' ? orchestrator ?? undefined : undefined)}>Try again</button>
          </div>
        ) : loading ? (
          <div className={s.state}><div className={s.stateMsg}>Loading benchmark…</div></div>
        ) : (
          <div className={s.body}>
            <div className={s.col}>
              <div className={s.colHead}>
                MODEL RANKING
                <span className={s.colCount}>{board!.length}</span>
              </div>
              <div className={s.colScroll}>
                <Ranking board={board!} models={models} />
              </div>
            </div>

            <div className={s.col}>
              {selected ? (
                <>
                  <div className={s.colHead}>
                    <button className={s.detailBack} onClick={() => setSelected(null)}>← Recent</button>
                  </div>
                  <div className={s.colScroll}>
                    <Detail id={selected} models={models} />
                  </div>
                </>
              ) : (
                <>
                  <div className={s.colHead}>
                    RECENT EVALUATIONS
                    <span className={s.colCount}>{evals!.length}</span>
                  </div>
                  <div className={s.colScroll}>
                    {evals!.length === 0 ? (
                      <div className={s.state}>
                        <div className={s.stateMsg}>
                          Graded runs appear here once a subagent report is scored.
                        </div>
                      </div>
                    ) : evals!.map(e => (
                      <button
                        key={e.id}
                        className={s.evRow}
                        data-active={selected === e.id}
                        onClick={() => setSelected(e.id)}
                      >
                        <span className={s.evTime}>{formatLastTs(e.timestamp)}</span>
                        <span className={s.badge} data-mode={e.mode}>{e.mode}</span>
                        <span className={s.evBody}>
                          <span className={s.evTask}>{e.task}</span>
                          <span className={s.evSub}>{nameFor(models, e.subagent_model)}</span>
                        </span>
                        {e.status === 'error' || e.overall === null
                          ? <span className={s.evErr}>error</span>
                          : <span className={s.evScore}>{e.overall}</span>}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
