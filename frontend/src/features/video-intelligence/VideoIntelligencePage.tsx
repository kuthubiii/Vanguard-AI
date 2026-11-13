import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import {
  AlertCircle, ArrowDownToLine, Bot, Check, ChevronRight, CircleHelp,
  Clock3, FileVideo2, Film, Gauge, Info, ScanEye, Settings2,
  ShieldCheck, SlidersHorizontal, Sparkles, Trash2, Upload, Video, X,
} from 'lucide-react'
import { formatDuration, formatFileSize, validateVideoFile } from './videoFile'
import AnnotatedVideoPlayer from './AnnotatedVideoPlayer'
import {
  deleteVideo, getAnalysisJob, getAnalysisJobs, getAnalysisResults, getAnalysisTracks, getVideos,
  getAnnotatedVideoUrl, runVideoUpload, startAnalysis, uploadVideo,
  type AnalysisJob, type AnalysisResults, type TrackSummaryList, type UploadProgress, type VideoRecord, type VideoUploadTask,
} from '../../lib/api'
import './VideoIntelligencePage.css'

type DurationState = number | 'loading' | null

export default function VideoIntelligencePage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const uploadTaskRef = useRef<VideoUploadTask | null>(null)
  const uploadKeyRef = useRef<string | null>(null)
  const uploadKeysRef = useRef(new Map<string, string>())
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewFile, setPreviewFile] = useState<File | null>(null)
  const [duration, setDuration] = useState<DurationState>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [confidence, setConfidence] = useState(45)
  const [samplingRate, setSamplingRate] = useState(5)
  const [trackingEnabled, setTrackingEnabled] = useState(true)
  const [reasoningEnabled, setReasoningEnabled] = useState(false)
  const [uploadedVideos, setUploadedVideos] = useState<VideoRecord[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadingFilename, setUploadingFilename] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null)
  const [deletingVideoId, setDeletingVideoId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [analysisJobs, setAnalysisJobs] = useState<AnalysisJob[]>([])
  const [activeJob, setActiveJob] = useState<AnalysisJob | null>(null)
  const [analysisResults, setAnalysisResults] = useState<AnalysisResults | null>(null)
  const [trackSummary, setTrackSummary] = useState<TrackSummaryList | null>(null)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [resultsError, setResultsError] = useState<string | null>(null)
  const [resultsLoading, setResultsLoading] = useState(false)
  const [startingVideoId, setStartingVideoId] = useState<string | null>(null)

  const refreshUploadedVideos = async (signal?: AbortSignal) => {
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      const response = await getVideos(signal)
      setUploadedVideos(response.items)
      setHistoryTotal(response.total)
    } catch (error) {
      if (!signal?.aborted) setHistoryError(error instanceof Error ? error.message : 'Could not load uploaded videos.')
    } finally {
      if (!signal?.aborted) setHistoryLoading(false)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void refreshUploadedVideos(controller.signal)
    void getAnalysisJobs(controller.signal).then((response) => {
      setAnalysisJobs(response.items)
      setActiveJob((current) => current ?? response.items.find((job) => job.status === 'queued' || job.status === 'running') ?? response.items[0] ?? null)
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setAnalysisError(error instanceof Error ? error.message : 'Could not load analysis jobs.')
    })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!activeJob || (activeJob.status !== 'queued' && activeJob.status !== 'running')) return
    const controller = new AbortController()
    let polling = false
    const pollJob = async () => {
      if (polling || controller.signal.aborted) return
      polling = true
      try {
        const updated = await getAnalysisJob(activeJob.job_id, controller.signal)
        if (!controller.signal.aborted) {
          setActiveJob(updated)
          setAnalysisJobs((current) => [updated, ...current.filter((job) => job.job_id !== updated.job_id)])
          setAnalysisError(null)
        }
      } catch (error) {
        if (!controller.signal.aborted) setAnalysisError(error instanceof Error ? error.message : 'Could not refresh analysis status.')
      } finally {
        polling = false
      }
    }
    void pollJob()
    const timer = window.setInterval(() => void pollJob(), 1200)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [activeJob?.job_id, activeJob?.status])

  useEffect(() => {
    if (!activeJob || activeJob.status !== 'completed') {
      setAnalysisResults(null)
      setTrackSummary(null)
      setResultsError(null)
      setResultsLoading(false)
      return
    }
    const controller = new AbortController()
    setAnalysisResults(null)
    setTrackSummary(null)
    setResultsError(null)
    setResultsLoading(true)
    void getAnalysisResults(activeJob.job_id, controller.signal).then(setAnalysisResults).catch((error: unknown) => {
      if (!controller.signal.aborted) setResultsError(error instanceof Error ? error.message : 'Could not load detections.')
    }).finally(() => {
      if (!controller.signal.aborted) setResultsLoading(false)
    })
    if (activeJob.tracking_enabled) {
      void getAnalysisTracks(activeJob.job_id, controller.signal).then(setTrackSummary).catch((error: unknown) => {
        if (!controller.signal.aborted) setResultsError(error instanceof Error ? error.message : 'Could not load tracked objects.')
      })
    }
    return () => controller.abort()
  }, [activeJob?.job_id, activeJob?.status])

  useEffect(() => {
    if (!selectedFile) {
      setPreviewUrl(null)
      setDuration(null)
      setPreviewError(null)
      return
    }

    const objectUrl = URL.createObjectURL(selectedFile)
    setPreviewFile(selectedFile)
    setPreviewUrl(objectUrl)
    setDuration('loading')
    setPreviewError(null)
    return () => URL.revokeObjectURL(objectUrl)
  }, [selectedFile])

  const chooseFile = (file?: File) => {
    if (!file || uploadTaskRef.current) return
    setUploadError(null)
    setUploadSuccess(null)
    const validation = validateVideoFile(file)
    if (!validation.valid) {
      setValidationError(validation.message)
      return
    }
    setValidationError(null)
    const identity = `${file.name}\u0000${file.size}\u0000${file.lastModified}`
    const idempotencyKey = uploadKeysRef.current.get(identity) ?? crypto.randomUUID()
    uploadKeysRef.current.set(identity, idempotencyKey)
    uploadKeyRef.current = idempotencyKey
    setUploadProgress(null)
    setSelectedFile(file)
  }

  const onFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    chooseFile(event.currentTarget.files?.[0])
    event.currentTarget.value = ''
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragging(false)
    chooseFile(event.dataTransfer.files[0])
  }

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragging(true)
  }

  const clearVideo = () => {
    if (uploadTaskRef.current) return
    setSelectedFile(null)
    setValidationError(null)
    setUploadError(null)
    setUploadSuccess(null)
    uploadKeyRef.current = null
    setUploadProgress(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const loadMoreUploadedVideos = async () => {
    if (historyLoading || uploadedVideos.length >= historyTotal) return
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      const response = await getVideos(undefined, 100, uploadedVideos.length)
      setUploadedVideos((current) => {
        const existingIds = new Set(current.map((record) => record.id))
        return [...current, ...response.items.filter((record) => !existingIds.has(record.id))]
      })
      setHistoryTotal(response.total)
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : 'Could not load more uploaded videos.')
    } finally {
      setHistoryLoading(false)
    }
  }

  const startUpload = async () => {
    if (!selectedFile || uploadTaskRef.current) return
    setUploadError(null)
    setUploadSuccess(null)
    setUploadProgress(null)
    setUploadingFilename(selectedFile.name)
    setUploading(true)
    const idempotencyKey = uploadKeyRef.current ?? crypto.randomUUID()
    uploadKeyRef.current = idempotencyKey
    await runVideoUpload(
      () => {
        const task = uploadVideo(selectedFile, idempotencyKey, setUploadProgress)
        uploadTaskRef.current = task
        return task
      },
      (record) => {
      setUploadSuccess(`Uploaded ${record.original_filename} to your local video library.`)
      setUploadError(null)
      setUploadedVideos((current) => [record, ...current.filter((video) => video.id !== record.id)])
      void refreshUploadedVideos()
      },
      (error) => {
      const message = error instanceof Error ? error.message : 'The video upload failed.'
      setUploadError(message)
      setUploadProgress((current) => current ? { ...current, phase: message.startsWith('Upload cancelled') ? 'cancelled' : 'failed' } : {
        loaded: 0, total: selectedFile.size, percent: 0, speedBytesPerSecond: null, remainingSeconds: null,
        phase: message.startsWith('Upload cancelled') ? 'cancelled' : 'failed',
      })
      if (message.startsWith('Upload cancelled')) void refreshUploadedVideos()
      },
      (task) => {
      if (task && uploadTaskRef.current === task) uploadTaskRef.current = null
      setUploading(false)
      setUploadingFilename(null)
      },
    )
  }

  const cancelUpload = () => uploadTaskRef.current?.abort()

  const removeUploadedVideo = async (record: VideoRecord) => {
    if (deletingVideoId) return
    setDeletingVideoId(record.id)
    setHistoryError(null)
    try {
      await deleteVideo(record.id)
      setUploadedVideos((current) => current.filter((video) => video.id !== record.id))
      setHistoryTotal((current) => Math.max(0, current - 1))
      setConfirmDeleteId(null)
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : 'Could not delete the uploaded video.')
    } finally {
      setDeletingVideoId(null)
    }
  }

  const startVideoAnalysis = async (record: VideoRecord) => {
    if (startingVideoId || analysisJobs.some((job) => job.video_id === record.id && (job.status === 'queued' || job.status === 'running'))) return
    setStartingVideoId(record.id)
    setAnalysisError(null)
    setResultsError(null)
    setAnalysisResults(null)
    try {
      const job = await startAnalysis(record.id, {
        confidence_threshold: confidence / 100,
        sampling_rate_fps: samplingRate,
        image_size: 640,
        tracking_enabled: trackingEnabled,
      })
      setAnalysisJobs((current) => [job, ...current.filter((item) => item.job_id !== job.job_id)])
      setActiveJob(job)
    } catch (error) {
      setAnalysisError(error instanceof Error ? error.message : 'Could not start video analysis.')
    } finally {
      setStartingVideoId(null)
    }
  }

  const selectAnalysisJob = (job: AnalysisJob) => {
    setActiveJob(job)
    setAnalysisError(null)
  }

  return (
    <div className="video-page">
      <div className="page-heading-row video-page-heading">
        <div>
          <div className="overline"><span className="overline-line" />VIDEO OPERATIONS</div>
          <h1>Video Intelligence</h1>
          <p className="page-subtitle">Review operational footage and prepare an analysis workflow.</p>
        </div>
        <div className="video-local-badge"><ShieldCheck size={14} /> LOCAL WORKSPACE</div>
      </div>

      <div className="video-layout">
        <div className="video-main-column">
          <section className="panel video-source-panel" aria-labelledby="source-heading">
            <div className="video-panel-heading">
              <div className="video-heading-icon"><Film size={17} /></div>
              <div><h2 id="source-heading">Video source</h2><p>Select a local video to preview it in your browser.</p></div>
              {selectedFile && <span className="file-ready-chip"><Check size={12} /> FILE READY</span>}
            </div>

            {!selectedFile ? (
              <div
                className={`video-dropzone ${isDragging ? 'dragging' : ''}`}
                onDrop={onDrop}
                onDragOver={onDragOver}
                onDragLeave={(event) => {
                  event.preventDefault()
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setIsDragging(false)
                }}
              >
                <div className="upload-icon-frame"><Upload size={19} /></div>
                <strong>Drop a video file here</strong>
                <span className="dropzone-subtitle">or browse files from this device</span>
                <button className="secondary-button browse-button" onClick={() => inputRef.current?.click()} type="button">Browse files <ChevronRight size={14} /></button>
                <span className="format-note">MP4 · WebM · MOV <i /> Up to 2 GB</span>
                <input ref={inputRef} className="visually-hidden-input" type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov" onChange={onFileInput} aria-label="Select a video file" />
              </div>
            ) : (
              <div className="selected-video-area">
                <div className="selected-video-bar">
                  <div className="selected-file-icon"><FileVideo2 size={17} /></div>
                  <div className="selected-file-copy"><strong title={selectedFile.name}>{selectedFile.name}</strong><span>{formatFileSize(selectedFile.size)} <i /> {duration === 'loading' ? 'Reading duration…' : duration === null ? 'Duration unavailable' : formatDuration(duration)}</span></div>
                  <button className="file-action-button" type="button" onClick={() => inputRef.current?.click()} aria-label="Replace selected video" title="Replace video" disabled={uploading}><Upload size={15} /></button>
                  <button className="file-action-button remove-file-button" type="button" onClick={clearVideo} aria-label="Remove selected video" title="Remove video" disabled={uploading}><X size={16} /></button>
                  <input ref={inputRef} className="visually-hidden-input" type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov" onChange={onFileInput} aria-label="Replace selected video" disabled={uploading} />
                </div>
                <div className="video-player-shell">
                  {previewUrl && previewFile === selectedFile && <video
                    key={previewUrl}
                    src={previewUrl}
                    controls
                    playsInline
                    preload="metadata"
                    aria-label={`Video preview: ${selectedFile.name}`}
                    onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
                    onError={() => {
                      setDuration(null)
                      setPreviewError('This browser could not decode the selected video. Try another supported video or browser.')
                    }}
                  />}
                  {previewError && <div className="video-error-overlay"><AlertCircle size={17} /><span>{previewError}</span></div>}
                </div>
                <div className="preview-caption"><span><Video size={13} /> Browser preview</span><span>Preview is local until uploaded</span></div>
                <div className="upload-actions">
                  <button type="button" className="upload-video-button" onClick={() => void startUpload()} disabled={uploading || !!uploadSuccess}>
                    {uploading ? <><span className="upload-spinner" /> {uploadProgress?.phase === 'server' ? 'Server validating and saving…' : 'Uploading video…'}</> : uploadSuccess ? <><Check size={14} /> Uploaded to local library</> : uploadError ? <><Upload size={14} /> Retry upload</> : <><Upload size={14} /> Upload to local library</>}
                  </button>
                  {uploading && <button type="button" className="cancel-upload-button" onClick={cancelUpload}>Cancel</button>}
                  {uploading && uploadingFilename && <span className="upload-target-name" title={uploadingFilename}>{uploadingFilename}</span>}
                </div>
                {(uploading || uploadProgress) && <div className="upload-progress-area" role="status" aria-live="polite">
                  {uploadProgress && <>
                    <div className="upload-progress-label"><span>{uploadProgress.phase === 'preparing' ? 'Preparing upload' : uploadProgress.phase === 'uploading' ? 'Uploading request bytes' : uploadProgress.phase === 'server' ? 'All request bytes sent · server validating and saving' : uploadProgress.phase === 'completed' ? 'Upload completed' : uploadProgress.phase === 'cancelled' ? 'Upload cancelled' : 'Upload failed'}</span><strong>{uploadProgress.phase === 'completed' ? '100%' : `${uploadProgress.percent}% transferred`}</strong></div>
                    <progress value={uploadProgress.percent} max="100" aria-label="Transferred request bytes" />
                    <div className="upload-transfer-details"><span>{formatFileSize(uploadProgress.loaded)} / {formatFileSize(uploadProgress.total)} request bytes</span><span>{uploadProgress.phase === 'uploading' ? formatUploadRate(uploadProgress.speedBytesPerSecond) : uploadProgress.phase === 'server' ? 'Waiting for server confirmation' : ''}</span><span>{uploadProgress.phase === 'uploading' && uploadProgress.remainingSeconds !== null ? formatUploadEta(uploadProgress.remainingSeconds) : ''}</span></div>
                  </>}
                </div>}
                {uploadError && <div className="upload-result-message error" role="alert"><AlertCircle size={14} /><span>{uploadError}</span></div>}
                {uploadSuccess && <div className="upload-result-message success" role="status"><Check size={14} /><span>{uploadSuccess}</span></div>}
              </div>
            )}

            {validationError && <div className="validation-message" role="alert"><AlertCircle size={15} /><span>{validationError}</span><button type="button" onClick={() => setValidationError(null)} aria-label="Dismiss validation message"><X size={14} /></button></div>}
          </section>

          <section className="panel analysis-status-panel" aria-labelledby="status-heading">
            <div className="video-panel-heading">
              <div className="video-heading-icon status-heading-icon"><Clock3 size={17} /></div>
              <div><h2 id="status-heading">Analysis status</h2><p>Live processing state from the local YOLO analysis worker.</p></div>
              <span className="status-not-configured">{activeJob?.status.toUpperCase() ?? 'READY'}</span>
            </div>
            <div className="analysis-status-content">
              <div className="status-step-icon"><Clock3 size={17} /></div>
              <div className="status-step-copy"><strong>{activeJob ? `${activeJob.status[0].toUpperCase()}${activeJob.status.slice(1)} · ${activeJob.current_stage}` : 'Select an uploaded video to analyze'}</strong><span>{activeJob ? `${activeJob.frames_processed} / ${activeJob.total_frames ?? '…'} frames processed · ${activeJob.frames_sampled} sampled${activeJob.error_message ? ` · ${activeJob.error_message}` : ''}` : 'Real frame sampling and object detection run on the backend.'}</span></div>
              <span className="not-started-pill">{activeJob?.status.toUpperCase() ?? 'IDLE'}</span>
            </div>
            <div className="progress-unavailable"><span>PROGRESS</span><progress value={activeJob?.progress_percentage ?? 0} max="100" aria-label="Analysis progress" /><span className="progress-dash">{activeJob ? `${activeJob.progress_percentage}%` : '—'}</span></div>
            {analysisError && <div className="history-error" role="alert"><AlertCircle size={14} /><span>{analysisError}</span></div>}
          </section>

          <section className="panel results-panel" aria-labelledby="results-heading">
            <div className="video-panel-heading">
              <div className="video-heading-icon results-heading-icon"><ScanEye size={17} /></div>
              <div><h2 id="results-heading">Analysis results</h2><p>Evidence and detections from the selected video.</p></div>
              <span className="result-count">{analysisResults?.total ?? (resultsLoading ? '…' : '—')} <span>DETECTIONS</span></span>
            </div>
            {activeJob?.status === 'completed' ? <AnnotatedVideoPlayer key={activeJob.job_id} jobId={activeJob.job_id} src={getAnnotatedVideoUrl(activeJob.job_id)} /> : <div className="annotated-video-placeholder" role="status"><Video size={18} /><div><strong>{activeJob?.status === 'queued' || activeJob?.status === 'running' ? 'Video analysis is processing' : activeJob?.status === 'failed' ? 'Annotated video unavailable' : 'No completed analysis selected'}</strong><span>{activeJob?.status === 'queued' || activeJob?.status === 'running' ? 'The annotated player will be available after the backend finishes processing.' : activeJob?.status === 'failed' ? activeJob.error_message ?? 'This analysis did not complete successfully.' : 'Select a completed analysis job to view its annotated video.'}</span></div></div>}
            {activeJob?.status === 'completed' && <div className="track-summary-strip"><strong>{activeJob.tracking_enabled ? trackSummary?.total_unique_tracked_objects ?? '…' : 'Off'}</strong><span>UNIQUE TRACKED OBJECTS</span><div>{trackSummary && Object.entries(trackSummary.class_counts).map(([name, count]) => <span key={name}>{name} <b>{count}</b></span>)}</div></div>}
            {resultsError && <div className="history-error" role="alert"><AlertCircle size={14} /><span>{resultsError}</span></div>}
            {resultsLoading ? <div className="uploaded-loading"><span className="upload-spinner" /> Loading real detections…</div> : analysisResults?.detections.length ? <div className="detection-list">{analysisResults.detections.slice(0, 50).map((detection) => <div className="detection-row" key={detection.detection_id}><strong>{detection.class_name}{detection.track_id !== null ? ` · ID ${detection.track_id}` : ''}</strong><span>Frame {detection.frame_index} · {detection.timestamp_seconds.toFixed(2)}s</span><b>{(detection.confidence * 100).toFixed(1)}%</b></div>)}{analysisResults.total > 50 && <p className="control-help">Showing first 50 of {analysisResults.total} detections.</p>}</div> : <div className="results-empty"><div className="results-empty-icon"><ScanEye size={19} /></div><strong>{activeJob?.status === 'completed' ? 'No objects detected' : 'No analysis results'}</strong><p>{activeJob?.status === 'completed' ? 'The real detector returned no objects above the configured confidence threshold.' : 'Results appear after a real video analysis completes.'}</p></div>}
            {trackSummary?.tracks.length ? <div className="track-list"><div className="track-list-heading">TRACKED OBJECTS <span>OBSERVED SPAN</span></div>{trackSummary.tracks.map((track) => <div className="track-row" key={track.track_id}><strong>{track.class_name} <small>ID {track.track_id}</small></strong><span>{track.first_observed_seconds.toFixed(1)}s – {track.last_observed_seconds.toFixed(1)}s · {track.observation_count} observations</span><b>{track.observed_span_seconds === null ? 'single observation' : `${track.observed_span_seconds.toFixed(1)}s`}</b></div>)}<p className="control-help">Observed span covers sampled detections only; it does not imply continuous physical presence.</p></div> : null}
          </section>

          <section className="panel uploaded-history-panel" aria-labelledby="uploaded-heading">
            <div className="video-panel-heading uploaded-heading-row">
              <div className="video-heading-icon uploaded-heading-icon"><FileVideo2 size={17} /></div>
              <div><h2 id="uploaded-heading">Uploaded videos</h2><p>Files stored in your local workspace.</p></div>
              <div className="uploaded-history-actions"><span>{historyLoading ? '…' : historyTotal} <small>FILES</small></span><button type="button" onClick={() => void refreshUploadedVideos()} aria-label="Refresh uploaded videos" title="Refresh list" disabled={historyLoading}><span className={historyLoading ? 'refresh-symbol spinning' : 'refresh-symbol'}>↻</span></button></div>
            </div>
            {historyError && <div className="history-error" role="alert"><AlertCircle size={14} /><span>{historyError}</span><button type="button" onClick={() => void refreshUploadedVideos()}>Retry</button></div>}
            {historyLoading && uploadedVideos.length === 0 ? <div className="uploaded-loading"><span className="upload-spinner" /> Loading uploaded videos…</div> : historyError && uploadedVideos.length === 0 ? null : uploadedVideos.length === 0 ? <div className="history-empty uploaded-empty"><span>No uploaded videos</span><span>Videos confirmed by the backend will be listed here.</span></div> : <div className="uploaded-video-list">
              {uploadedVideos.map((record) => <div className="uploaded-video-item" key={record.id}>
                <div className="uploaded-record-icon"><FileVideo2 size={15} /></div>
                <div className="uploaded-record-copy"><strong title={record.original_filename}>{record.original_filename}</strong><span>{formatFileSize(record.file_size)} <i /> {new Date(record.created_at).toLocaleString()}</span></div>
                <span className="uploaded-record-status">{record.upload_status}</span>
                <button className="analyze-video-button" type="button" onClick={() => void startVideoAnalysis(record)} disabled={!!startingVideoId || analysisJobs.some((job) => job.video_id === record.id && (job.status === 'queued' || job.status === 'running'))}>{startingVideoId === record.id ? 'Starting…' : analysisJobs.some((job) => job.video_id === record.id && (job.status === 'queued' || job.status === 'running')) ? 'Running' : 'Analyze'}</button>
                <button className="file-action-button uploaded-delete-button" type="button" aria-label={`Delete ${record.original_filename}`} title="Delete uploaded video" onClick={() => setConfirmDeleteId(confirmDeleteId === record.id ? null : record.id)} disabled={deletingVideoId === record.id}><Trash2 size={14} /></button>
                {confirmDeleteId === record.id && <div className="delete-confirm-row"><span>Delete this stored video?</span><button type="button" onClick={() => void removeUploadedVideo(record)} disabled={deletingVideoId === record.id}>{deletingVideoId === record.id ? 'Deleting…' : 'Delete'}</button><button type="button" onClick={() => setConfirmDeleteId(null)} disabled={deletingVideoId === record.id}>Keep</button></div>}
              </div>)}
              {uploadedVideos.length < historyTotal && <button className="load-more-videos" type="button" onClick={() => void loadMoreUploadedVideos()} disabled={historyLoading}>{historyLoading ? 'Loading…' : `Load more · ${historyTotal - uploadedVideos.length} remaining`}</button>}
            </div>}
          </section>

          <section className="panel history-panel" aria-labelledby="history-heading">
            <div className="video-panel-heading history-heading-row">
              <div className="video-heading-icon history-heading-icon"><Clock3 size={17} /></div>
              <div><h2 id="history-heading">Recent video analyses</h2><p>Previously processed videos in this workspace.</p></div>
              <span className="history-unavailable"><CircleHelp size={13} /> {analysisJobs.length} JOBS</span>
            </div>
            {analysisJobs.length === 0 ? <div className="history-empty"><span>No analysis history yet</span><span>Completed and failed jobs appear here after submission.</span></div> : <div className="analysis-job-list">{analysisJobs.map((job) => <button type="button" className={`analysis-job-row ${activeJob?.job_id === job.job_id ? 'selected' : ''}`} key={job.job_id} onClick={() => selectAnalysisJob(job)}><strong>{uploadedVideos.find((video) => video.id === job.video_id)?.original_filename ?? job.video_id.slice(0, 12)}</strong><span>{job.status} · {job.detections_count} detections · {new Date(job.created_at).toLocaleString()}</span></button>)}</div>}
          </section>
        </div>

        <aside className="video-side-column">
          <section className="panel config-panel" aria-labelledby="config-heading">
            <div className="config-panel-heading"><div><div className="overline"><span className="overline-line" />SETUP</div><h2 id="config-heading">Analysis configuration</h2></div><SlidersHorizontal size={16} /></div>
            <p className="config-intro">Configure the real YOLO video analysis run.</p>

            <div className="config-control confidence-control">
              <div className="config-label-row"><label htmlFor="confidence-slider">Detection confidence</label><span>{confidence}%</span></div>
              <input id="confidence-slider" type="range" min="10" max="95" step="5" value={confidence} style={{ '--range-progress': `${((confidence - 10) / 85) * 100}%` } as React.CSSProperties} onChange={(event) => setConfidence(Number(event.currentTarget.value))} />
              <div className="range-scale"><span>10% · More detections</span><span>95% · Higher certainty</span></div>
              <p className="control-help">Minimum confidence threshold for detected objects.</p>
            </div>

            <div className="config-control sampling-control">
              <div className="config-label-row"><label htmlFor="sampling-slider">Frame sampling rate</label><span>{samplingRate} <small>FPS</small></span></div>
              <input id="sampling-slider" type="range" min="1" max="10" step="1" value={samplingRate} style={{ '--range-progress': `${((samplingRate - 1) / 9) * 100}%` } as React.CSSProperties} onChange={(event) => setSamplingRate(Number(event.currentTarget.value))} />
              <div className="range-scale"><span>1 frame/s</span><span>10 frames/s</span></div>
              <p className="control-help">Default 5 FPS; higher rates improve temporal coverage but use more compute.</p>
            </div>

            <div className="config-divider" />
            <div className="config-feature-heading"><Settings2 size={14} /><span>PROCESSING OPTIONS</span></div>
            <ConfigToggle icon={<ScanEye size={15} />} title="Object tracking" description="Ultralytics ByteTrack on sampled frames" enabled={trackingEnabled} onChange={setTrackingEnabled} tag="BYTE TRACK" />
            <ConfigToggle icon={<Sparkles size={15} />} title="AI reasoning" description="Summarize events and findings" enabled={reasoningEnabled} onChange={setReasoningEnabled} />

            <div className="config-notice"><Info size={14} /><span>ByteTrack updates at the selected sample rate. When tracking is on, muted INTERPOLATED boxes connect only matching observed IDs; they are not persisted detections or confidence scores. Tracking off shows sampled detections only. Track spans remain observed samples, not continuous presence.</span></div>
            <button className="analysis-unavailable-button" type="button" disabled={!uploadedVideos.length || !!startingVideoId || (!!activeJob && (activeJob.status === 'queued' || activeJob.status === 'running'))} onClick={() => { const record = uploadedVideos[0]; if (record) void startVideoAnalysis(record) }}><Bot size={16} /> {startingVideoId ? 'Starting analysis…' : activeJob?.status === 'queued' || activeJob?.status === 'running' ? 'Analysis running' : 'Analyze latest uploaded video'}</button>
            <p className="analysis-unavailable-note">Select Analyze beside any uploaded video to run real inference.</p>
          </section>

          <section className="engine-card"><div className="engine-card-top"><div className="engine-icon"><Gauge size={16} /></div><span>ENGINE STATUS</span><span className="engine-led" /></div><strong>YOLO + ByteTrack ready</strong><p>Detection and sampled-frame tracking run locally. Language reasoning is not configured.</p><div className="engine-card-footer"><span><span /> {activeJob?.status === 'running' ? 'PROCESSING' : 'READY'}</span><ArrowDownToLine size={14} /></div></section>

          <div className="privacy-note"><ShieldCheck size={15} /><span><strong>Local workspace</strong>Preview in your browser; upload only when you choose to add the file to the library.</span></div>
        </aside>
      </div>
      <footer className="page-footer"><span>VANGUARD AI <i>·</i> LOCAL INTELLIGENCE WORKSPACE</span><span>SEE. THINK. PREDICT. ACT.</span></footer>
    </div>
  )
}

function ConfigToggle({ icon, title, description, enabled, onChange, tag = 'NOT CONFIGURED' }: {
  icon: React.ReactNode
  title: string
  description: string
  enabled: boolean
  onChange: (enabled: boolean) => void
  tag?: string
}) {
  return <div className="config-toggle-row">
    <div className="config-toggle-icon">{icon}</div>
    <div className="config-toggle-copy"><strong>{title}</strong><span>{description}</span><em>{tag}</em></div>
    <button className={`switch-control ${enabled ? 'enabled' : ''}`} role="switch" aria-checked={enabled} aria-label={`${title} preference`} type="button" onClick={() => onChange(!enabled)}><span /></button>
  </div>
}

function formatUploadRate(bytesPerSecond: number | null): string {
  return bytesPerSecond === null ? '—' : `${(bytesPerSecond / (1024 * 1024)).toFixed(2)} MB/s`
}

function formatUploadEta(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return 'Estimating…'
  const rounded = Math.ceil(seconds)
  return rounded < 60 ? `about ${rounded}s left` : `about ${Math.floor(rounded / 60)}m ${rounded % 60}s left`
}
