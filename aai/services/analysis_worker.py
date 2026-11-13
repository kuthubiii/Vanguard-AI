"""Single-threaded background queue for local video analysis jobs."""

import logging
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from aai.core.config import settings
from aai.db.models import AnalysisJob, Detection, Video
from aai.db.session import SessionLocal
from aai.services.analysis_processor import AnalysisProcessingError, process_video_analysis
from aai.services.analysis_results import remove_annotated_video
from aai.services.detection_engine import DetectionEngine, InferenceError, ModelUnavailableError
from aai.services.video_decoder import VideoDecodeError

logger = logging.getLogger(__name__)
_STOP = object()


class AnalysisWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        detector_factory: Callable[[], DetectionEngine] | None = None,
        queue_limit: int = 32,
    ) -> None:
        self.session_factory = session_factory
        self.detector_factory = detector_factory or (lambda: DetectionEngine(settings.yolo_model_path))
        self._queue: queue.Queue[str | object] = queue.Queue(maxsize=queue_limit)
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._accepting = False
        self._engine: DetectionEngine | None = None

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._recover_interrupted_jobs()
            self._accepting = True
            self._thread = threading.Thread(target=self._run, name="vanguard-analysis-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._state_lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._accepting = False
                return
            self._accepting = False
            self._queue.put(_STOP)
        thread.join()

    def can_accept(self) -> bool:
        with self._state_lock:
            return self._accepting and self._thread is not None and self._thread.is_alive() and not self._queue.full()

    def submit(self, job_id: str) -> None:
        with self._state_lock:
            if not self._accepting or self._thread is None or not self._thread.is_alive():
                raise RuntimeError("Analysis worker is not running")
            self._queue.put_nowait(job_id)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                self._process_job(str(item))
            finally:
                self._queue.task_done()

    def _process_job(self, job_id: str) -> None:
        try:
            with self.session_factory() as session:
                job = session.get(AnalysisJob, job_id)
                if job is None or job.status != "queued":
                    return
                video = session.get(Video, job.video_id)
                if video is None:
                    self._mark_failed(session, job, "The source video record no longer exists.")
                    return

                job.status = "running"
                job.current_stage = "Loading model"
                job.started_at = datetime.now(timezone.utc)
                session.commit()

                if self._engine is None:
                    self._engine = self.detector_factory()
                self._engine.load()
                reset_tracker = getattr(self._engine, "reset_tracker", None)
                if callable(reset_tracker):
                    reset_tracker()
                job.inference_device = self._engine.device_label
                session.commit()
                process_video_analysis(session, job, video, self._engine)
                if callable(reset_tracker):
                    reset_tracker()

                job = session.get(AnalysisJob, job_id)
                if job is not None:
                    job.status = "completed"
                    job.progress_percentage = 100
                    job.current_stage = "Completed"
                    job.error_message = None
                    job.completed_at = datetime.now(timezone.utc)
                    session.commit()
        except Exception as error:
            reset_tracker = getattr(self._engine, "reset_tracker", None) if self._engine is not None else None
            if callable(reset_tracker):
                try:
                    reset_tracker()
                except Exception as reset_error:
                    logger.warning("Could not reset tracker after job %s (%s)", job_id, type(reset_error).__name__)
            logger.error("Analysis job %s failed (%s)", job_id, type(error).__name__)
            try:
                with self.session_factory() as session:
                    job = session.get(AnalysisJob, job_id)
                    if job is not None:
                        self._mark_failed(session, job, self._public_error(error))
            except Exception as persistence_error:
                logger.error("Could not persist failure for job %s (%s)", job_id, type(persistence_error).__name__)
            remove_annotated_video(job_id)

    @staticmethod
    def _mark_failed(session: Session, job: AnalysisJob, message: str) -> None:
        session.execute(delete(Detection).where(Detection.job_id == job.job_id))
        job.status = "failed"
        job.current_stage = "Failed"
        job.progress_percentage = min(99, job.progress_percentage)
        job.detections_count = 0
        job.error_message = message
        job.completed_at = datetime.now(timezone.utc)
        job.annotated_storage_key = None
        session.commit()

    @staticmethod
    def _public_error(error: Exception) -> str:
        if isinstance(error, (VideoDecodeError, ModelUnavailableError, InferenceError, AnalysisProcessingError)):
            return str(error)
        return "Video analysis failed because of an unexpected processing error."

    def _recover_interrupted_jobs(self) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            interrupted = session.scalars(
                select(AnalysisJob).where(AnalysisJob.status.in_(("queued", "running")))
            ).all()
            for job in interrupted:
                job.status = "failed"
                job.current_stage = "Interrupted by backend restart"
                job.error_message = "Analysis was interrupted when the backend stopped. Submit a new job to retry."
                job.detections_count = 0
                job.annotated_storage_key = None
                job.completed_at = now
                session.execute(delete(Detection).where(Detection.job_id == job.job_id))
            session.commit()

            completed_keys = set(
                session.scalars(
                    select(AnalysisJob.annotated_storage_key).where(
                        AnalysisJob.status == "completed",
                        AnalysisJob.annotated_storage_key.is_not(None),
                    )
                ).all()
            )

        processed_dir = settings.processed_video_dir
        if not processed_dir.exists():
            return
        for candidate in processed_dir.iterdir():
            if candidate.name.endswith(".partial.mp4") or (candidate.suffix.lower() == ".mp4" and candidate.name not in completed_keys):
                try:
                    candidate.unlink()
                except OSError:
                    logger.warning("Could not clean abandoned analysis output (%s)", candidate.name)


analysis_worker = AnalysisWorker()
