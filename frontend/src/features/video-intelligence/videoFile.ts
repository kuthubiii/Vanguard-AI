export const MAX_VIDEO_SIZE_BYTES = 2 * 1024 * 1024 * 1024

const supportedTypesByExtension: Record<string, readonly string[]> = {
  mp4: ['video/mp4'],
  webm: ['video/webm'],
  mov: ['video/quicktime', 'video/mp4'],
}

export type VideoFileErrorCode = 'empty' | 'too-large' | 'unsupported-type'

export type VideoFileValidation =
  | { valid: true }
  | { valid: false; code: VideoFileErrorCode; message: string }

export function validateVideoFile(file: Pick<File, 'name' | 'size' | 'type'>): VideoFileValidation {
  if (file.size <= 0) {
    return { valid: false, code: 'empty', message: 'This file is empty. Choose a video file with content.' }
  }
  if (file.size > MAX_VIDEO_SIZE_BYTES) {
    return { valid: false, code: 'too-large', message: 'This video exceeds the 2 GB limit. Choose a smaller file.' }
  }

  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  const acceptedMimeTypes = supportedTypesByExtension[extension]
  if (!acceptedMimeTypes || (file.type && !acceptedMimeTypes.includes(file.type.toLowerCase()))) {
    return { valid: false, code: 'unsupported-type', message: 'Unsupported video format. Choose an MP4, WebM, or MOV file.' }
  }

  return { valid: true }
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let size = bytes / 1024
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${units[unitIndex]}`
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return 'Duration unavailable'
  const wholeSeconds = Math.floor(seconds)
  const hours = Math.floor(wholeSeconds / 3600)
  const minutes = Math.floor((wholeSeconds % 3600) / 60)
  const remainder = wholeSeconds % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
    : `${minutes}:${String(remainder).padStart(2, '0')}`
}
