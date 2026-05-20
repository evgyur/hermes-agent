# Backward-compat map (/present refactor)

## Preserved
- Старый вызов `/present <text>` продолжает работать.
- По умолчанию используется `auto`, который при отсутствии ключей даёт прежний «универсальный» результат.
- Прежний «универсальный» результат означает единый HTML-документ с вертикальным скроллом, а не слайд-деку.
- Навигационные кнопки, slide counter, fullscreen deck и one-screen-per-section допустимы только в явном режиме `/present slides`.

## Added (non-breaking)
- `/present report <text>`
- `/present offer <text>`
- Генератор `--mode auto|general|report|offer`

## Migration notes
- Старые интеграции можно не менять: `/present <topic>` остаётся валидным.
- Для более точного дизайна рекомендуется явно задавать mode.
- Если пользователь просто пишет `/present`, нельзя молча переводить его в `slides` даже если материал хорошо раскладывается на слайды.
