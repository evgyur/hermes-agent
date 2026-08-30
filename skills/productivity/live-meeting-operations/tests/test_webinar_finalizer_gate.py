import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "webinar_finalizer_gate.py"
SPEC = importlib.util.spec_from_file_location("webinar_finalizer_gate", MODULE_PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


class FinalizerGateTests(unittest.TestCase):
    def _run(self, status, returncode):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"pipeline_argv": ["finalize"], "timeout_seconds": 30}), encoding="utf-8")
            config.chmod(0o600)
            completed = mock.Mock(returncode=returncode, stdout=json.dumps({"status": status}) + "\n", stderr="")
            with mock.patch.object(gate.subprocess, "run", return_value=completed), mock.patch("builtins.print") as printer:
                self.assertEqual(gate.main(["--config", str(config)]), 0)
            return json.loads(printer.call_args.args[0])

    def test_deferred_stays_silent_for_retry(self):
        self.assertFalse(self._run("FINALIZATION_DEFERRED", 75)["wakeAgent"])

    def test_processing_stays_silent_for_retry(self):
        self.assertFalse(self._run("ARTIFACTS_PROCESSING", 3)["wakeAgent"])

    def test_rejects_group_readable_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"pipeline_argv": ["finalize"]}), encoding="utf-8")
            config.chmod(0o640)
            with mock.patch.object(gate.subprocess, "run") as runner, mock.patch("builtins.print") as printer:
                self.assertEqual(gate.main(["--config", str(config)]), 0)
            runner.assert_not_called()
            payload = json.loads(printer.call_args.args[0])
            self.assertTrue(payload["wakeAgent"])

    def test_complete_wakes_agent(self):
        self.assertTrue(self._run("PACKAGE_COMPLETE", 0)["wakeAgent"])

    def test_real_blocker_wakes_agent(self):
        self.assertTrue(self._run("RECORDING_MISSING", 5)["wakeAgent"])


if __name__ == "__main__":
    unittest.main()
