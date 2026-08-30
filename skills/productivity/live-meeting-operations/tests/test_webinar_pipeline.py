import argparse
import fcntl
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "webinar_pipeline.py"
SPEC = importlib.util.spec_from_file_location("webinar_pipeline", MODULE_PATH)
assert SPEC and SPEC.loader
pipeline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pipeline
SPEC.loader.exec_module(pipeline)


class WebinarPipelineTests(unittest.TestCase):
    def _fixture(self, root: Path):
        source = root / "source.json"
        source.write_text(json.dumps({"schema": 1, "selection": {"final_domain": "room.example"}}), encoding="utf-8")
        media = root / "conference.mkv"
        media.write_bytes(b"fake-media")
        transcript = root / "transcript.md"
        transcript.write_text("# Transcript\n\n[00:00:00] hello\n", encoding="utf-8")
        summary = root / "summary.md"
        summary.write_text("# Summary\n", encoding="utf-8")
        speakers = root / "speakers"
        speakers.mkdir()
        (speakers / "01-speaker.md").write_text("# Speaker\n", encoding="utf-8")
        state = root / "state.json"
        init_args = argparse.Namespace(
            state=str(state),
            title="Example Webinar",
            event_date="2026-08-29",
            timezone="Europe/Moscow",
            scheduled_start="2026-08-29T16:00:00+03:00",
            scheduled_end="2026-08-29T20:00:00+03:00",
            capture_started_at="2026-08-29T16:25:00+03:00",
            official_replay_published_at=None,
            source_receipt=str(source),
            media=str(media),
        )
        self.assertEqual(pipeline.initialize(init_args), 0)
        final_args = argparse.Namespace(
            state=str(state),
            package_dir=str(root / "package"),
            transcript=str(transcript),
            speaker_dir=str(speakers),
            summary=str(summary),
            decisions=None,
            ideas=None,
            source_links=None,
            asr_command_file=None,
            finalization_lock=str(root / "finalize.lock"),
            resume_delay=300,
            ffprobe="ffprobe",
            ffmpeg="ffmpeg",
        )
        return state, final_args

    @staticmethod
    def _verified_metadata():
        return {
            "bytes": 10,
            "duration_seconds": 100.0,
            "format_name": "matroska",
            "video": {"codec": "h264", "width": 1280, "height": 720},
            "audio": {"codec": "aac", "sample_rate": "48000", "channels": 2},
            "decode_samples": [{"label": "first", "decodable": True}],
            "sha256": "a" * 64,
            "verified_at": "2026-08-30T00:00:00+00:00",
        }

    def test_lock_contention_is_deferred_after_recording_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, args = self._fixture(root)
            lock_handle = open(args.finalization_lock, "a+", encoding="utf-8")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with mock.patch.object(pipeline, "verify_recording", return_value=self._verified_metadata()):
                    rc = pipeline.finalize(args)
            finally:
                lock_handle.close()
            self.assertEqual(rc, 75)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "FINALIZATION_DEFERRED")
            self.assertEqual(state["phases"]["recording_integrity"], "VERIFIED")
            self.assertNotEqual(state["status"], "RECORDING_MISSING")
            self.assertTrue(Path(state["recording"]["receipt_path"]).is_file())
            self.assertFalse(Path(args.package_dir).exists())

    def test_resume_builds_complete_private_package_with_semantic_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, args = self._fixture(root)
            with mock.patch.object(pipeline, "verify_recording", return_value=self._verified_metadata()):
                rc = pipeline.finalize(args)
            self.assertEqual(rc, 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "PACKAGE_COMPLETE")
            package = Path(args.package_dir)
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            dates = json.loads((package / "semantic-dates.json").read_text(encoding="utf-8"))
            paths = {entry["path"] for entry in manifest["artifacts"]}
            self.assertIn("recording-receipt.json", paths)
            self.assertIn("source-receipt.json", paths)
            self.assertIn("transcript.md", paths)
            self.assertIn("transcripts-by-speaker/01-speaker.md", paths)
            self.assertEqual(dates["event_date"], "2026-08-29")
            self.assertEqual(dates["capture_started_at"], "2026-08-29T16:25:00+03:00")
            self.assertIsNone(dates["official_replay_published_at"])
            for path in package.rglob("*"):
                if path.is_file():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_resume_reuses_integrity_receipt_when_file_identity_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, args = self._fixture(root)
            lock_handle = open(args.finalization_lock, "w", encoding="utf-8")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with mock.patch.object(pipeline, "verify_recording", return_value=self._verified_metadata()) as verify:
                    self.assertEqual(pipeline.finalize(args), 75)
                    verify.assert_called_once()
            finally:
                lock_handle.close()
            with mock.patch.object(pipeline, "verify_recording", side_effect=AssertionError("must reuse receipt")):
                self.assertEqual(pipeline.finalize(args), 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["recording"]["integrity_reused"])

    def test_atomic_rebuild_drops_stale_optional_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, args = self._fixture(root)
            with mock.patch.object(pipeline, "verify_recording", return_value=self._verified_metadata()):
                self.assertEqual(pipeline.finalize(args), 0)
                self.assertTrue((Path(args.package_dir) / "summary.md").is_file())
                args.summary = None
                self.assertEqual(pipeline.finalize(args), 0)
            self.assertFalse((Path(args.package_dir) / "summary.md").exists())

    def test_capture_receipt_links_source_type_domain_readiness_and_growth(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_path, final_args = self._fixture(root)
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
            media = Path(state_payload["capture"]["media_path"])
            source = Path(state_payload["source"]["receipt_path"])
            source.write_text(
                json.dumps(
                    {
                        "source": {"chat_id": "8806427465", "message_id": 826985},
                        "selection": {"source_type": "inline_button", "final_domain": "start.bizon365.ru"},
                    }
                ),
                encoding="utf-8",
            )
            media.write_bytes(b"growing-capture")
            receipt = root / "capture-receipt.json"
            args = argparse.Namespace(
                state=str(state_path),
                media=None,
                receipt_output=str(receipt),
                media_ready="true",
                growth_window=0,
                require_growth=False,
                ffprobe="ffprobe",
            )
            with mock.patch.object(
                pipeline,
                "_probe_media",
                return_value={"bytes": media.stat().st_size, "duration_seconds": 2.0, "video": {"codec": "h264"}},
            ):
                self.assertEqual(pipeline.capture_receipt(args), 0)
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "CAPTURE_ACTIVE_VERIFIED")
            self.assertEqual(payload["source"]["link_type"], "inline_button")
            self.assertEqual(payload["source"]["final_domain"], "start.bizon365.ru")
            self.assertTrue(payload["media_ready"])
            self.assertTrue(payload["file_growth_verified"])
            with mock.patch.object(pipeline, "verify_recording", return_value=self._verified_metadata()):
                self.assertEqual(pipeline.finalize(final_args), 0)
            self.assertTrue((Path(final_args.package_dir) / "capture-receipt.json").is_file())

    def test_missing_recording_is_only_reported_by_integrity_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, args = self._fixture(root)
            Path(json.loads(state_path.read_text())["capture"]["media_path"]).unlink()
            rc = pipeline.finalize(args)
            self.assertEqual(rc, 5)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "RECORDING_MISSING")
            self.assertEqual(state["phases"]["recording_integrity"], "MISSING")


if __name__ == "__main__":
    unittest.main()
