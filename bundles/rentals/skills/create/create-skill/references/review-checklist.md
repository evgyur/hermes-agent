# create-skill manual review checklist

## Structure
- [ ] Scope: один скилл = одна задача
- [ ] SKILL.md короткий и директивный
- [ ] Heavy context вынесен в references/assets
- [ ] Нет deep nested references (всё линкуется напрямую из SKILL.md)

## Discovery quality
- [ ] Frontmatter содержит `name` и `description`
- [ ] `name` в kebab-case (lowercase + numbers + hyphen)
- [ ] `description` в третьем лице, с триггерами когда применять

## Reliability
- [ ] Output contract формализован и привязан к mode
- [ ] Есть quick test checklist
- [ ] До writing/editing пройден planning gate и есть `ready to write = yes`
- [ ] Выбран и назван benchmark mode (`old/new`, `with/without`, `review-only`)
- [ ] Есть iteration loop по умолчанию или явно зафиксированный waiver
- [ ] Для compare-only есть evidence even without rewrite loop
- [ ] Есть validation loop (check -> fix -> re-check)
- [ ] Для refactor есть legacy inventory + backward-compat map + migration notes
- [ ] Нет ломающих изменений без compat strategy
