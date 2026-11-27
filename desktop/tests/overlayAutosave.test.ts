import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createOverlayAutosave } from '../src/features/overlay/overlayAutosave.ts'

function deferred() {
  let resolve, reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}
function setup(persist, validate = () => null) {
  let current = { start_ms: 1200, duration_ms: 8500 }
  const states = [], errors = [], saved = []
  const autosave = createOverlayAutosave({ snapshot: () => current, validate, persist,
    onState: value => states.push(value), onError: error => errors.push(error), onSaved: value => saved.push(value) })
  return { autosave, states, errors, saved, edit: value => { current = { ...current, ...value }; autosave.changed() } }
}

test('closing waits for the running save before allowing the editor to unmount', async () => {
  const request = deferred()
  const editor = setup(() => request.promise)
  editor.edit({ duration_ms: 12500 })
  const saving = editor.autosave.flush()
  let closed = false
  const close = editor.autosave.flush().then(ok => { closed = ok })
  assert.equal(closed, false)
  request.resolve({ duration_ms: 12500 })
  assert.equal(await saving, true)
  await close
  assert.equal(closed, true)
  assert.equal(editor.states.at(-1), 'saved')
})

test('serializes edits during an in-flight save and marks saved only for the latest duration', async () => {
  const first = deferred(), second = deferred(), calls = []
  const editor = setup(value => { calls.push(value); return calls.length === 1 ? first.promise : second.promise })
  editor.edit({ duration_ms: 6000 })
  const flush = editor.autosave.flush()
  editor.edit({ duration_ms: 17500 })
  assert.equal(editor.autosave.flush(), flush)
  assert.equal(calls.length, 1)
  first.resolve({ duration_ms: 6000 })
  await Promise.resolve()
  assert.equal(calls.length, 2)
  assert.deepEqual(calls.map(value => value.duration_ms), [6000, 17500])
  assert.equal(editor.saved.length, 0)
  second.resolve({ duration_ms: 17500 })
  assert.equal(await flush, true)
  assert.deepEqual(editor.saved, [{ duration_ms: 17500 }])
})

test('failed saving blocks close and retry persists the unchanged draft', async () => {
  let attempts = 0
  const editor = setup(async value => { if (++attempts === 1) throw new Error('Core unavailable'); return value })
  editor.edit({ duration_ms: 23000 })
  assert.equal(await editor.autosave.flush(), false)
  assert.equal(editor.states.at(-1), 'error')
  assert.equal(editor.saved.length, 0)
  assert.equal(await editor.autosave.flush(), true)
  assert.equal(editor.saved[0].duration_ms, 23000)
})

test('invalid duration blocks save and close, then saving succeeds after correction', async () => {
  let writes = 0
  const editor = setup(async value => { writes++; return value }, value => value.duration_ms > 10000 ? 'Overlay ends after video' : null)
  editor.edit({ duration_ms: 12000 })
  assert.equal(await editor.autosave.flush(), false)
  assert.equal(writes, 0)
  assert.equal(editor.states.at(-1), 'blocked')
  assert.equal(editor.errors[0].message, 'Overlay ends after video')
  editor.edit({ duration_ms: 8500 })
  assert.equal(await editor.autosave.flush(), true)
  assert.equal(writes, 1)
})

test('saved or untouched editor closes without rewriting the overlay list', async () => {
  let writes = 0
  const editor = setup(async value => { writes++; return value })
  assert.equal(await editor.autosave.flush(), true)
  editor.edit({ duration_ms: 7500 })
  assert.equal(await editor.autosave.flush(), true)
  assert.equal(await editor.autosave.flush(), true)
  assert.equal(writes, 1)
})
