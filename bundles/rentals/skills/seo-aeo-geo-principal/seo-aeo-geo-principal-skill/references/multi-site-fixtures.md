# Multi-site fixture benchmark

Synthetic fixtures prevent overfitting to one website.

| Fixture | Failure class | Expected priority |
|---|---|---|
| `clean-site.json` | mostly healthy site with unmeasured performance | performance residual gap |
| `thin-content-site.json` | extractable text too shallow | content depth |
| `schema-missing-site.json` | missing JSON-LD/entity graph | structured data coverage |
| `discovery-weak-site.json` | missing `llms.txt` / `.well-known` surfaces | GEO / agent discovery |
| `image-alt-debt-site.json` | meaningful images without alt | image SEO/accessibility |
