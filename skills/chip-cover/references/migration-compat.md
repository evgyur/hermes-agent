# Migration compatibility

Legacy mapping:

```text
chip-img -> img
chip/human20-cover -> chip-cover brand=human20
/tg human20 media -> chip-cover human20 + tg preview
/tg hlru media -> chip-cover hlru + tg preview
```

Keep aliases during migration. Do not delete protected assets, renderers, golden examples, visual contracts, manifests, or source references.
