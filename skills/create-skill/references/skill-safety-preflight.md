# Skill safety preflight

Use when a new, external, community-submitted, or agent-created skill may be installed, published, or merged.

## Goal

Answer: **is this skill safe enough to install or publish?**

Do not treat this as a pure prose review. A useful gate combines deterministic scanners, script inspection, dependency checks, and optional semantic review.

## Recommended layers

1. **Hermes native guard**
   - Use `tools/skills_guard.py` / hub quarantine model when available.
   - Community skills: any finding should usually block unless owner explicitly forces install.
   - Trusted skills: caution can pass; dangerous should block.
   - Agent-created skills: dangerous should force rewrite or manual review.

2. **NVIDIA SkillSpector**
   - External scanner for AI agent skills.
   - Scans local dirs, single `SKILL.md`, Git repos, and zip files.
   - Can output terminal, JSON, Markdown, SARIF.
   - Useful as a GitHub Action gate for community skill submissions.

3. **Static content checks**
   - Prompt injection: ignore previous instructions, hidden comments, role hijack, system prompt leakage.
   - Exfiltration: `.env`, SSH/AWS/Kube/Docker creds, env dumps, context leakage, suspicious outbound URLs.
   - Destructive/persistence: `rm -rf`, chmod 777, sudo, cron/systemd/backdoors, self-modification.
   - Network/script side effects: `curl | bash`, remote code fetch, subprocess shell injection, unbounded crawling.
   - Obfuscation: base64/hex eval, packed scripts, minified blobs without source.

4. **Supply chain checks**
   - Pin dependencies.
   - Reject mutable Git refs for runtime deps unless explicitly justified.
   - Run OSV/CVE lookup when manifests exist.
   - Watch for typosquatting and unexpected package managers.

5. **Permission contract**
   - Skill should declare expected toolsets and external systems.
   - Network/file/terminal/browser needs must match the skill purpose.
   - Broad autonomous permissions without scope are a caution/danger signal.

6. **Optional LLM semantic review**
   - Use only after deterministic checks.
   - Ask for intent-level risks: hidden objectives, social engineering, covert persistence, or scope mismatch.
   - LLM review is additive, not authoritative.

## Verdicts

- `safe` — no material findings; install/merge can proceed.
- `caution` — legitimate but risky surfaces; needs human/manual review or allowlist.
- `dangerous` — block install/merge unless owner explicitly forces after seeing evidence.

## CI pattern

For skill repos or marketplaces:

```text
PR changes SKILL.md / references / scripts / templates
→ run Hermes skills_guard
→ run SkillSpector, preferably SARIF/JSON
→ run frontmatter/structure validation
→ run secret scan
→ dangerous = fail
→ caution = label/manual review
→ safe = pass
```

## Reporting shape

```text
skill-safety: caution
┈ scanner: Hermes skills_guard + SkillSpector
┈ blocking: none
┈ review: script reads env var names, but only documented API key; allowlist if expected
┈ next: require declared permission contract before merge
```
