# Public / Private Split

В публичную версию можно включать:
- общую архитектуру cockpit
- общую структуру topics
- общие `/cas_*` workflows
- общий onboarding
- публичные upstream references
- типовые конфиг-риски вроде overwrite массивов

В публичную версию нельзя тащить:
- реальные chat IDs
- user IDs
- приватные repo URLs
- private workspace paths, если они деанонимизируют владельца
- live binding entries с персональными идентификаторами
- персональные system prompts и operator notes
- anything that leaks account topology or private operations

Правило простое:
если артефакт нельзя безопасно показать чужому человеку как reusable pattern, он должен остаться в private-версии.
