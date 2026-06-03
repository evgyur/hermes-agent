# TG handoff

`chip-cover` never publishes. It returns media and metadata to `tg`.

```yaml
cover_path: /absolute/path.png
TG_MEDIA_PATH_OVERRIDE: /absolute/path.png
tg_mode: preview | revision
qa_verdict: ok
ready_for_preview: true
```

`tg` owns caption, preview, revision, and publish gates. Publish only after explicit publish command.
