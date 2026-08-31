#!/usr/bin/env bash
set -Eeuo pipefail

# Hermes Powerpack installer/upgrader for an existing git-based Hermes install.
# It changes only the code checkout and its venv. HERMES_HOME (config, profiles,
# state.db and telegram-chip credentials/sessions) is deliberately never copied,
# migrated, reset, or removed.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$ROOT"
POWERPACK_REPO_URL="${HERMES_POWERPACK_REPO_URL:-}"
INSTALL_DIR="${HERMES_INSTALL_DIR:-}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DRY_RUN=false
SYNC_DEPS=true
RESTART=false
SERVICE_NAME="${HERMES_GATEWAY_SERVICE:-hermes-gateway.service}"

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Hermes Powerpack installer/upgrader

Usage: scripts/install-powerpack.sh [options]

  --dry-run           Read-only preflight; print the exact planned candidate
  --source-dir PATH   Powerpack checkout to install (default: this checkout)
  --repo-url URL      Durable Powerpack git remote (default: source origin)
  --dir PATH          Hermes code checkout to install/upgrade
  --hermes-home PATH  Existing Hermes data home (default: $HERMES_HOME or ~/.hermes)
  --restart           Stop/restart an active hermes-gateway around the update
  --no-sync           Skip dependency sync (advanced/testing only)
  -h, --help          Show this help

The installer never migrates or merges state.db and never edits telegram-chip
credentials. A divergent or dirty code checkout fails closed.
EOF
}

while (($#)); do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --source-dir) SOURCE_DIR="${2:?missing value for --source-dir}"; shift 2 ;;
        --repo-url) POWERPACK_REPO_URL="${2:?missing value for --repo-url}"; shift 2 ;;
        --dir) INSTALL_DIR="${2:?missing value for --dir}"; shift 2 ;;
        --hermes-home) HERMES_HOME="${2:?missing value for --hermes-home}"; shift 2 ;;
        --restart) RESTART=true; shift ;;
        --no-sync) SYNC_DEPS=false; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

normalize_path() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1"
    else
        printf '%s\n' "$1"
    fi
}

git_at() {
    local repo="$1"
    shift
    git -c "safe.directory=$repo" -C "$repo" "$@"
}

is_git_checkout() {
    local repo="$1"
    [[ "$(git_at "$repo" rev-parse --is-inside-work-tree 2>/dev/null || true)" == true ]]
}

SOURCE_DIR="$(normalize_path "$SOURCE_DIR")"
HERMES_HOME="$(normalize_path "$HERMES_HOME")"
if [[ -z "$INSTALL_DIR" ]]; then
    if is_git_checkout /opt/hermes-agent; then
        INSTALL_DIR=/opt/hermes-agent
    elif is_git_checkout "$HERMES_HOME/hermes-agent"; then
        INSTALL_DIR="$HERMES_HOME/hermes-agent"
    else
        INSTALL_DIR="$HERMES_HOME/hermes-agent"
    fi
fi
INSTALL_DIR="$(normalize_path "$INSTALL_DIR")"

is_git_checkout "$SOURCE_DIR" || die "source is not a git checkout: $SOURCE_DIR"
candidate_sha="$(git_at "$SOURCE_DIR" rev-parse 'HEAD^{commit}')"
[[ "$candidate_sha" =~ ^[0-9a-f]{40}$ ]] || die "cannot resolve candidate SHA"

init_version="$(sed -nE 's/^__version__[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$SOURCE_DIR/hermes_cli/__init__.py" | head -n1)"
project_version="$(sed -nE 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$SOURCE_DIR/pyproject.toml" | head -n1)"
[[ -n "$init_version" && "$init_version" == "$project_version" ]] \
    || die "candidate version metadata is missing or inconsistent"

release_manifest="$SOURCE_DIR/powerpack/release.json"
powerpack_version="$(grep -Eo '"powerpack_version"[[:space:]]*:[[:space:]]*"[^"]+"' "$release_manifest" | head -n1 | sed -E 's/.*"([^"]+)"$/\1/' | tr -d '\r')"
manifest_hermes_version="$(grep -Eo '"hermes_version"[[:space:]]*:[[:space:]]*"[^"]+"' "$release_manifest" | head -n1 | sed -E 's/.*"([^"]+)"$/\1/' | tr -d '\r')"
plugin_version="$(sed -nE 's/^version:[[:space:]]*([^[:space:]]+).*/\1/p' "$SOURCE_DIR/packages/powerpack-gen2/plugin.yaml" | head -n1 | tr -d '\r')"
metadata_version="$(grep -Eo '"version"[[:space:]]*:[[:space:]]*"[^"]+"' "$SOURCE_DIR/packages/powerpack-gen2/metadata/powerpack-gen2.json" | head -n1 | sed -E 's/.*"([^"]+)"$/\1/' | tr -d '\r')"
[[ -n "$powerpack_version" && "$powerpack_version" == "$plugin_version" && "$powerpack_version" == "$metadata_version" ]] \
    || die "Powerpack version metadata is missing or inconsistent"
[[ "$manifest_hermes_version" == "$init_version" ]] \
    || die "Hermes version metadata is missing or inconsistent"

if [[ -z "$POWERPACK_REPO_URL" ]]; then
    POWERPACK_REPO_URL="$(git_at "$SOURCE_DIR" remote get-url origin 2>/dev/null || true)"
fi
[[ -n "$POWERPACK_REPO_URL" ]] || die "Powerpack repo URL is unknown; pass --repo-url"

action=fresh_install
current_sha=""
current_branch=""
if [[ -e "$INSTALL_DIR" ]]; then
    is_git_checkout "$INSTALL_DIR" \
        || die "install directory is not a git checkout: $INSTALL_DIR"
    action=upgrade
    dirty_status="$(git_at "$INSTALL_DIR" status --porcelain=v1 --untracked-files=all)"
    [[ -z "$dirty_status" ]] \
        || die "existing Hermes checkout is dirty; preserve/commit those changes first: $dirty_status"
    current_sha="$(git_at "$INSTALL_DIR" rev-parse 'HEAD^{commit}')"
    current_branch="$(git_at "$INSTALL_DIR" branch --show-current)"
    ancestry_ok=false
    if git_at "$SOURCE_DIR" cat-file -e "$current_sha^{commit}" 2>/dev/null \
       && git_at "$SOURCE_DIR" merge-base --is-ancestor "$current_sha" "$candidate_sha"; then
        ancestry_ok=true
    fi
    predecessor_ok=false
    if [[ -f "$release_manifest" ]] \
       && grep -Eo '[0-9a-f]{40}' "$release_manifest" | grep -Fqx "$current_sha"; then
        predecessor_ok=true
    fi
    if [[ "$ancestry_ok" != true && "$predecessor_ok" != true ]]; then
        die "existing Hermes HEAD $current_sha is not an ancestor or registered predecessor of Powerpack $candidate_sha"
    fi
fi

check_upgrade_permissions() {
    [[ "$action" == upgrade ]] || return 0
    [[ "$(id -u)" != 0 ]] || return 0

    local git_dir
    git_dir="$(git_at "$INSTALL_DIR" rev-parse --git-dir)"
    if [[ "$git_dir" != /* ]]; then
        git_dir="$INSTALL_DIR/$git_dir"
    fi
    [[ -w "$git_dir" ]] || die \
        "existing Hermes git metadata is not writable; rerun the same preflight/install with sudo"

    if ! git_at "$SOURCE_DIR" cat-file -e "$current_sha^{commit}" 2>/dev/null; then
        [[ -w "$INSTALL_DIR" ]] || die \
            "existing Hermes checkout is not writable; rerun the same preflight/install with sudo"
        return 0
    fi

    local rel target probe write_dir
    while IFS= read -r -d '' rel; do
        target="$INSTALL_DIR/$rel"
        probe="$target"
        while [[ ! -e "$probe" && ! -L "$probe" && "$probe" != "$INSTALL_DIR" ]]; do
            probe="$(dirname "$probe")"
        done
        if [[ -d "$probe" ]]; then
            write_dir="$probe"
        else
            write_dir="$(dirname "$probe")"
        fi
        [[ -w "$write_dir" ]] || die \
            "existing Hermes path is not writable ($write_dir); rerun the same preflight/install with sudo"
    done < <(git_at "$SOURCE_DIR" diff --name-only -z "$current_sha" "$candidate_sha")
}

# Permission failures during git checkout can leave a partially materialized
# candidate even though HEAD never moved. Detect them before stopping a live
# gateway, including during --dry-run.
check_upgrade_permissions

printf 'powerpack_version=%s\n' "$powerpack_version"
printf 'hermes_version=%s\n' "$init_version"
printf 'candidate_sha=%s\n' "$candidate_sha"
printf 'action=%s\n' "$action"
printf 'install_dir=%s\n' "$INSTALL_DIR"
printf 'hermes_home=%s\n' "$HERMES_HOME"
printf 'data_action=preserve\n'
printf 'database_action=none\n'
printf 'telegram_chip_action=preserve\n'

if [[ "$DRY_RUN" == true ]]; then
    printf 'result=DRY_RUN_PASS\n'
    exit 0
fi

service_scope=""
if command -v systemctl >/dev/null 2>&1; then
    if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        service_scope=user
    elif systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        service_scope=system
    fi
fi
if [[ -n "$service_scope" && "$RESTART" != true ]]; then
    die "$SERVICE_NAME is active; rerun with --restart for a controlled stop/update/start"
fi

service_do() {
    if [[ "$service_scope" == user ]]; then
        systemctl --user "$@" "$SERVICE_NAME"
    elif [[ "$service_scope" == system ]]; then
        if [[ "$(id -u)" == 0 ]]; then
            systemctl "$@" "$SERVICE_NAME"
        else
            sudo -n systemctl "$@" "$SERVICE_NAME"
        fi
    fi
}

service_main_pid() {
    service_do show --property=MainPID --value
}

drain_marker_created=false
write_planned_restart_drain() {
    local control_python=""
    control_python="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
    [[ -n "$control_python" ]] || die "Python is required to arm lossless restart ingress"
    POWERPACK_DRAIN_HOME="$HERMES_HOME" "$control_python" - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

home = Path(os.environ["POWERPACK_DRAIN_HOME"])
path = home / ".drain_request.json"
if path.exists():
    raise SystemExit(f"active drain marker already exists: {path}")

boot_id = ""
try:
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
except OSError:
    pass
pid1_start = ""
try:
    tail = Path("/proc/1/stat").read_text().rsplit(")", 1)[1].split()
    pid1_start = tail[19]
except (OSError, IndexError):
    pass
epoch = f"{boot_id}:{pid1_start}" if boot_id or pid1_start else ""
payload = {
    "action": "drain",
    "requested_at": datetime.now(timezone.utc).isoformat(),
    "principal": "powerpack-installer",
    "epoch": epoch,
    "suppress_notification": False,
    "planned_restart": True,
}
home.mkdir(parents=True, exist_ok=True)
fd, tmp_name = tempfile.mkstemp(prefix=".drain_request.", dir=home)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp_name, 0o600)
    os.replace(tmp_name, path)
finally:
    try:
        os.unlink(tmp_name)
    except FileNotFoundError:
        pass
PY
    drain_marker_created=true
}

wait_for_planned_restart_drain() {
    local expected_pid="$1"
    local control_python=""
    control_python="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
    local status_path="$HERMES_HOME/gateway_state.json"
    local _
    for _ in {1..50}; do
        if POWERPACK_GATEWAY_STATUS="$status_path" \
           POWERPACK_GATEWAY_PID="$expected_pid" \
           "$control_python" - <<'PY'
import json
import os
from pathlib import Path

try:
    state = json.loads(Path(os.environ["POWERPACK_GATEWAY_STATUS"]).read_text())
except (OSError, ValueError, TypeError):
    raise SystemExit(1)
expected_pid = int(os.environ["POWERPACK_GATEWAY_PID"])
ok = (
    state.get("gateway_state") == "draining"
    and state.get("restart_requested") is True
    and int(state.get("pid") or 0) == expected_pid
)
raise SystemExit(0 if ok else 1)
PY
        then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

clear_planned_restart_drain() {
    [[ "$drain_marker_created" == true ]] || return 0
    rm -f -- "$HERMES_HOME/.drain_request.json"
    [[ ! -e "$HERMES_HOME/.drain_request.json" ]] \
        || die "could not clear planned restart drain marker"
    drain_marker_created=false
}

old_origin=""
backup_ref=""
mutated=false
service_stopped=false
rollback() {
    status=$?
    trap - EXIT
    set +e
    if [[ "$mutated" == true && -n "$current_sha" && -d "$INSTALL_DIR/.git" ]]; then
        if [[ -n "$current_branch" ]]; then
            git_at "$INSTALL_DIR" checkout -f -B "$current_branch" "$current_sha" >/dev/null 2>&1 || true
        else
            git_at "$INSTALL_DIR" checkout --detach -f "$current_sha" >/dev/null 2>&1 || true
        fi
        git_at "$INSTALL_DIR" clean -fd >/dev/null 2>&1 || true
        if [[ -n "$old_origin" ]]; then
            git_at "$INSTALL_DIR" remote set-url origin "$old_origin" >/dev/null 2>&1 || true
        fi
    fi
    clear_planned_restart_drain || true
    if [[ "$service_stopped" == true ]]; then
        service_do start >/dev/null 2>&1 || true
    fi
    printf 'result=ROLLBACK\n' >&2
    return "$status"
}
trap rollback EXIT

if [[ -n "$service_scope" ]]; then
    write_planned_restart_drain
    live_service_pid="$(service_main_pid)"
    [[ "$live_service_pid" =~ ^[1-9][0-9]*$ ]] \
        || die "cannot resolve live $SERVICE_NAME PID before planned restart"
    wait_for_planned_restart_drain "$live_service_pid" \
        || die "$SERVICE_NAME did not acknowledge lossless planned-restart ingress"
    service_do stop
    service_stopped=true
fi

if [[ "$action" == fresh_install ]]; then
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --branch main "$POWERPACK_REPO_URL" "$INSTALL_DIR"
    fetched_sha="$(git_at "$INSTALL_DIR" rev-parse 'HEAD^{commit}')"
    [[ "$fetched_sha" == "$candidate_sha" ]] \
        || die "remote main $fetched_sha does not match packaged candidate $candidate_sha"
    mutated=true
else
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup_ref="backup/powerpack-$timestamp"
    git_at "$INSTALL_DIR" branch "$backup_ref" "$current_sha"
    old_origin="$(git_at "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
    mutated=true
    if [[ -n "$old_origin" && "$old_origin" != "$POWERPACK_REPO_URL" ]] \
       && ! git_at "$INSTALL_DIR" remote get-url upstream >/dev/null 2>&1; then
        git_at "$INSTALL_DIR" remote add upstream "$old_origin"
    fi
    git_at "$INSTALL_DIR" remote set-url origin "$POWERPACK_REPO_URL"
    git_at "$INSTALL_DIR" fetch --no-tags origin main
    remote_sha="$(git_at "$INSTALL_DIR" rev-parse 'origin/main^{commit}')"
    [[ "$remote_sha" == "$candidate_sha" ]] \
        || die "remote main $remote_sha does not match packaged candidate $candidate_sha"
    git_at "$INSTALL_DIR" checkout -B main "$candidate_sha"
    git_at "$INSTALL_DIR" branch --set-upstream-to=origin/main main >/dev/null
fi

if [[ "$SYNC_DEPS" == true ]]; then
    uv_bin="${UV_BIN:-}"
    if [[ -z "$uv_bin" ]]; then
        uv_bin="$(command -v uv 2>/dev/null || true)"
    fi
    if [[ -z "$uv_bin" ]]; then
        for uv_candidate in \
            "$HERMES_HOME/bin/uv" \
            "$HOME/.local/bin/uv" \
            "$HERMES_HOME/uv/bin/uv"; do
            if [[ -x "$uv_candidate" ]]; then
                uv_bin="$uv_candidate"
                break
            fi
        done
    fi
    [[ -n "$uv_bin" ]] || die "uv is required for locked dependency sync"
    UV_NO_CACHE=1 UV_PROJECT_ENVIRONMENT="$INSTALL_DIR/venv" \
        "$uv_bin" sync --project "$INSTALL_DIR" --extra all --extra messaging --locked
    "$INSTALL_DIR/venv/bin/python" -c \
        "import hermes_cli; assert hermes_cli.__version__ == '$init_version'"

    if [[ "$(id -u)" == 0 ]]; then
        link_dir=/usr/local/bin
    else
        link_dir="$HOME/.local/bin"
    fi
    mkdir -p "$link_dir"
    ln -sfn "$INSTALL_DIR/hermes" "$link_dir/hermes"
fi

if [[ "$service_stopped" == true ]]; then
    clear_planned_restart_drain
    service_do start
    service_stopped=false
    service_do is-active --quiet
fi

receipt_dir="$HERMES_HOME/runtime/receipts"
mkdir -p "$receipt_dir"
receipt="$receipt_dir/powerpack-install-$powerpack_version-$candidate_sha.json"
if [[ -x "$INSTALL_DIR/venv/bin/python" ]]; then
    receipt_python="$INSTALL_DIR/venv/bin/python"
elif command -v python >/dev/null 2>&1; then
    receipt_python="$(command -v python)"
else
    receipt_python="$(command -v python3 2>/dev/null || true)"
fi
[[ -n "$receipt_python" ]] || die "Python is required to write the install receipt"
POWERPACK_RECEIPT="$receipt" POWERPACK_VERSION="$powerpack_version" \
POWERPACK_HERMES_VERSION="$init_version" \
POWERPACK_SHA="$candidate_sha" POWERPACK_PREVIOUS_SHA="$current_sha" \
POWERPACK_BACKUP_REF="$backup_ref" POWERPACK_INSTALL_DIR="$INSTALL_DIR" \
POWERPACK_HERMES_HOME="$HERMES_HOME" \
POWERPACK_RELEASE_MANIFEST="$SOURCE_DIR/powerpack/release.json" \
"$receipt_python" - <<'PY'
import json
import os
from pathlib import Path

component_pins = {}
manifest_path = Path(os.environ["POWERPACK_RELEASE_MANIFEST"])
if manifest_path.is_file():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_pins = manifest.get("component_pins", {})
    if isinstance(raw_pins, dict):
        component_pins = raw_pins

payload = {
    "format": "hermes-powerpack-install-v2",
    "result": "PASS",
    "version": os.environ["POWERPACK_VERSION"],
    "powerpack_version": os.environ["POWERPACK_VERSION"],
    "hermes_version": os.environ["POWERPACK_HERMES_VERSION"],
    "candidate_sha": os.environ["POWERPACK_SHA"],
    "previous_sha": os.environ.get("POWERPACK_PREVIOUS_SHA") or None,
    "backup_ref": os.environ.get("POWERPACK_BACKUP_REF") or None,
    "install_dir": os.environ["POWERPACK_INSTALL_DIR"],
    "hermes_home": os.environ["POWERPACK_HERMES_HOME"],
    "data_action": "preserve",
    "database_action": "none",
    "telegram_chip_action": "preserve",
    "component_pins": component_pins,
}
path = Path(os.environ["POWERPACK_RECEIPT"])
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(path)
PY

trap - EXIT
printf 'receipt=%s\n' "$receipt"
printf 'result=PASS\n'
