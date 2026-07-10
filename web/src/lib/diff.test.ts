import { describe, expect, it } from 'vitest'
import { parseUnifiedDiff } from './diff'

const DIFF = `--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 import os
-x = 1
+x = 2
+y = 3
 print(x)
@@ -10,2 +11,2 @@
 tail
-old
+new
`

describe('parseUnifiedDiff', () => {
  it('parses hunks with correct line numbers', () => {
    const hunks = parseUnifiedDiff(DIFF)
    expect(hunks).toHaveLength(2)
    expect(hunks[0].header).toBe('@@ -1,3 +1,4 @@')
    expect(hunks[0].lines).toEqual([
      { kind: 'ctx', oldNo: 1, newNo: 1, text: 'import os' },
      { kind: 'del', oldNo: 2, newNo: null, text: 'x = 1' },
      { kind: 'add', oldNo: null, newNo: 2, text: 'x = 2' },
      { kind: 'add', oldNo: null, newNo: 3, text: 'y = 3' },
      { kind: 'ctx', oldNo: 3, newNo: 4, text: 'print(x)' },
    ])
    expect(hunks[1].lines[1]).toEqual({ kind: 'del', oldNo: 11, newNo: null, text: 'old' })
  })

  it('handles empty and headerless input', () => {
    expect(parseUnifiedDiff('')).toEqual([])
  })
})
