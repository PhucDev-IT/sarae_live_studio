import { test } from 'node:test'
import assert from 'node:assert/strict'
import { saveObsConnection } from '../src/features/studio/obsConnection.ts'

test('saving with a working connection does not force disconnect and reconnect', async () => {
  const saved = { connected: true, media_input: 'Video A' }
  const calls = []
  const result = await saveObsConnection({ media_input: 'Video A' }, {
    configure: async input => { calls.push(['save', input]); return saved },
    connect: async () => { throw new Error('Unnecessary reconnect') },
    configured: value => calls.push(['configured', value]),
    connecting: () => calls.push(['connecting']),
  })
  assert.equal(result, saved)
  assert.deepEqual(calls.map(call => call[0]), ['save', 'configured'])
})

test('reports successful saving before waiting for an offline OBS connection', async () => {
  const calls = []
  let finish
  const request = new Promise(resolve => { finish = resolve })
  const action = saveObsConnection({}, {
    configure: async () => ({ connected: false }),
    connect: () => { calls.push('connect'); return request },
    configured: () => calls.push('saved'),
    connecting: () => calls.push('connecting'),
  })
  await Promise.resolve()
  assert.deepEqual(calls, ['saved', 'connecting', 'connect'])
  finish({ connected: true })
  assert.equal((await action).connected, true)
})

test('connection failures stay visible after successful saving', async () => {
  const failure = new Error('OBS authentication failed')
  let saved = false
  await assert.rejects(saveObsConnection({}, {
    configure: async () => ({ connected: false }),
    connect: async () => { throw failure },
    configured: () => { saved = true }, connecting: () => {},
  }), error => error === failure)
  assert.equal(saved, true)
})

test('configuration failure does not start a connection or report success', async () => {
  let connected = false, saved = false
  await assert.rejects(saveObsConnection({}, {
    configure: async () => { throw new Error('Invalid port') },
    connect: async () => { connected = true; return { connected: true } },
    configured: () => { saved = true }, connecting: () => {},
  }), /Invalid port/)
  assert.equal(connected, false)
  assert.equal(saved, false)
})
