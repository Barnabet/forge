// Regenerate web/src/protocol/generated.ts from the engine's Pydantic models.
// Usage: pnpm gen:protocol  (requires uv + the server venv)
import { execSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { compile } from 'json-schema-to-typescript'

const serverDir = fileURLToPath(new URL('../../server', import.meta.url))
const raw = execSync('uv run python -m forge.protocol_export', { cwd: serverDir })
const bundle = JSON.parse(raw.toString())

// Pydantic stamps a `title` on every property (e.g. "Seq", "SessionId", "Type").
// json-schema-to-typescript turns each titled property into a standalone exported
// type alias, and those names collide across the bundle's keys (Seq, Type, Text…
// recur in every schema). Strip titles from property values so the types inline
// into their parent interface; class/$def titles (the interface names) are kept.
function stripPropTitles(node, isPropertyValue) {
  if (Array.isArray(node)) {
    for (const item of node) stripPropTitles(item, false)
    return
  }
  if (!node || typeof node !== 'object') return
  if (isPropertyValue) delete node.title
  for (const [key, value] of Object.entries(node)) {
    if (key === 'properties' && value && typeof value === 'object') {
      for (const prop of Object.values(value)) stripPropTitles(prop, true)
    } else {
      stripPropTitles(value, false)
    }
  }
}
for (const schema of Object.values(bundle)) stripPropTitles(schema, false)

let out =
  '/* AUTO-GENERATED from the engine Pydantic models — do not edit.\n' +
  ' * Regenerate with: pnpm gen:protocol */\n\n'
for (const [key, schema] of Object.entries(bundle)) {
  out += await compile(schema, key, {
    bannerComment: '',
    additionalProperties: false,
  })
  out += '\n'
}
writeFileSync(new URL('../src/protocol/generated.ts', import.meta.url), out)
console.log('wrote src/protocol/generated.ts')
