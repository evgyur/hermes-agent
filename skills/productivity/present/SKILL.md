---
name: present
description: Generates polished HTML presentations/documents for chat using template routing (general/report/offer) and sends them as .html attachments.
metadata:
  hermes:
    tags: [presentation, slides, html, reports, documents]
    related_skills: [powerpoint, google-workspace]
  slash:
    command: /present
    emoji: 📄
---

# /present

Универсальный рендерер для **любого объёма информации** в читаемый HTML: отчёты, офферы, планы, исследования, переговорные брифы, longread-конспекты.

`present` can use an optional deterministic cleanup pass before rendering. If the optional cleanup helper is absent, the bundled generator falls back to a small built-in text cleanup so the skill works out of the box.

## Язык и стиль для русского текста

- Используй союзы, чтобы текст был связным.
- Пиши лаконично, но не телеграфно.
- Избегай англицизмов, если есть нормальный русский вариант.

## Trigger
- `/present <topic/text>`
- `/present slides <topic/text>`
- `/present report <topic/text>`
- `/present offer <topic/text>`
- `/present h20 <topic/text>`
- `/present help`

## Mode split (critical)

- Plain `/present ...` is a **single scrollable HTML document** with sections stacked top-to-bottom.
- `/present slides ...` is the **separate fullscreen deck mode** with slide navigation.
- Do not add slide navigation, slide counters, fixed next/prev buttons, or one-screen-per-section behavior to plain `/present`.
- `report`, `offer`, `h20`, `general`, and `auto` must stay in document mode unless the user explicitly asked for `slides`.

## Output Contract (mandatory)
1. Сгенерировать standalone `.html`
2. Выполнить visual-pass (DoD):
   - добавить **минимум 1 архитектурную схему** (pipeline / flow / блок-схема)
   - добавить **минимум 1 сравнительный визуальный блок** (таблица / compare-cards / before-after)
3. Для `/present slides` дополнительно экспортировать `.png`-версию деки и по умолчанию отправлять пользователю `PNG`, но не вместо `.html`
4. Сохранить артефакты в workspace как `present_<slug>.html` и `present_<slug>.png`
5. Держать итоговые файлы в текущем workspace или во временной директории, если пользователь не задал путь явно.
6. Всегда отправлять в текущий чат `.html` как вложение, даже если уже отправлен `PNG`
7. Для `/present slides` по умолчанию отправлять оба файла: сначала `PNG` для быстрого просмотра, затем `.html` в этот же чат
8. Подтвердить доставку (edit/reaction fallback)

## HTML Purity Rule (TABOO)
- В финальном `.html` запрещён markdown-синтаксис как формат вывода.
- Нельзя оставлять `#`, `##`, `- `, `* `, fenced code blocks, markdown links/таблицы как сырой текст структуры.
- Любая структура должна быть переведена в валидный HTML (`<h1..h6>`, `<p>`, `<ul>/<ol>`, `<table>`, `<a>`, и т.д.).
- Перед отправкой файла обязательно визуально/текстово проверить, что markdown не "протёк" в тело документа.

## Routing
- `slides` → `templates/present-slides.html` (полноэкранная слайд-дека, один экран = один слайд, навигация кликом/клавишами)
- `report` → `templates/present-report.html`

Delivery rule for this skill:
- HTML-файл не является опциональным артефактом доставки
- если режим `slides`, PNG удобен для превью, но HTML все равно обязательно отправляется в тот же чат
- `offer` / `negotiation` → `templates/present-offer.html`
- `h20` → `templates/present-h20.html` (Human 2.0 style template)
- default / auto → `templates/present-default.html`

Backward-compat invariant:
- обычный `/present` должен выглядеть как прежний длинный визуальный документ, а не как pseudo-slides страница.

## Slides mode authoring rules (mandatory for `/present slides`)

`/present slides` это не документ с прокруткой, а сценическая дека. Пиши так, чтобы человек мог показывать экран и переключать слайды по одному.

### Core format
- Первый экран должен работать как обложка: сильный заголовок, короткий подзаголовок, без перегруза.
- Каждый следующий `##`-раздел = один слайд = одна главная мысль.
- Не пытайся уместить весь документ на один экран. Лучше 8-14 ясных слайдов, чем 4 перегруженных.

### Writing style for slides
- Заголовок слайда должен не называть тему вообще, а продвигать мысль. Не `Экономика`, а `Партнёрский пул фиксирован на 45%`.
- На одном слайде держи 1 тезис, 3-5 опорных пунктов или 1 визуальную структуру.
- Абзацы делай короткими. Если абзац длиннее 2-3 строк на экране, это уже подозрительно.
- Избегай канцелярита и пересказа очевидного. Слайд должен читаться быстро.
- Если можно заменить текст структурой, замени текст структурой.

### Preferred slide shapes
- Титул / promise
- Problem / why now
- How it works / flow
- Roles / system map
- Economics / metrics
- Safeguards / constraints
- Roadmap / next steps
- Closing / status / CTA

### Visual bias
Для `/present slides` визуальные блоки особенно желательны.

По умолчанию предпочитай:
- `@flow` для процесса, воронки, deep-link пути, архитектуры
- `@compare` или `@beforeafter` для выбора, old vs new, manual vs automated
- `@chart` для процентов, KPI, ranking, unit economics
- `@timeline` или `@phase` для rollout и последовательности
- таблицу только если сравнение реально табличное и иначе хуже читается

### Density guardrails
- На слайде не должно быть ощущения «простыня текста».
- Если контента слишком много, дроби на два соседних слайда.
- Не ставь больше одной тяжёлой таблицы на слайд.
- Если список длиннее 5 пунктов, перегруппируй его в карточки, фазы или отдельные слайды.

### Speaker-friendly rule
- Слайд должен поддерживать устное объяснение, а не заменять его полностью.
- Оставляй воздух: зритель должен считывать структуру за 3-5 секунд.
- Хороший слайд выглядит как опорный экран для речи, а не как статья.

## Generation command
```bash
python3 /path/to/present/scripts/generate_present.py \
  --mode auto \
  --title "Заголовок" \
  --subtitle "Подзаголовок" \
  --badge "" \
  --input /path/to/content.md \
  --output ./present_output.html
```

### PNG export for slides
```bash
python3 /path/to/present/scripts/render_slides_png.py \
  --input ./present_output.html \
  --output ./present_output.png \
  --title "Название деки"
```

> Badge rule: если `--badge` не задан (или равен `/present`), генератор должен ставить не повтор заголовка, а короткую тему документа: например `Enterprise AI`, `Architecture`, `Strategy`, `Offer`, `Report`, `Workflow`.

## Visual Engine (mandatory when useful)
Когда материал выигрывает от визуализации, /present должен использовать visual-блоки активно, а не ограничиваться обычными карточками.

Используй по ситуации:
- `@flow title::desc::emoji` — pipeline / process / architecture / user flow
- `@compare leftTitle::leftBody::rightTitle::rightBody` — side-by-side compare
- `@beforeafter beforeTitle::beforeBody::afterTitle::afterBody` — transformation / migration / redesign
- `@chart label::value::note` — компактный bar chart для метрик / ranking / scoring
- `@timeline label::body::emoji` — chronological sequence
- `@phase title::desc::emoji` — staged rollout / roadmap

## Visual-pass heuristics
Если в материале есть одно из ниже — visual block обязателен:
- архитектура, pipeline, flow, воронка, процесс → `@flow`
- сравнение вариантов, old vs new, option A vs B → `@compare` или `@beforeafter`
- числа, ранжирование, KPI, score → `@chart`
- этапы, rollout, roadmap → `@phase` или `@timeline`

Не делай визуал ради визуала. Но если структура явно просит схему — схема должна быть.

## References
- [Modes](references/modes.md)
- [Slides authoring](references/slides-authoring.md)
- [Quick test checklist](references/quick-test-checklist.md)
- [Manual review checklist](references/manual-review-checklist.md)
- [Backward-compat map](references/backward-compat-map.md)
