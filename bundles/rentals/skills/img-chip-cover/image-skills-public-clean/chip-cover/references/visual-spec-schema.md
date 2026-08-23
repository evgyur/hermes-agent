# Visual spec schema

Suggested cover spec:

```yaml
brand: brand-slug
mode: create | revise | qa | series | new-family | render-only
platform: telegram | x | linkedin | article | website | custom
canvas:
  width: 1080
  height: 1080
  aspect: square
content:
  source_summary: "..."
  thesis: "..."
  audience: "..."
visual:
  composition: "..."
  metaphor: "..."
  background_prompt: "no text, no letters, no logos, no watermark, ..."
  negative_prompt: "..."
overlay:
  headline: "..."
  badge: "..."
  cta: "..."
  logo: assets/<brand-slug>/logos/primary.svg
  safe_zones: []
qa:
  required_checks:
    - exact text
    - logo correctness
    - phone readability
    - safe margins
```
