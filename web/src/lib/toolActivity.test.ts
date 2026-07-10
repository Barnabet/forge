import { describe, expect, it } from 'vitest'
import type { StreamItem } from '../state/reducer'
import { familyOf, groupLabel, relDisplay, segmentItems, toolVerb, type ToolItem } from './toolActivity'

const tool = (over: Partial<ToolItem>): ToolItem => ({
  kind: 'tool', seq: 1, callId: 'c1', tool: 'bash', display: 'ls',
  status: 'done', output: '', durationMs: 0, diffStats: null, autoApproved: false,
  ...over,
})

describe('familyOf', () => {
  it('maps engine tools to families', () => {
    expect(familyOf('read_file')).toBe('read')
    expect(familyOf('write_file')).toBe('edit')
    expect(familyOf('edit_file')).toBe('edit')
    expect(familyOf('bash')).toBe('run')
    expect(familyOf('grep')).toBe('search')
    expect(familyOf('glob')).toBe('search')
    expect(familyOf('list_dir')).toBe('search')
    expect(familyOf('load_skill')).toBe('skill')
  })

  it('gives unknown tools their own family', () => {
    expect(familyOf('web_fetch')).toBe('other:web_fetch')
    expect(familyOf('web_fetch')).not.toBe(familyOf('bash'))
  })
})

describe('relDisplay', () => {
  it('strips the cwd prefix from paths', () => {
    expect(relDisplay('/w/proj/src/app.py', '/w/proj')).toBe('src/app.py')
  })

  it('strips every occurrence inside a command string', () => {
    expect(relDisplay('sed -n 1,5p /w/proj/a.py /w/proj/b.py', '/w/proj'))
      .toBe('sed -n 1,5p a.py b.py')
  })

  it('respects path boundaries and leaves outside paths absolute', () => {
    expect(relDisplay('/www/x.py', '/w')).toBe('/www/x.py')
    expect(relDisplay('~/.forge/config.toml', '/w/proj')).toBe('~/.forge/config.toml')
  })

  it('falls back sensibly for the cwd itself and empty cwd', () => {
    expect(relDisplay('/w/proj/', '/w/proj')).toBe('.')
    expect(relDisplay('/w/proj/a.py', '')).toBe('/w/proj/a.py')
  })
})

describe('toolVerb', () => {
  it('conjugates on status', () => {
    expect(toolVerb(tool({ tool: 'read_file', status: 'running' }))).toBe('Reading')
    expect(toolVerb(tool({ tool: 'read_file', status: 'done' }))).toBe('Read')
    expect(toolVerb(tool({ tool: 'bash', status: 'running' }))).toBe('Running')
    expect(toolVerb(tool({ tool: 'bash', status: 'done' }))).toBe('Ran')
    expect(toolVerb(tool({ tool: 'edit_file', status: 'error' }))).toBe('Edited')
  })

  it('falls back to Running/Ran for unknown tools', () => {
    expect(toolVerb(tool({ tool: 'web_fetch', status: 'running' }))).toBe('Running')
    expect(toolVerb(tool({ tool: 'web_fetch', status: 'done' }))).toBe('Ran')
  })
})

describe('groupLabel', () => {
  it('labels families with count and noun', () => {
    expect(groupLabel([
      tool({ tool: 'bash', callId: 'a' }), tool({ tool: 'bash', callId: 'b' }),
      tool({ tool: 'bash', callId: 'c' }),
    ])).toBe('Ran 3 commands')
    expect(groupLabel([
      tool({ tool: 'grep', display: 'foo', callId: 'a' }),
      tool({ tool: 'list_dir', display: 'web/', callId: 'b' }),
    ])).toBe('Ran 2 searches')
  })

  it('uses present tense while any member runs', () => {
    expect(groupLabel([
      tool({ tool: 'read_file', display: 'a.py', callId: 'a' }),
      tool({ tool: 'read_file', display: 'b.py', callId: 'b', status: 'running' }),
    ])).toBe('Reading 2 files')
  })

  it('counts unique files for read/edit, not calls', () => {
    expect(groupLabel([
      tool({ tool: 'read_file', display: 'a.py', callId: 'a' }),
      tool({ tool: 'read_file', display: 'a.py', callId: 'b' }),
      tool({ tool: 'read_file', display: 'b.py', callId: 'c' }),
    ])).toBe('Read 2 files')
    expect(groupLabel([
      tool({ tool: 'read_file', display: 'a.py', callId: 'a' }),
      tool({ tool: 'read_file', display: 'a.py', callId: 'b' }),
    ])).toBe('Read 1 file')
  })

  it('labels unknown-tool groups by tool name', () => {
    expect(groupLabel([
      tool({ tool: 'web_fetch', callId: 'a' }), tool({ tool: 'web_fetch', callId: 'b' }),
    ])).toBe('Ran web_fetch × 2')
  })
})

describe('segmentItems', () => {
  const prose = (seq: number): StreamItem => ({ kind: 'prose', seq, text: 'p', streaming: false })

  it('groups consecutive tools by family in first-seen order', () => {
    const entries = segmentItems([
      tool({ tool: 'read_file', callId: 'r1' }),
      tool({ tool: 'bash', callId: 'b1' }),
      tool({ tool: 'read_file', callId: 'r2' }),
    ])
    expect(entries).toHaveLength(1)
    const e = entries[0]
    if (e.kind !== 'tools') throw new Error('expected tools entry')
    expect(e.groups.map(g => g.map(t => t.callId))).toEqual([['r1', 'r2'], ['b1']])
    expect(e.key).toBe('t:r1')
  })

  it('groups edits per file, not per family', () => {
    const entries = segmentItems([
      tool({ tool: 'edit_file', callId: 'e1', display: 'a.py' }),
      tool({ tool: 'write_file', callId: 'e2', display: 'b.py' }),
      tool({ tool: 'edit_file', callId: 'e3', display: 'a.py' }),
    ])
    expect(entries).toHaveLength(1)
    const e = entries[0]
    if (e.kind !== 'tools') throw new Error('expected tools entry')
    expect(e.groups.map(g => g.map(t => t.callId))).toEqual([['e1', 'e3'], ['e2']])
  })

  it('breaks tool runs on any non-tool item', () => {
    const entries = segmentItems([
      tool({ tool: 'bash', callId: 'b1' }),
      prose(5),
      tool({ tool: 'bash', callId: 'b2' }),
    ])
    expect(entries.map(e => e.kind)).toEqual(['tools', 'item', 'tools'])
    const [first, , third] = entries
    if (first.kind !== 'tools' || third.kind !== 'tools') throw new Error('expected tools entries')
    expect(first.groups[0].map(t => t.callId)).toEqual(['b1'])
    expect(third.groups[0].map(t => t.callId)).toEqual(['b2'])
  })

  it('keys non-tool items by seq, streaming prose by index', () => {
    const entries = segmentItems([
      prose(3),
      { kind: 'prose', seq: 0, text: 'streaming', streaming: true },
    ])
    expect(entries.map(e => e.key)).toEqual(['s:3', 'i:1'])
  })
})
