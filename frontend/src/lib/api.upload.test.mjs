import assert from 'node:assert/strict'
import test from 'node:test'

class FakeFormData {
  items = []
  append(...values) { this.items.push(values) }
}

class FakeXhr {
  upload = {}
  headers = {}
  status = 0
  responseText = ''
  timeout = 0
  open(method, url) { this.method = method; this.url = url }
  setRequestHeader(name, value) { this.headers[name] = value }
  send(body) { this.body = body }
  abort() { this.onabort?.() }
  progress(loaded, total) { this.upload.onprogress?.({ loaded, total, lengthComputable: true }) }
  finishTransfer() { this.upload.onload?.() }
  respond(status, body) { this.status = status; this.responseText = JSON.stringify(body); this.onload?.() }
  failNetwork() { this.onerror?.() }
  failTimeout() { this.ontimeout?.() }
}

const oldXhr = globalThis.XMLHttpRequest
const oldFormData = globalThis.FormData
globalThis.XMLHttpRequest = FakeXhr
globalThis.FormData = FakeFormData
const api = await import('./api.ts')
let request
const create = (onProgress = () => {}) => {
  const file = { name: 'warehouse.mp4', size: 5_000_000, type: 'video/mp4' }
  const task = api.uploadVideo(file, '6d591b81-6232-4e12-8da2-0bb47ca8f50e', onProgress)
  request = globalThis.lastXhr
  return task
}

// Capture the request object constructed by the module without changing XHR behavior.
const originalConstructor = globalThis.XMLHttpRequest
globalThis.XMLHttpRequest = class extends originalConstructor {
  constructor() { super(); globalThis.lastXhr = this }
}

test('reports measured transfer, server confirmation, and only then completion', async () => {
  const progress = []
  const task = create((entry) => progress.push(entry))
  assert.equal(request.timeout, api.UPLOAD_TIMEOUT_MS)
  assert.equal(request.headers['Upload-Idempotency-Key'], '6d591b81-6232-4e12-8da2-0bb47ca8f50e')
  await new Promise((resolve) => setTimeout(resolve, 550))
  request.progress(500, 1000)
  assert.equal(progress.at(-1).percent, 50)
  assert.equal(progress.at(-1).phase, 'uploading')
  assert.ok(progress.at(-1).speedBytesPerSecond > 0)
  assert.ok(progress.at(-1).remainingSeconds > 0)
  let resolved = false
  task.promise.then(() => { resolved = true })
  request.progress(1000, 1000)
  request.finishTransfer()
  await Promise.resolve()
  assert.equal(resolved, false)
  assert.equal(progress.at(-1).phase, 'server')
  request.respond(201, { id: 'video-1', original_filename: 'warehouse.mp4' })
  assert.equal((await task.promise).id, 'video-1')
  assert.equal(progress.at(-1).phase, 'completed')
})

test('server errors reject with the backend message', async () => {
  const task = create()
  request.respond(415, { detail: { message: 'Unsupported video type.' } })
  await assert.rejects(task.promise, /Unsupported video type/)
})

test('network failure and total timeout reject instead of hanging', async () => {
  let task = create()
  request.failNetwork()
  await assert.rejects(task.promise, /Network error/)
  task = create()
  request.failTimeout()
  await assert.rejects(task.promise, /60-minute maximum/)
})

test('cancellation aborts the request and rejects', async () => {
  const task = create()
  task.abort()
  await assert.rejects(task.promise, /Upload cancelled/)
})

test('upload loading cleanup callback runs after success, failure, and setup exceptions', async () => {
  for (const mode of ['success', 'failure', 'setup']) {
    let loading = true
    let settled = 0
    const onSuccess = () => { assert.equal(mode, 'success') }
    const onFailure = () => { assert.notEqual(mode, 'success') }
    const onSettled = () => { loading = false; settled += 1 }
    const task = { promise: mode === 'failure' ? Promise.reject(new Error('server')) : Promise.resolve({ id: 'v' }), abort() {} }
    await api.runVideoUpload(() => {
      if (mode === 'setup') throw new Error('construction failed')
      return task
    }, onSuccess, onFailure, onSettled)
    assert.equal(loading, false)
    assert.equal(settled, 1)
  }
})

test.after(() => {
  globalThis.XMLHttpRequest = oldXhr
  globalThis.FormData = oldFormData
  delete globalThis.lastXhr
})
