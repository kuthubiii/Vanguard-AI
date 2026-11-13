"""Migration coverage for the Phase 3–5 local SQLite schemas."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from aai.db.base import Base
from aai.db import models  # noqa: F401
from aai.db.migrations import apply_sqlite_migrations


VIDEO_DDL = """CREATE TABLE videos (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    original_filename VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(48) NOT NULL UNIQUE,
    storage_key VARCHAR(48) NOT NULL UNIQUE,
    file_size INTEGER NOT NULL,
    content_type VARCHAR(64) NOT NULL,
    upload_status VARCHAR(16) NOT NULL,
    created_at DATETIME NOT NULL
)"""

PHASE4_JOB_DDL = """CREATE TABLE analysis_jobs (
    job_id VARCHAR(36) NOT NULL PRIMARY KEY,
    video_id VARCHAR(36) NOT NULL,
    status VARCHAR(16) NOT NULL,
    progress_percentage INTEGER NOT NULL,
    current_stage VARCHAR(64) NOT NULL,
    frames_processed INTEGER NOT NULL,
    frames_sampled INTEGER NOT NULL,
    total_frames INTEGER,
    detections_count INTEGER NOT NULL,
    error_message TEXT,
    confidence_threshold FLOAT NOT NULL,
    sampling_rate_fps FLOAT NOT NULL,
    image_size INTEGER NOT NULL,
    annotated_storage_key VARCHAR(64),
    created_at DATETIME NOT NULL,
    started_at DATETIME,
    completed_at DATETIME,
    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
)"""

PHASE4_DETECTION_DDL = """CREATE TABLE detections (
    detection_id VARCHAR(36) NOT NULL PRIMARY KEY,
    job_id VARCHAR(36) NOT NULL,
    frame_index INTEGER NOT NULL,
    timestamp_seconds FLOAT NOT NULL,
    class_id INTEGER NOT NULL,
    class_name VARCHAR(128) NOT NULL,
    confidence FLOAT NOT NULL,
    bounding_box JSON NOT NULL,
    FOREIGN KEY(job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE
)"""


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vanguard-migration-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _engine(self, name="migration.sqlite3"):
        return create_engine(f"sqlite:///{(self.root / name).as_posix()}")

    @staticmethod
    def _create_phase3(engine):
        with engine.begin() as connection:
            connection.exec_driver_sql(VIDEO_DDL)
            connection.execute(text(
                "INSERT INTO videos VALUES (:id,:name,:stored,:key,:size,:type,:status,:created)"
            ), {"id":"video-1","name":"phase3.mp4","stored":"stored1.mp4","key":"stored1.mp4","size":777,
                "type":"video/mp4","status":"uploaded","created":"2026-01-01 00:00:00"})

    @staticmethod
    def _create_phase4(engine):
        DatabaseMigrationTests._create_phase3(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(PHASE4_JOB_DDL)
            connection.exec_driver_sql(PHASE4_DETECTION_DDL)
            connection.execute(text("""INSERT INTO analysis_jobs VALUES
                ('job-1','video-1','completed',100,'Completed',4,2,4,1,NULL,0.25,2.0,640,'job-1.mp4',
                 '2026-01-01 00:00:01','2026-01-01 00:00:02','2026-01-01 00:00:03')"""))
            connection.execute(text("""INSERT INTO detections VALUES
                ('det-1','job-1',2,0.5,0,'person',0.9,'[1,2,3,4]')"""))

    @staticmethod
    def _create_phase5(engine):
        DatabaseMigrationTests._create_phase4(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE analysis_jobs ADD COLUMN tracking_enabled BOOLEAN NOT NULL DEFAULT 1")
            connection.exec_driver_sql("ALTER TABLE detections ADD COLUMN track_id INTEGER NULL")
            connection.exec_driver_sql("CREATE INDEX ix_detections_job_frame ON detections (job_id, frame_index)")
            connection.exec_driver_sql("CREATE INDEX ix_detections_job_track_frame ON detections (job_id, track_id, frame_index)")
            connection.execute(text("UPDATE detections SET track_id=9 WHERE detection_id='det-1'"))

    @staticmethod
    def _tables(engine):
        return sorted(inspect(engine).get_table_names())

    @staticmethod
    def _index_map(engine, table):
        return {index["name"]: tuple(index["column_names"]) for index in inspect(engine).get_indexes(table)}

    def _assert_model_schema(self, engine):
        inspector = inspect(engine)
        self.assertEqual(set(inspector.get_table_names()), set(Base.metadata.tables))
        for table in Base.metadata.sorted_tables:
            columns = {column["name"] for column in inspector.get_columns(table.name)}
            self.assertTrue({column.name for column in table.columns}.issubset(columns), table.name)
            actual = self._index_map(engine, table.name)
            for index in table.indexes:
                self.assertIn(index.name, actual)
                self.assertEqual(actual[index.name], tuple(column.name for column in index.columns))

    def test_fresh_database_initialization(self):
        engine = self._engine("fresh.sqlite3")
        try:
            apply_sqlite_migrations(engine, Base.metadata)
            self._assert_model_schema(engine)
            self.assertEqual(self._tables(engine), ["activity_logs", "agent_configs", "agent_executions", "agent_workflows",
                                                    "ai_reports", "analysis_jobs", "detections",
                                                    "operational_events", "operational_reviews", "videos"])
        finally:
            engine.dispose()

    def test_existing_phase3_database_migrates_and_backs_up_before_changes(self):
        engine = self._engine("phase3.sqlite3")
        try:
            self._create_phase3(engine)
            backup = apply_sqlite_migrations(engine, Base.metadata)
            self.assertIsNotNone(backup)
            self.assertTrue(backup.is_file())
            backup_conn = sqlite3.connect(backup)
            try:
                self.assertEqual(backup_conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(backup_conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0], 1)
                self.assertNotIn("upload_idempotency_key", {row[1] for row in backup_conn.execute("PRAGMA table_info(videos)")})
            finally:
                backup_conn.close()
            with engine.connect() as connection:
                row = connection.execute(text("SELECT id, original_filename, file_size, upload_idempotency_key FROM videos")).one()
                self.assertEqual(tuple(row), ("video-1", "phase3.mp4", 777, None))
            self._assert_model_schema(engine)
            self.assertEqual(self._tables(engine), ["activity_logs", "agent_configs", "agent_executions", "agent_workflows",
                                                    "ai_reports", "analysis_jobs", "detections",
                                                    "operational_events", "operational_reviews", "videos"])
        finally:
            engine.dispose()

    def test_existing_phase4_database_adds_inference_tracking_columns_without_losing_rows(self):
        engine = self._engine("phase4.sqlite3")
        try:
            self._create_phase4(engine)
            apply_sqlite_migrations(engine, Base.metadata)
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT inference_device, tracking_enabled FROM analysis_jobs WHERE job_id='job-1'")).one(), (None, 1))
                self.assertEqual(connection.execute(text("SELECT track_id FROM detections WHERE detection_id='det-1'")).scalar_one(), None)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM videos")).scalar_one(), 1)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM analysis_jobs")).scalar_one(), 1)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM detections")).scalar_one(), 1)
            self._assert_model_schema(engine)
        finally:
            engine.dispose()

    def test_existing_phase5_database_repairs_missing_inference_column_and_upload_index(self):
        engine = self._engine("phase5.sqlite3")
        try:
            self._create_phase5(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP INDEX IF EXISTS uq_videos_upload_idempotency_key")
            apply_sqlite_migrations(engine, Base.metadata)
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT inference_device FROM analysis_jobs WHERE job_id='job-1'")).scalar_one(), None)
                self.assertEqual(connection.execute(text("SELECT track_id FROM detections WHERE detection_id='det-1'")).scalar_one(), 9)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM detections WHERE track_id IS NOT NULL")).scalar_one(), 1)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM videos")).scalar_one(), 1)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM analysis_jobs")).scalar_one(), 1)
            self.assertIn("uq_videos_upload_idempotency_key", self._index_map(engine, "videos"))
            self._assert_model_schema(engine)
        finally:
            engine.dispose()

    def test_missing_inference_device_column_is_added_as_nullable(self):
        engine = self._engine("missing-inference.sqlite3")
        try:
            self._create_phase4(engine)
            apply_sqlite_migrations(engine, Base.metadata)
            column = next((column for column in inspect(engine).get_columns("analysis_jobs") if column["name"] == "inference_device"), None)
            self.assertIsNotNone(column)
            self.assertTrue(column["nullable"])
            self.assertIsNone(column["default"])
        finally:
            engine.dispose()

    def test_missing_upload_idempotency_index_is_added_only_after_column_exists(self):
        engine = self._engine("missing-upload-index.sqlite3")
        try:
            apply_sqlite_migrations(engine, Base.metadata)
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP INDEX uq_videos_upload_idempotency_key")
            apply_sqlite_migrations(engine, Base.metadata)
            self.assertEqual(self._index_map(engine, "videos")["uq_videos_upload_idempotency_key"], ("upload_idempotency_key",))
        finally:
            engine.dispose()

    def test_malformed_named_index_is_replaced_by_the_declared_definition(self):
        engine = self._engine("malformed-index.sqlite3")
        try:
            apply_sqlite_migrations(engine, Base.metadata)
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP INDEX uq_videos_upload_idempotency_key")
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX uq_videos_upload_idempotency_key ON videos (storage_key)"
                )
            apply_sqlite_migrations(engine, Base.metadata)
            self.assertEqual(
                self._index_map(engine, "videos")["uq_videos_upload_idempotency_key"],
                ("upload_idempotency_key",),
            )
        finally:
            engine.dispose()

    def test_migration_is_idempotent_on_repeated_startup(self):
        engine = self._engine("repeat.sqlite3")
        try:
            self._create_phase5(engine)
            apply_sqlite_migrations(engine, Base.metadata)
            first_tables = self._tables(engine)
            first_indexes = {table: self._index_map(engine, table) for table in first_tables}
            second_backup = apply_sqlite_migrations(engine, Base.metadata)
            self.assertIsNone(second_backup)
            self.assertEqual(self._tables(engine), first_tables)
            self.assertEqual({table: self._index_map(engine, table) for table in first_tables}, first_indexes)
        finally:
            engine.dispose()

    def test_existing_tracking_events_remain_queryable_after_repeated_migration(self):
        engine = self._engine("tracking.sqlite3")
        try:
            self._create_phase5(engine)
            apply_sqlite_migrations(engine, Base.metadata)
            apply_sqlite_migrations(engine, Base.metadata)
            with engine.connect() as connection:
                event = connection.execute(text("SELECT job_id, track_id, frame_index, timestamp_seconds FROM detections WHERE detection_id='det-1'")).one()
            self.assertEqual(tuple(event), ("job-1", 9, 2, 0.5))
        finally:
            engine.dispose()

    def test_activity_logs_are_append_only_and_triggers_are_idempotent(self):
        engine = self._engine("activity-append-only.sqlite3")
        try:
            apply_sqlite_migrations(engine, Base.metadata)
            with engine.begin() as connection:
                connection.exec_driver_sql("INSERT INTO activity_logs (id, category, event_type, title, description, severity, status, source, metadata_json, created_at) VALUES ('log-1','SYSTEM','system.test','Test','Test event','INFO','COMPLETED','test','{}','2026-01-01 00:00:00')")
            with engine.begin() as connection:
                with self.assertRaises(Exception):
                    connection.exec_driver_sql("UPDATE activity_logs SET title='changed' WHERE id='log-1'")
            with engine.begin() as connection:
                with self.assertRaises(Exception):
                    connection.exec_driver_sql("DELETE FROM activity_logs WHERE id='log-1'")
            self.assertIsNone(apply_sqlite_migrations(engine, Base.metadata))
            with engine.connect() as connection:
                self.assertEqual(connection.exec_driver_sql("SELECT title FROM activity_logs WHERE id='log-1'").scalar_one(), "Test")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
