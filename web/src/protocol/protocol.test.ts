/// <reference types="node" />
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { seqOf, type WireEvent } from './index'

// Vite rewrites `new URL('./x', import.meta.url)` into a dev-server asset URL,
// which readFileSync can't open — resolve the path off import.meta.url instead.
const here = dirname(fileURLToPath(import.meta.url))

describe('protocol', () => {
  it('generated bundle covers every engine event type', () => {
    const src = readFileSync(join(here, 'generated.ts'), 'utf8')
    for (const t of [
      'session_created', 'session_renamed', 'status_changed', 'autonomy_changed',
      'model_changed', 'user_message', 'assistant_message', 'tool_call_started',
      'tool_call_finished', 'approval_requested', 'approval_resolved',
      'policy_added', 'context_compacted', 'run_finished', 'error',
      'text_delta', 'output_chunk',
      'session_archived', 'session_unarchived', 'effort_changed', 'session_deleted',
    ]) expect(src).toContain(`"${t}"`)
  })

  it('exports the project and new meta fields', () => {
    const src = readFileSync(join(here, 'generated.ts'), 'utf8')
    for (const s of ['export interface Project', 'project_id', 'archived', 'effort'])
      expect(src).toContain(s)
  })

  it('seqOf defaults missing seq to 0', () => {
    const e = { type: 'text_delta', session_id: 's', text: 'x' } as WireEvent
    expect(seqOf(e)).toBe(0)
  })
})
