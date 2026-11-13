import assert from 'node:assert/strict'
import test from 'node:test'
import { formatDuration, formatFileSize, MAX_VIDEO_SIZE_BYTES, validateVideoFile } from './videoFile.ts'

const video = (overrides = {}) => ({ name: 'warehouse.mp4', size: 1024, type: 'video/mp4', ...overrides })

test('accepts supported video extensions and browser MIME types', () => {
  assert.deepEqual(validateVideoFile(video()), { valid: true })
  assert.deepEqual(validateVideoFile(video({ name: 'shift.webm', type: 'video/webm' })), { valid: true })
  assert.deepEqual(validateVideoFile(video({ name: 'clip.MOV', type: 'video/quicktime' })), { valid: true })
})

test('accepts a supported extension when the browser does not provide a MIME type', () => {
  assert.deepEqual(validateVideoFile(video({ type: '' })), { valid: true })
})

test('rejects empty, oversized, unsupported, and mismatched video files', () => {
  assert.equal(validateVideoFile(video({ size: 0 })).valid, false)
  assert.equal(validateVideoFile(video({ size: MAX_VIDEO_SIZE_BYTES + 1 })).valid, false)
  assert.equal(validateVideoFile(video({ name: 'notes.txt', type: 'text/plain' })).valid, false)
  assert.equal(validateVideoFile(video({ name: 'clip.webm', type: 'video/mp4' })).valid, false)
})

test('formats file sizes and media durations', () => {
  assert.equal(formatFileSize(512), '512 B')
  assert.equal(formatFileSize(1_572_864), '1.5 MB')
  assert.equal(formatDuration(65), '1:05')
  assert.equal(formatDuration(3_661), '1:01:01')
  assert.equal(formatDuration(Number.NaN), 'Duration unavailable')
})
