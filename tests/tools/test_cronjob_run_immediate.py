"""Tests for cronjob action='run' execution modes (#41037).

Direct CLI/Python calls still claim and execute immediately so CLI-only setups do
not report a false success when no scheduler is running. Agent tool calls carry a
``task_id`` and instead queue the job for the gateway scheduler: a long cron agent
must not block the parent chat turn or trip its inactivity watchdog.
"""
import json
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from tools.cronjob_tools import cronjob, _execute_job_now
from tools.environments.base import set_activity_callback


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


    def test_execute_job_now_bails_without_claim(self):
        """_execute_job_now never calls run_one_job when the claim is lost."""
        with patch("tools.cronjob_tools.claim_job_for_fire", return_value=False), \
             patch("cron.scheduler.run_one_job") as m_run:
            res = _execute_job_now(dict(_JOB))
        assert res["claimed"] is False
        assert res["success"] is False
        m_run.assert_not_called()

    def test_execute_job_now_passes_live_gateway_context_to_delivery(self):
        """Manual runs must deliver on the live gateway adapter's owning loop."""
        adapters = {"matrix": object()}
        gateway_loop = object()
        runner = SimpleNamespace(adapters=adapters, _gateway_loop=gateway_loop)
        completed = {"id": "job-run-1", "last_status": "ok", "last_error": None}

        with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
             patch("gateway.run._gateway_runner_ref", return_value=runner), \
             patch("cron.scheduler.run_one_job", return_value=True) as m_run, \
             patch("tools.cronjob_tools.get_job", return_value=completed):
            res = _execute_job_now(dict(_JOB))

        assert res["success"] is True
        m_run.assert_called_once_with(
            _JOB,
            adapters=adapters,
            loop=gateway_loop,
            extra_prompt=None,
        )

    def test_execute_job_now_remains_standalone_without_gateway(self):
        """CLI-only runs retain the standalone delivery path."""
        completed = {"id": "job-run-1", "last_status": "ok", "last_error": None}

        with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
             patch.dict(sys.modules, {"gateway.run": None}), \
             patch("cron.scheduler.run_one_job", return_value=True) as m_run, \
             patch("tools.cronjob_tools.get_job", return_value=completed):
            res = _execute_job_now(dict(_JOB))

        assert res["success"] is True
        m_run.assert_called_once_with(_JOB, adapters=None, loop=None, extra_prompt=None)

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

    def test_execute_job_now_heartbeats_while_job_runs(self):
        """A manual run ticks the caller's activity tracker while the job
        executes so the gateway inactivity watchdog doesn't kill the parent
        turn (#76502)."""
        touches = []
        heartbeat_seen = threading.Event()

        def record(desc):
            touches.append(desc)
            heartbeat_seen.set()

        set_activity_callback(record)
        try:
            def slow_run(job, **kw):
                # Deterministic: block until at least one heartbeat has fired
                # (bounded so a broken heartbeat can't hang the test).
                assert heartbeat_seen.wait(timeout=5.0), "no heartbeat within 5s"
                return True

            with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
                 patch("tools.cronjob_tools._CRON_RUN_HEARTBEAT_INTERVAL", 0.05), \
                 patch("cron.scheduler.run_one_job", side_effect=slow_run) as m_run, \
                 patch("tools.cronjob_tools.get_job",
                       return_value={"last_status": "ok", "last_error": None}):
                res = _execute_job_now(dict(_JOB))

            m_run.assert_called_once()
            assert res["success"] is True, res
            assert any("cronjob: running job" in t for t in touches), touches
        finally:
            set_activity_callback(None)

    def test_execute_job_now_without_callback_does_not_heartbeat(self):
        """No activity callback registered (direct callers, tests) → the
        heartbeat thread is never started and behavior is unchanged."""
        set_activity_callback(None)
        try:
            with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
                 patch("cron.scheduler.run_one_job", return_value=True) as m_run, \
                 patch("tools.cronjob_tools.get_job",
                       return_value={"last_status": "ok", "last_error": None}), \
                 patch("tools.cronjob_tools.threading.Thread") as m_thread:
                res = _execute_job_now(dict(_JOB))
            assert res["success"] is True
            m_run.assert_called_once()
            m_thread.assert_not_called()   # heartbeat thread truly never created
        finally:
            set_activity_callback(None)

    def test_heartbeat_stops_at_ceiling_but_job_completes(self):
        """Past _CRON_RUN_HEARTBEAT_CEILING the heartbeat stops (so the
        gateway watchdog regains authority over a wedged run) while the job
        itself keeps running to completion."""
        touches = []
        first_beat = threading.Event()

        def record(desc):
            touches.append(desc)
            first_beat.set()

        set_activity_callback(record)
        try:
            def slow_run(job, **kw):
                # Ceiling=0 → the very first wake stops the loop without
                # touching. Give it a couple of cycles to prove silence.
                time.sleep(0.2)
                return True

            with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
                 patch("tools.cronjob_tools._CRON_RUN_HEARTBEAT_INTERVAL", 0.05), \
                 patch("tools.cronjob_tools._CRON_RUN_HEARTBEAT_CEILING", 0.0), \
                 patch("cron.scheduler.run_one_job", side_effect=slow_run), \
                 patch("tools.cronjob_tools.get_job",
                       return_value={"last_status": "ok", "last_error": None}):
                res = _execute_job_now(dict(_JOB))
            assert res["success"] is True, res
            assert not first_beat.is_set(), touches   # heartbeat never fired
        finally:
            set_activity_callback(None)

    def test_heartbeat_survives_callback_exception(self):
        """One raising callback must not silently kill watchdog protection
        for the rest of a long job — the loop continues heartbeating."""
        calls = []
        second_beat = threading.Event()

        def flaky(desc):
            calls.append(desc)
            if len(calls) >= 2:
                second_beat.set()
            if len(calls) == 1:
                raise RuntimeError("transient")

        set_activity_callback(flaky)
        try:
            def slow_run(job, **kw):
                # Block until a heartbeat AFTER the raising one has fired.
                assert second_beat.wait(timeout=5.0), \
                    "heartbeat stopped after one callback exception"
                return True

            with patch("tools.cronjob_tools.claim_job_for_fire", return_value=True), \
                 patch("tools.cronjob_tools._CRON_RUN_HEARTBEAT_INTERVAL", 0.05), \
                 patch("cron.scheduler.run_one_job", side_effect=slow_run), \
                 patch("tools.cronjob_tools.get_job",
                       return_value={"last_status": "ok", "last_error": None}):
                res = _execute_job_now(dict(_JOB))
            assert res["success"] is True, res
            assert len(calls) >= 2, calls
        finally:
            set_activity_callback(None)
