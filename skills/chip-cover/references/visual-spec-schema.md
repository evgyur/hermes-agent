# Visual spec schema

## Input

```yaml
brand: human20 | hlru | <brand-slug> | auto
mode: create | revise | qa | series | new-family | render-only | handoff-tg
platform:
  kind: telegram | dzen | article | website | social | generic
  size: 1080x1080 | 16:9 | 4:5 | 9:16 | custom
source:
  post_text: "..."
  source_urls: []
  repo_urls: []
  screenshots: []
  existing_cover_path: null
content:
  headline: null
  subtitle: null
  badge: null
  cta: null
  footer: null
  proof_points: []
visual:
  thesis: null
  metaphor: null
  composition_archetype: auto
  background_strategy: generated | deterministic | existing | auto
constraints:
  must_include_logo: true
  exact_text_required: true
  no_fake_text: true
  no_placeholder_logo: true
```

## Output

```yaml
cover_path: /absolute/path.png
brand: human20 | hlru | <brand-slug>
qa: {verdict: ok | needs_fix | blocked, checks: {}}
tg_handoff: {TG_MEDIA_PATH_OVERRIDE: /absolute/path.png, ready_for_preview: true | false}
```
