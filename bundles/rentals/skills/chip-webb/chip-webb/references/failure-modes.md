# Failure Modes

Use this before implementation and before completion. Staff-level work asks how the system fails, then adds proof that the failure is blocked or intentionally accepted.

## Source Of Truth Failures

| Failure | Symptom | Prevention | Proof |
| --- | --- | --- | --- |
| Wrong checkout deployed | live UI differs from repo | canonical publish path and exact SHA gate | release fingerprint matches selected SHA |
| Legacy app edited | changes never appear live | source-of-truth map names live app directory | changed file is under live app path |
| Manual live hotfix | repo loses production truth | live dirs treated as runtime only | diff exists in git, not only runtime |
| Retired deploy script used | deploy targets wrong host/path | retired scripts listed and blocked | command history/handoff uses official path |

## Supabase And Data Failures

| Failure | Symptom | Prevention | Proof |
| --- | --- | --- | --- |
| Wrong database migrated | app still missing tables/functions | runtime DB authority check | runtime connection and API/PostgREST see change |
| Backup treated as primary | stale data appears live | data authority contract | one live write DB identified |
| RLS too open | user can read another user's data | RLS/API tests with two users | forbidden cross-user read returns 403/empty |
| RLS too closed | valid user loses access | entitlement fixtures | active user route/API succeeds |
| Local state used as truth | progress/access differs per browser | server-backed authoritative state | state survives browser/device change |

## Auth And Account Failures

| Failure | Symptom | Prevention | Proof |
| --- | --- | --- | --- |
| UI gate only | API still exposes data | server/API access checks | direct API request fails without access |
| Callback works, reset broken | support load after launch | journey matrix | reset-password journey passes separately |
| `next` lost or unsafe | users land wrong or open redirect risk | safe local path normalization | malicious external `next` rejected |
| Stale cookie crashes render | white screen/server error | stale session fallback | stale-cookie test returns guest/recovery |
| Generic `hasAccess` hides scope bugs | wrong product unlocked | scoped entitlements | each product/community/workshop fixture tested |

## Media Failures

| Failure | Symptom | Prevention | Proof |
| --- | --- | --- | --- |
| Paid video in public path | URL can be shared forever | protected media contract | protected files absent from public static dirs |
| Token not content-bound | token for one video opens another | token payload includes content id | wrong-content token test fails |
| Path traversal | segment route reads arbitrary file | filename and root validation | traversal fixture returns 400/403 |
| Keys cached publicly | revoked access keeps working | cache policy and short TTL | key route returns no-store/private policy |
| Missing signing env opens content | fail-open media route | fail-closed env check | missing key test returns 500/403 without content |

## Deploy And Rollback Failures

| Failure | Symptom | Prevention | Proof |
| --- | --- | --- | --- |
| Dirty deploy | unreviewed changes go live | clean-tree gate | deploy log shows clean tree/exact SHA |
| Build after live switch | broken release becomes current | staged build before switch | live pointer changes after build success |
| Release metadata absent | cannot tell what is live | release contract | `/release.json` or platform equivalent exists |
| Rollback after writes corrupts state | users lose/duplicate data | rollback data-safety gate | write-after-release audit reviewed |
| Smoke too narrow | deploy "green" but auth broken | risk-based smoke matrix | changed critical journey evidence exists |

## Env And Secret Failures

| Failure | Symptom | Prevention | Proof |
| --- | --- | --- | --- |
| Real value in `.env.example` | secret leak | placeholder-only examples | secret scan no matches |
| Service key in client bundle | privilege escalation | server-only env boundary | bundle/static scan no key names/values |
| Script prints secrets | chat/log leak | redacted logging | command output reviewed |
| Project-specific data in public skill | skill leaks private ops | public-safety scan | banned-term scan no matches |

## Completion Rule

For every in-scope failure mode, classify it:

- `blocked_by_contract`
- `verified`
- `accepted_with_reason`
- `not_applicable`
- `unverified_risk`

Do not claim production-ready while any P0/P1 failure mode is `unverified_risk`.
