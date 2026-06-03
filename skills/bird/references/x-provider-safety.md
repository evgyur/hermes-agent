# X provider and credential safety

Use runtime credentials only. Do not bundle or print API keys, bearer tokens, cookies, `ct0`, `auth_token`, local secret paths, or account exports.

If no approved credential is available, stop with a clear blocker instead of trying blocked HTML scraping.

When cookie-authenticated access is allowed, run requests slowly, never in parallel, and report only content and safe metadata.