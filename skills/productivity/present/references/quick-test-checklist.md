# Quick test checklist

- [ ] `--mode auto` выбирает `report` на тексте с ключом «отчёт».
- [ ] `--mode auto` выбирает `offer` на тексте с ключом «оффер».
- [ ] `--mode general` создаёт валидный standalone html (открывается локально).
- [ ] `--mode general` создаёт единый scroll-документ, а не fullscreen slide deck.
- [ ] В `general/report/offer/h20/auto` нет slide counter, fixed prev/next buttons и `overflow: hidden` на всём body как у deck-режима.
- [ ] markdown-таблица в input рендерится как `<table>` в режиме `report`.
- [ ] output-файл создаётся по пути `present_<slug>.html`.
- [ ] `@flow` рендерится в визуальную pipeline-схему, а не в обычный список.
- [ ] `@beforeafter` рендерится как до/после с центральной стрелкой.
- [ ] `@chart` рендерится как bar-chart с относительной шириной.
- [ ] `--mode slides` создаёт fullscreen-deck без общего page scroll.
- [ ] В `slides` режиме каждый `##`-раздел становится отдельным слайдом.
- [ ] В `slides` режиме работают переходы вперёд (`ЛКМ`, `→`, `Space`) и назад (`ПКМ`, `←`).
