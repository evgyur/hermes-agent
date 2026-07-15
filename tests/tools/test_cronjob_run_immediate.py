"""Tests for cronjob action='run' execution modes (#41037).

Direct CLI/Python calls still claim and execute immediately so CLI-only setups do
not report a false success when no scheduler is running. Agent tool calls carry a
``task_id`` and instead queue the job for the gateway scheduler: a long cron agent
must not block the parent chat turn or trip its inactivity watchdog.
"""
import json
from unittest.mock import patch

from tools.cronjob_tools import cronjob, _execute_job_now


_JOB = {"id": "job-run-1", "name": "manual run", "prompt": "hi",
        "schedule": {"kind": "cron", "expr": "0 9 * * *"}}


class TestCronjobRunExecutesImmediately:
    def test_agent_run_queues_without_blocking_the_tool_call(self):
        """Agent tool calls queue work; they must not wait for the cron agent."""
        queued = dict(_JOB, next_run_at="2026-07-15T15:30:00+03:00")
        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools._execute_job_now", return_value={
                 "claimed": True, "success": True, "error": None,
             }) as m_execute, \
             patch("tools.cronjob_tools.trigger_job_if_active", return_value=(queued, "queued")) as m_trigger:
            out = json.loads(
                cronjob(action="run", job_id="job-run-1", task_id="gateway-turn-1")
            )

        assert out["success"] is True
        assert out["job"]["execution_state"] == "queued"
        assert out["job"]["executed"] is False
        m_trigger.assert_called_once_with("job-run-1")
        m_execute.assert_not_called()

    def test_agent_run_does_not_reactivate_pause_that_wins_race(self):
        """An atomic pause after initial lookup must beat the queued run request."""
        paused = dict(_JOB, enabled=False, state="paused")
        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools.trigger_job_if_active", return_value=(paused, "paused")) as m_safe_trigger, \
             patch("cron.jobs.trigger_job", return_value=dict(_JOB)) as m_reactivating_trigger, \
             patch("tools.cronjob_tools.get_job", return_value=paused):
            out = json.loads(
                cronjob(action="run", job_id="job-run-1", task_id="gateway-turn-1")
            )

        assert out["success"] is True
        assert out["job"]["execution_state"] == "skipped"
        assert "paused" in out["job"]["execution_skipped"].lower()
        m_safe_trigger.assert_called_once_with("job-run-1")
        m_reactivating_trigger.assert_not_called()

    def test_atomic_agent_trigger_keeps_paused_job_paused(self):
        """The store-level queue operation checks and updates under one lock."""
        from cron.jobs import (
            create_job,
            get_job,
            pause_job,
            remove_job,
            trigger_job_if_active,
        )

        job = create_job(name="paused race", schedule="0 9 * * *", prompt="hi")
        try:
            pause_job(job["id"])
            snapshot, status = trigger_job_if_active(job["id"])
            assert status == "paused"
            assert snapshot is not None
            assert snapshot["state"] == "paused"
            stored = get_job(job["id"])
            assert stored is not None
            assert stored["enabled"] is False
            assert stored["state"] == "paused"
        finally:
            remove_job(job["id"])

    def test_run_action_claims_and_fires_via_run_one_job(self):
        """action='run' must claim the job then fire it through run_one_job."""
        ran = {"job": "after-run", "last_status": "ok", "last_error": None}
        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools.claim_job_for_fire", return_value=True) as m_claim, \
             patch("cron.scheduler.run_one_job", return_value=True) as m_run, \
             patch("tools.cronjob_tools.get_job", return_value=ran):
            out = json.loads(cronjob(action="run", job_id="job-run-1"))

        assert out["success"] is True
        assert out["job"]["executed"] is True
        assert out["job"]["execution_success"] is True
        m_claim.assert_called_once_with("job-run-1")   # at-most-once claim taken
        m_run.assert_called_once()                       # fired via the shared body

    def test_run_skips_when_claim_lost(self):
        """If the scheduler already holds the fire claim, do NOT double-run."""
        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools.claim_job_for_fire", return_value=False), \
             patch("cron.scheduler.run_one_job") as m_run, \
             patch("tools.cronjob_tools.get_job", return_value=dict(_JOB)):
            out = json.loads(cronjob(action="run", job_id="job-run-1"))

        assert out["success"] is True
        assert out["job"]["executed"] is False
        assert out["job"]["execution_success"] is False
        assert "execution_skipped" in out["job"]
        m_run.assert_not_called()  # claim lost -> never fired

    def test_run_reports_failure_from_last_status(self):
        """A failed run is reported via the re-read job's last_status/last_error."""
        failed = {"id": "job-run-1", "last_status": "error", "last_error": "provider 500"}
        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.run_one_job", return_value=True), \
             patch("tools.cronjob_tools.get_job", return_value=failed):
            out = json.loads(cronjob(action="run", job_id="job-run-1"))

        assert out["job"]["executed"] is True
        assert out["job"]["execution_success"] is False
        assert out["job"]["execution_error"] == "provider 500"

    def test_execute_job_now_bails_without_claim(self):
        """_execute_job_now never calls run_one_job when the claim is lost."""
        with patch("tools.cronjob_tools.claim_job_for_fire", return_value=False), \
             patch("cron.scheduler.run_one_job") as m_run:
            res = _execute_job_now(dict(_JOB))
        assert res["claimed"] is False
        assert res["success"] is False
        m_run.assert_not_called()

    def test_execute_job_now_marks_failure_on_exception(self):
        """An exception during fire is captured, marked failed, not propagated."""
        with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
             patch("cron.scheduler.run_one_job", side_effect=RuntimeError("boom")), \
             patch("tools.cronjob_tools.mark_job_run") as m_mark, \
             patch("tools.cronjob_tools.get_job", return_value=dict(_JOB)):
            res = _execute_job_now(dict(_JOB))
        assert res["claimed"] is True
        assert res["success"] is False
        assert "boom" in res["error"]
        m_mark.assert_called_once()
