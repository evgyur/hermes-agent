# Quick test checklist

- [ ] Чат под Codex создан
- [ ] Topics включены
- [ ] Бот находится в чате как admin
- [ ] Отдельный agent существует в config
- [ ] Binding указывает чат -> отдельный agent
- [ ] `/cas_resume` отвечает внутри `Codex Control`
- [ ] `/cas_status` отвечает внутри `Codex Control`
- [ ] Inline-кнопки работают в topics, не только в DM
- [ ] Длинный run даёт heartbeat примерно через 150 секунд
- [ ] Если run всё ещё молчит на следующем окне, появляется stall notice и auto-nudge
- [ ] Финал, blocker и вопрос к пользователю сразу relay-ятся в topic
- [ ] Онбординг соответствует реальному UX
