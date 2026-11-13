import { useEffect, useRef, useState } from 'react'
import { Maximize2, Video } from 'lucide-react'

type AnnotatedVideoPlayerProps = {
  jobId: string
  src: string
}

export default function AnnotatedVideoPlayer({ jobId, src }: AnnotatedVideoPlayerProps) {
  const shellRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [diagnostic, setDiagnostic] = useState<string | null>(null)
  const [playbackRate, setPlaybackRate] = useState(1)

  useEffect(() => {
    setLoading(true)
    setError(null)
    setDiagnostic(null)
    setPlaybackRate(1)
    const video = videoRef.current
    if (!video) return
    video.playbackRate = 1

    let url: URL
    try {
      url = new URL(src, window.location.href)
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Unsupported video URL protocol')
    } catch {
      setLoading(false)
      setError('The annotated video URL is invalid.')
      return
    }

    const onReady = () => { setLoading(false); setError(null); setDiagnostic(null) }
    const onWaiting = () => setLoading(true)
    const onError = () => {
      setLoading(false)
      const mediaError = video.error
      const code = mediaError?.code
      const messages: Record<number, string> = {
        1: 'Video loading was aborted.',
        2: 'A network error interrupted the video. Check that the local API is running and the video file is reachable.',
        3: 'The browser received the video but could not decode it. The file may use an unsupported codec or be damaged.',
        4: 'The browser does not support the video source or codec. Annotated videos must be encoded as H.264/AVC.',
      }
      setError(code ? messages[code] ?? `The browser could not load the video (media error ${code}).` : 'The browser could not load the video source.')
      if (import.meta.env.DEV) {
        setDiagnostic(`URL: ${video.currentSrc || url.href} · media error: ${code ?? 'unknown'} · network state: ${video.networkState} · ready state: ${video.readyState}${mediaError?.message ? ` · ${mediaError.message}` : ''}`)
      }
    }
    video.addEventListener('loadedmetadata', onReady)
    video.addEventListener('canplay', onReady)
    video.addEventListener('waiting', onWaiting)
    video.addEventListener('error', onError)
    return () => {
      video.removeEventListener('loadedmetadata', onReady)
      video.removeEventListener('canplay', onReady)
      video.removeEventListener('waiting', onWaiting)
      video.removeEventListener('error', onError)
    }
  }, [jobId, src])

  const changePlaybackRate = (rate: number) => {
    setPlaybackRate(rate)
    if (videoRef.current) videoRef.current.playbackRate = rate
  }

  const enterFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await shellRef.current?.requestFullscreen()
    } catch {
      // Fullscreen may be unavailable in embedded or restricted browser contexts.
    }
  }

  return (
    <section className="annotated-video" aria-label="Annotated analysis video">
      <div className="annotated-video-heading">
        <div><strong>Annotated video</strong><span>Detection labels are rendered into the processed video.</span></div>
        <label className="video-speed-control">Speed
          <select aria-label="Playback speed" value={playbackRate} onChange={(event) => changePlaybackRate(Number(event.currentTarget.value))}>
            {[0.5, 0.75, 1, 1.25, 1.5, 2].map((rate) => <option key={rate} value={rate}>{rate}×</option>)}
          </select>
        </label>
        <button className="video-fullscreen-button" type="button" onClick={() => void enterFullscreen()} aria-label="Toggle video fullscreen" title="Fullscreen"><Maximize2 size={14} /></button>
      </div>
      <div className="annotated-video-shell" ref={shellRef}>
        <video ref={videoRef} controls playsInline preload="metadata" src={src} aria-label="Annotated video with object detections" />
        {loading && !error && <div className="annotated-video-overlay" role="status"><span className="upload-spinner" />Loading annotated video…</div>}
        {error && <div className="annotated-video-overlay annotated-video-error" role="alert"><Video size={17} /><span>{error}{diagnostic && <small>{diagnostic}</small>}</span></div>}
      </div>
      <p className="annotated-video-footnote">Solid labels show persisted detections. Muted INTERPOLATED boxes connect matching ByteTrack observations; they have no confidence score and are not stored as detection events.</p>
    </section>
  )
}
