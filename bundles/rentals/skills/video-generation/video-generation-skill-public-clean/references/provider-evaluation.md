# Provider evaluation

Classify every new video provider/model as one of:

- `use now` — API works, credentials are available, one smoke test passed, QA path exists.
- `watchlist` — promising, but not wired, not tested, too expensive, or not needed yet.
- `ignore` — weak fit, no API, bad pricing, low quality, unclear rights, or duplicative.

## Verify before `use now`

1. API authentication and endpoint shape.
2. Text-to-video support.
3. Image-to-video and reference-media support.
4. Duration, aspect-ratio, and resolution matrix.
5. Price per second/job and hidden post-processing fees.
6. Status polling and final asset URL fields.
7. Downloadability and URL expiry behavior.
8. Commercial rights and watermark policy.
9. One real smoke test output.
10. Local QA and delivery path.

## Common provider boundaries

| Boundary | Meaning |
|---|---|
| API available | Public/partner API exists and docs are clear. |
| CLI wired | The local environment has a working wrapper for submit/status/download. |
| Self-host only | Requires local or rented GPU infrastructure. |
| Research-only | Interesting model, but not production-ready for this workflow. |
| Not available | No accessible API or no acceptable legal/commercial route. |

## Pricing rule

Pricing changes. Check current provider docs before quoting. If unknown, say unknown.
