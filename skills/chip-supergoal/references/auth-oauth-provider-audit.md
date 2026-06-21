# Auth/OAuth production verification pitfall

Use this when a SuperGoal touches OAuth/login buttons or provider configuration.

## Lesson

A rendered login button is only frontend evidence. OAuth readiness requires backend-provider evidence. Final audit should not mark provider login as complete from HTML alone.

## Required probes

- Parse public runtime env from the login page and record the enabled flags/provider IDs.
- For Supabase built-in providers, query `/auth/v1/settings` with the publishable/anon key and confirm the provider is enabled server-side.
- For custom provider IDs, hit the exact authorize path or app-owned bridge route. Treat `provider is not enabled` / `Provider <id> could not be found` as an audit gap.
- If the provider is intentionally unavailable, require fail-closed UI: hide the button and leave a non-secret diagnostic in the report/state.

## Human20/Yandex example

Yandex did not work as plain `custom:yandex` on self-hosted Supabase. The robust pattern was an app-owned bridge:

- `/auth/yandex/start` redirects to `https://oauth.yandex.ru/authorize` with CSRF state and safe next cookies.
- `/auth/yandex/callback` validates state, exchanges code at `https://oauth.yandex.ru/token`, fetches profile from `https://login.yandex.ru/info?format=json`, creates/verifies Supabase auth server-side, and redirects to a safe local path.
- `/auth/yandex/userinfo` requires bearer and returns `401` without it.

## Completion language

External provider login is `trust-prior` until a human completes a real browser OAuth roundtrip with the provider account. Do not print `AUDIT_COMPLETE` for end-to-end OAuth success unless that proof exists or the acceptance criteria explicitly scope it out.