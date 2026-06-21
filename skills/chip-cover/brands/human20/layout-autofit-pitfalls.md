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

## Headline → card vertical spacing

Problem: a strong 3-line headline can visually collide with the white product/GitHub card below it. This happened with `Jailbreak / для open-source / моделей`: even when fixed coordinates were technically close, the third line looked like it touched the card.

Fix pattern:
- compute the headline/subtitle bounding box after drawing text;
- set `card_top = max(default_card_top, subtitle_bottom + safe_gap)`;
- move every card child using `card_top + offset`, never fixed absolute y-values;
- keep the headline large and expressive; move the card first, shrink the headline only if CTA/footer would break.

Pass condition: a clear dark-background gap between the last headline line and the white card; no touching, overlap, or visual crowding.

## QA fixture

Use fixtures with four code lines and a 3-line mixed Latin/Russian headline, because simpler renders can hide bugs:

```bash
python3 scripts/render_human20_cover.py \
  --headline $'Jailbreak\nдля open-source\nмоделей' \
  --badge 'AI SAFETY' \
  --subtitle '' \
  --card-title 'GitHub: p-e-w/heretic' \
  --chips '22.7k stars|AGPL-3.0|Gemma 3 12B' \
  --code $'97/100 refusals → 3/100|success' \
  --code $'KL divergence: 0.16|neutral' \
  --code $'Optuna tunes ablation params|muted' \
  --code $'open-weight safety = weights|error' \
  --output /tmp/human20_cover_spacing_qa.png

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
- headline/card vertical gap is clean for `Jailbreak / для open-source / моделей`;
- badge text centered horizontally and vertically;
- all four code lines fully inside the black box;
- no fourth-line crop/overflow;
- default CTA `Подписаться: @human20` unless the user overrides it.
