# Human20 Powerpack Gen2

Canonical runtime package for Human20 capabilities on Hermes Agent.

## Authority

- Runtime SSOT: `human20team/hermes-agent-powerpack`
- Package path: `packages/powerpack-gen2`
- Donor provenance: `human20team/human20-rentals-autopilot-powerpack` at `dbe696ec217576a0adef5fcb4e40370de253937a`
- Product and skill content remains owned by `human20team/human20bot`.

## Coexistence modes

- `disabled`: registers no runtime surface.
- `compatibility`: registers only namespaced status/doctor surfaces, the evidence policy hook, and package skills. It does not own effectful tools or providers.
- `gen2_only`: registers the complete Gen2 tool/provider surface and fails closed on any rejected registration.

The default is `disabled`. Activation is explicit and transaction-owned in production.

Variants are intentionally separate from credentials. `rentals` provides the
common memory/provider surface, `employee` additionally exposes the isolated
employee Telegram bridge, and `owner` selects every Powerpack surface while
allowing the host to bind its separately managed full-memory and account
credentials. The package never copies or persists secrets itself.

## Boxed install over an existing Hermes profile

Run this with the same managed Python and `HERMES_HOME` used by the target
gateway:

```bash
PYTHONPATH=/path/to/packages/powerpack-gen2 python -m powerpack_gen2.installer \
  --source-root /path/to/packages/powerpack-gen2 \
  --hermes-home "$HERMES_HOME" \
  --variant owner --mode gen2_only
```

The installer verifies the complete package inventory before changing the
profile, atomically materializes the standalone plugin, selects its tool and
provider owners, and writes a non-secret receipt. A failed stage restores the
previous plugin and config. Restart the gateway only after the installer exits
successfully.

## Validation

```bash
hermes powerpack-gen2 doctor --ci --upstream-sha <exact-upstream-sha>
```

Host certification additionally checks repository cleanliness, upstream/core drift, venv ownership, service/process identity, active package hash, credential presence, and managed runtimes without printing secret values.

The deterministic inventory at `metadata/powerpack-gen2-files.json` covers every package file except the inventory itself and generated cache files.
