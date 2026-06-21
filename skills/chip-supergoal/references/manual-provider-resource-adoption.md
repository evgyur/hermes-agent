# Manual provider resource adoption in production SuperGoals

Use this when a SuperGoal planned to create a paid/provider resource, but Chip creates it manually in the provider UI after the automated create path is blocked (for example provider capacity `409 no_free_node`).

## Rule

Do not keep retrying creation and do not create a second resource. Rewrite or patch the plan so the next phase **adopts and verifies** the manually created resource.

## Required plan mutation

1. Supersede the create-resource phase with an adopt-resource phase.
2. Phase 0/next phase must use read-only provider API/panel evidence to identify the manual resource by name/id.
3. Verify exact approved facts before any further mutation:
   - resource id and name
   - project/account id
   - region/zone
   - image/OS
   - CPU/RAM/disk/network or equivalent shape
   - preset/configurator id if available
   - monthly base price and attached service price
   - temporary/public IPs
   - duplicate count: exactly one candidate
   - old production resource/IP/DNS still unchanged
4. Only after adoption passes may the executor continue to SSH/hardening/storage/migration phases.
5. If the manual resource differs from the approved spec or price, fail closed and ask Chip; do not silently accept a “close enough” tariff.

## Provider secret pitfall

Provider detail APIs may return generated root passwords or other sensitive bootstrap secrets. Never persist those values into reports, STATE, transcripts, or artifacts. Record only that a generated password exists and add/verify a hardening gate:

- prove key-based SSH works;
- disable password/root-password SSH access where appropriate;
- or record a temporary exception with a clear reason and follow-up.

## Evidence shape

The adoption report should include:

```text
manual_resource_found: yes
id: <id>
name: <name>
status: <on/provisioning>
spec: <verified shape>
billing: <base + attached services + total>
public_network: <temporary/public IPs>
duplicates: <count>
old_production_attachment: unchanged
public_health_old_prod: green
provider_mutation_boundary: GET-only
secrets: not persisted
```

## Anti-patterns

- Treating a screenshot alone as proof after the resource exists.
- Retrying POST/create after Chip has manually created the resource.
- Continuing with a weaker/more expensive shape without explicit approval.
- Reporting a provider API response verbatim when it includes `root_pass`, token, password, access key, or secret fields.
