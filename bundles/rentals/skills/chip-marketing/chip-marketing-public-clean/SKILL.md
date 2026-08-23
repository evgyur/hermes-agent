---
name: marketing-agent-toolkit
description: "Public-clean marketing skill: growth tactics, marketing ideas, and psychology models for agent-assisted marketing work. Router-only: delegates to references/tactics, references/ideas, or references/psychology."
---

# marketing-agent-toolkit — Marketing Router

A public-clean marketing toolkit for agent-assisted strategy and execution. It combines:

- executable growth tactics;
- marketing idea libraries by channel/category;
- buyer psychology, persuasion, pricing, and growth models.

## Router Contract

1. Pick exactly one domain for the request:
   - `references/tactics/` — executable growth tactics;
   - `references/ideas/` — strategy and channel ideas;
   - `references/psychology/` — buyer psychology, persuasion, pricing, and growth models.
2. State why that domain fits in one line.
3. Define the output contract before implementation:
   - expected artifact;
   - output location;
   - success verification.
4. Load on demand only. Do not preload unrelated references.

## Quick Dispatch

| Need | Go To |
|---|---|
| Launch product | `references/tactics/launch.md` |
| Outreach & partnerships | `references/tactics/outreach.md` |
| Monitor competitors | `references/tactics/monitoring.md` |
| Content & growth | `references/tactics/content.md` |
| SEO ideas | `references/ideas/content-seo.md` |
| Pricing psychology | `references/psychology/pricing.md` |
| Persuasion techniques | `references/psychology/persuasion.md` |
| Hook writing | `references/hook-frameworks/SKILL.md` |
| Product Hunt launch | `references/playbooks/SKILL.md` |

## Output Contract

- Primary artifact: implementation plan, strategy, copy, campaign brief, or psychological insight.
- Output location: specified by the user or local workspace.
- Success verification: explicit checklist tied to the artifact.

## Done Criteria

- [ ] Request mapped to exactly one domain.
- [ ] Selected reference file loaded before producing the artifact.
- [ ] Artifact, output location, and verification criteria are named.
- [ ] No unrelated references were loaded just in case.
