"""Regression coverage for gateway inactivity-warning visibility."""

from gateway.run import _build_inactivity_warning_text


def test_final_answer_first_suppresses_staged_inactivity_warning():
    assert (
        _build_inactivity_warning_text(
            long_running_mode="off",
            warning_after_seconds=900,
            timeout_after_seconds=1800,
        )
        is None
    )


def test_opted_in_surface_keeps_staged_inactivity_warning():
    warning = _build_inactivity_warning_text(
        long_running_mode="raw",
        warning_after_seconds=900,
        timeout_after_seconds=1800,
    )

    assert warning is not None
    assert "No activity for 15 min" in warning
    assert "timed out in 15 min" in warning
    assert "/reset" in warning
