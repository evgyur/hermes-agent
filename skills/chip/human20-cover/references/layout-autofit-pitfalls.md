# Human20 cover layout autofit pitfalls

Session lesson: the cover template can look correct at one text length and fail at another. Two areas need deterministic centering/fitting, not manual pixel constants.

## Top-right badge / pill

Problem: fixed `d.text((752, 78), badge, ...)` left the badge text visually off-center for shorter or different labels.

Fix pattern:
- define `badge_box = (720, 60, 1018, 118)`;
- measure `d.textbbox((0, 0), args.badge, font=badge_font)`;
- center horizontally inside the box;
- center vertically using bbox height and baseline offset;
- apply a small optical `+2px` vertical nudge after QA if the font sits high.

Do not reintroduce a hard-coded x/y for the badge text. The badge label changes across posts (`AI TOOLS`, `HERMES HOTFIX`, `OPENCLAW`, etc.), so centering must be dynamic.

## Black code field with 4 lines

Problem: the old code renderer started at fixed `y = 640` and stepped by 38/32px. A fourth line could sit too close to the bottom or escape/crop outside the black field.

Fix pattern:
- define `code_box = (88, 615, 992, 760)`;
- support up to 4 short lines;
- if `n <= 3`, keep larger sizes/step;
- if `n == 4`, reduce line step and font sizes, then vertically center the whole block inside `code_box`;
- shrink individual lines in a loop until `text_width <= max_text_w`, with a readable lower bound;
- run vision QA with an actual 4-line fixture before returning media.

## QA fixture

Use a fixture with four lines, because 3-line renders can hide the bug:

```bash
python3 scripts/render_human20_cover.py \
  --headline $'Агенту нужны\nглаза в интернет' \
  --badge 'AI TOOLS' \
  --subtitle 'поиск, чтение, браузер, извлечение, проверка' \
  --card-title 'Web stack for AI agents' \
  --chips 'Tavily|Exa|Firecrawl|Browserbase' \
  --code $'search → find relevant sources|neutral' \
  --code $'scrape → clean Markdown / JSON|success' \
  --code $'browser → click, login, verify|neutral' \
  --code $'evidence-log → URL + time + excerpt|muted' \
  --output /tmp/human20_cover_4line_qa.png
```

Vision QA must explicitly check:
- badge text centered horizontally and vertically;
- all four code lines fully inside the black box;
- no fourth-line crop/overflow;
- CTA exactly `Подписаться: @human20`.
