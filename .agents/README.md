# AI-скиллы и стандарты backend-разработки

`.agents` — рабочий пакет правил, промптов и шаблонов для команды. Он согласован с [System Design v1, a9277b9](https://github.com/larchanka-training/dmc-268-api-t3/blob/a9277b9da2b6b066dbe889a6d5fdd1b9436cf74f/SYSTEM_DESIGN.md). Документ пока имеет статус Draft; открытые решения и связь с ранее принятыми стандартами перечислены в [контексте проекта](rules/project-context.md).

## Применение

| Задача | Материал |
| --- | --- |
| Реализация backend | [reviewer-backend](skills/reviewer-backend/SKILL.md) |
| Тестирование | [reviewer-backend-tests](skills/reviewer-backend-tests/SKILL.md) |
| Ревью diff | [reviewer-diff-review](skills/reviewer-diff-review/SKILL.md) |
| Рабочий процесс | [workflows.md](workflows.md) |
| Стиль и архитектура | [development.md](rules/development.md), [architecture.md](rules/architecture.md) |
| Локальные шаблоны | [реализация](templates/implement.md), [тест-план](templates/test-plan.md), [PR](templates/pull-request.md) |

Выберите скилл в редакторе с поддержкой project skills или приложите SKILL.md и связанные материалы вручную. Передайте задачу, контракт и критерии приёмки. Для прямого вызова модели используйте системный промпт [кодирования](prompts/coding-system.md) или [тестирования](prompts/testing-system.md) вместе с заполненным шаблоном.

Правила покрывают FastAPI/SQLAlchemy/PostgreSQL, uv/Ruff/Pylint, RabbitMQ, VcsReader/VcsPublisher, Context Builder, LLM Gateway, проверку findings, историю и один обновляемый summary-комментарий. Разделены техническая доставка webhook, бизнес-дедупликация автоматического ревью и ручной rerun. Redis исключён из v1; выбор модели и численные лимиты остаются решениями команды.

## Контракт AI-ревью 2.0.0

Используйте [system prompt](prompts/review-system.md), [входной шаблон](prompts/review-user.md), [сигнатуры](prompts/signatures.md), [схему кандидатов](schemas/review.schema.json) и [схему доверенного контекста](schemas/review-context.schema.json).

Каждый кандидат связан с rule_id из неизменяемого RuleSet, проверенной позицией исходника и изменёнными строками. Валидатор проверяет версии, SHA, правила, severity, полноту анализа, позиции и точные дубликаты. Режим фильтрации сохраняет корректные кандидаты, возвращает безопасные причины отклонения и отмечает частичный результат.

**Версия 2.0.0 несовместима с 1.0.0.** [Правила контракта и миграции](rules/review-contract.md) разделяют кандидата модели, запись Finding и DTO API. Идентификаторы базы, lifecycle и связи истории назначает приложение. Полнота анализа, жизненный цикл ReviewRun и состояние публикации независимы. Неполный анализ не доказывает FIXED.

[Инструкции локальной проверки](validation/README.md) содержат команды строгой валидации и фильтрации. Проверки формата не заменяют оценку качества модели и интеграционные тесты сервисов.

## Поддержка пакета

Общие файлы синхронизируются с frontend: пять файлов prompts, обе схемы и правила project-context/review-contract. Изменяйте их совместно с потребителями контракта. Markdown пишется на английском, кроме README.md на русском. Корневой AGENTS.md ведёт техлид; приложение и инфраструктуру реализуют владельцы соответствующих задач.
