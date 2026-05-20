# /present modes

## auto (default)
- Выбирает шаблон по ключевым словам в title/input:
  - report: `отчёт`, `исследование`, `kpi`, `аналитика`, `таблица`
  - offer: `оффер`, `переговор`, `коммерческ`, `proposal`, `deal`
- Если не найдено совпадение → `general`.

## general
- Универсальный документ/презентация.
- Шаблон: `templates/present-default.html`.
- Формат: единый scroll-документ сверху вниз.
- Без slide-navigation, fixed prev/next controls, counters, fullscreen deck mechanics.

## slides
- Полноэкранная HTML-слайд-дека, один экран = один слайд.
- Шаблон: `templates/present-slides.html`.
- Управление:
  - ЛКМ, `→`, `Space`, `PageDown` → следующий слайд
  - ПКМ, `←`, `PageUp` → предыдущий слайд
- Скролл страницы выключен, колода фиксирована в viewport.
- Стартовый титульный слайд создаётся автоматически из `title/subtitle/badge`, дальше каждый `##`-раздел становится отдельным слайдом.

## Compatibility rule
- `slides` is opt-in only.
- `auto`, `general`, `report`, `offer`, and `h20` must remain document-style outputs unless the user explicitly wrote `slides`.

## report
- Отчёты, сравнительные обзоры, аналитика.
- Шаблон: `templates/present-report.html`.
- Поддержка markdown-таблиц (`| col | col |`).

## offer
- Офферы, переговорные планы, коммерческие предложения.
- Шаблон: `templates/present-offer.html`.
- Акцент на блоках-решениях/условиях/CTA.

## h20
- Альтернативный стиль на human20-design-system.
- Шаблон: `templates/present-h20.html`.
- Используй когда нужен более «мягкий» фирменный стиль Human 2.0.
