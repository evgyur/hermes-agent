"""Single-owner replacement semantics for external platform transports."""

from hermes_cli.plugins import PluginManager, PluginManifest


def _manifest(*, source: str, key: str) -> PluginManifest:
    return PluginManifest(
        name="telegram-platform",
        version="1.0.0",
        source=source,
        path=f"/{source}/{key}",
        key=key,
        kind="platform",
    )


def _run_discovery(monkeypatch, manifests, enabled):
    manager = PluginManager()
    deferred = []
    loaded = []
    monkeypatch.setattr(manager, "_collect_directory_manifests", lambda: manifests)
    monkeypatch.setattr(manager, "_scan_entry_points", lambda: [])
    monkeypatch.setattr(
        "hermes_cli.plugins._get_enabled_plugins", lambda: set(enabled)
    )
    monkeypatch.setattr("hermes_cli.plugins._get_disabled_plugins", lambda: set())
    monkeypatch.setattr(manager, "_register_deferred_platform", deferred.append)
    monkeypatch.setattr(manager, "_load_plugin", loaded.append)
    manager._discover_and_load_inner()
    return manager, deferred, loaded


def test_enabled_user_platform_suppresses_same_name_bundled_owner(monkeypatch):
    bundled = _manifest(source="bundled", key="platforms/telegram")
    user = _manifest(source="user", key="telegram-platform")

    _manager, deferred, loaded = _run_discovery(
        monkeypatch, [bundled, user], {"telegram-platform"}
    )

    assert deferred == []
    assert loaded == [user]


def test_not_enabled_user_platform_leaves_bundled_fallback_owner(monkeypatch):
    bundled = _manifest(source="bundled", key="platforms/telegram")
    user = _manifest(source="user", key="telegram-platform")

    manager, deferred, loaded = _run_discovery(monkeypatch, [bundled, user], set())

    assert deferred == [bundled]
    assert loaded == []
    assert manager._plugins["telegram-platform"].enabled is False


def test_project_platform_wins_over_enabled_user_replacement(monkeypatch):
    bundled = _manifest(source="bundled", key="platforms/telegram")
    user = _manifest(source="user", key="user/telegram")
    project = _manifest(source="project", key="project/telegram")

    _manager, deferred, loaded = _run_discovery(
        monkeypatch,
        [bundled, user, project],
        {"user/telegram", "project/telegram"},
    )

    assert deferred == []
    assert loaded == [project]
