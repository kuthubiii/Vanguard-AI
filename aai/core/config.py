"""Application settings for local development."""

import os
import shutil
from pathlib import Path


class Settings:
    """Small environment-backed settings object, avoiding extra dependencies."""

    @property
    def cors_origins(self) -> list[str]:
        configured = os.getenv("VANGUARD_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
        return [origin.strip() for origin in configured.split(",") if origin.strip()]

    @property
    def frontend_dist(self) -> Path:
        return Path(__file__).resolve().parents[2] / "frontend" / "dist"

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def data_dir(self) -> Path:
        return Path(os.getenv("VANGUARD_DATA_DIR", str(self.project_root / "data"))).expanduser()

    @property
    def database_path(self) -> Path:
        return Path(os.getenv("VANGUARD_DATABASE_PATH", str(self.data_dir / "vanguard.sqlite3"))).expanduser()

    @property
    def video_storage_dir(self) -> Path:
        return Path(os.getenv("VANGUARD_VIDEO_STORAGE_DIR", str(self.data_dir / "videos"))).expanduser()

    @property
    def processed_video_dir(self) -> Path:
        return Path(os.getenv("VANGUARD_PROCESSED_VIDEO_DIR", str(self.data_dir / "processed"))).expanduser()

    @property
    def yolo_model_path(self) -> Path:
        return Path(os.getenv("VANGUARD_YOLO_MODEL_PATH", str(self.data_dir / "models" / "yolo26n.pt"))).expanduser()

    @property
    def yolo_image_size(self) -> int:
        return int(os.getenv("VANGUARD_YOLO_IMAGE_SIZE", "640"))

    @property
    def ffmpeg_path(self) -> Path | None:
        configured = os.getenv("VANGUARD_FFMPEG_PATH")
        executable = configured or shutil.which("ffmpeg")
        return Path(executable).expanduser() if executable else None

    @property
    def lm_studio_base_url(self) -> str:
        return os.getenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1235/v1").rstrip("/")

    @property
    def lm_studio_model(self) -> str:
        return os.getenv("LM_STUDIO_MODEL", "qwen3-4b-instruct-2507").strip()

    @property
    def lm_studio_timeout_seconds(self) -> float:
        try:
            value = float(os.getenv("LM_STUDIO_TIMEOUT_SECONDS", "180"))
        except ValueError:
            return 180.0
        return min(900.0, max(10.0, value))

    @property
    def operations_min_baseline_jobs(self) -> int:
        return _bounded_int("OPERATIONS_MIN_BASELINE_JOBS", 3, 2, 100)

    @property
    def operations_high_activity_ratio(self) -> float:
        return _bounded_float("OPERATIONS_HIGH_ACTIVITY_RATIO", 2.0, 1.1, 20.0)

    @property
    def operations_low_activity_ratio(self) -> float:
        return _bounded_float("OPERATIONS_LOW_ACTIVITY_RATIO", 0.5, 0.01, 0.9)

    @property
    def operations_category_share_shift(self) -> float:
        return _bounded_float("OPERATIONS_CATEGORY_SHARE_SHIFT", 0.2, 0.05, 0.95)

    @property
    def operations_category_min_detections(self) -> int:
        return _bounded_int("OPERATIONS_CATEGORY_MIN_DETECTIONS", 3, 1, 10000)

    @property
    def operations_frame_concentration_min(self) -> int:
        return _bounded_int("OPERATIONS_FRAME_CONCENTRATION_MIN", 8, 2, 10000)

    @property
    def operations_repeated_track_min_observations(self) -> int:
        return _bounded_int("OPERATIONS_REPEATED_TRACK_MIN_OBSERVATIONS", 30, 5, 100000)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


settings = Settings()
