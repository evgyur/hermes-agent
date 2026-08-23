# Templates

Use these as compact artifacts. Replace placeholders with target-repo facts. Do not invent values.

## SOURCE_OF_TRUTH.md

```md
# Source Of Truth

live_frontend:
live_backend_or_api:
canonical_publish_checkout:
deployable_ref:
production_host_or_platform:
runtime_current_path_or_release:
runtime_env_source:
release_fingerprint_route:
official_deploy_command:
official_rollback_command:
retired_paths_or_scripts:
proof_collected:
unknowns:
```

## ENV_CONTRACT.md

```md
# Env Contract

committed_examples:
local_generated_env:
production_secret_store:
public_env_prefixes:
server_only_keys:
rotation_owner:
redaction_policy:
must_not_commit:
verification:
```

## SUPABASE_LOCAL_RUNBOOK.md

```md
# Local Supabase Runbook

required_tools:
config_file:
migrations_path:
seed_path:
auth_redirect_urls:
storage_buckets:
start_command:
reset_command:
env_generation_command:
local_smoke:
known_gaps:
```

## AUTH_JOURNEY_MATRIX.md

```md
# Auth Journey Matrix

| Journey | Entry condition | Expected next step | Final state | Verification |
| --- | --- | --- | --- | --- |
| guest protected page | | | | |
| new signup | | | | |
| existing sign-in | | | | |
| callback | | | | |
| reset password | | | | |
| invite/join/payment continuation | | | | |
| unlinked identity | | | | |
| inactive entitlement | | | | |
| already authenticated | | | | |
| stale cookie | | | | |
```

## MEDIA_ACCESS_CONTRACT.md

```md
# Media Access Contract

media_classes:
protected_storage_root:
public_storage_root:
token_payload:
token_ttl:
manifest_route:
segment_route:
key_route:
filename_validation:
cache_policy:
missing_env_behavior:
tests:
operator_logs:
```

## DEPLOYMENT.md

```md
# Deployment

canonical_publish_checkout:
deploy_ref:
preflight:
build:
release_id:
release_metadata:
env_source:
live_switch:
service_restart_or_promotion:
health_checks:
smoke_checks:
handoff_required:
```

## RELEASE_HANDOFF.md

```md
# Release Handoff

what_changed:
source_of_truth:
git_sha:
release_id:
deploy_command:
checks_run:
release_fingerprint:
critical_journeys_checked:
unchecked_paths:
residual_risk:
rollback_path:
```

## ROLLBACK_PLAN.md

```md
# Rollback Plan

current_release:
current_sha:
last_known_good_release:
rollback_command:
data_safety_check:
writes_after_bad_release:
verification_after_rollback:
incident_report_owner:
stop_conditions:
```

## INCIDENT_REPORT.md

```md
# Incident Report

summary:
started_at:
detected_by:
affected_surface:
current_release:
suspected_release:
root_cause:
mitigation:
rollback_or_forward_fix:
data_impact:
users_affected:
verification:
follow_up_actions:
```
