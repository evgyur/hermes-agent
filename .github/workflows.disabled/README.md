# Disabled GitHub Actions

This fork intentionally keeps GitHub Actions disabled.

Reason: development and verification for this repository are done locally, not on hosted GitHub runners.
Keeping the workflow files outside `.github/workflows/` prevents push, pull request, schedule, and publish jobs from starting automatically.

Local verification checklist:

```bash
scripts/run_tests.sh
uv tool run ruff check .
uv tool run ty check .
python scripts/check-windows-footguns.py --all
uv lock --check
nix flake check --print-build-logs
nix build --print-build-logs
```

Run the subset that matches the change. Re-enable CI only by moving selected files back into `.github/workflows/`.
