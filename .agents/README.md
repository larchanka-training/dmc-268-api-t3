# Инструменты AI-разработки команды 3

Этот набор помогает писать и проверять код проекта. Корневой `AGENTS.md` ведёт техлид; этот пакет его не создаёт и не заменяет.

## Как применять

Скиллы находятся в `skills/<name>/SKILL.md`. В агенте с поддержкой project skills выберите нужный скилл; в других инструментах явно приложите его файл и указанные в нём references. Наличие папки само по себе не гарантирует загрузку инструкций любым редактором.

| Работа | Что загрузить |
| --- | --- |
| Изменение backend | [reviewer-backend](skills/reviewer-backend/SKILL.md) |
| Анализ diff / ошибочных сигнатур | [reviewer-diff-review](skills/reviewer-diff-review/SKILL.md) |
| Регрессионные и контрактные тесты | [reviewer-backend-tests](skills/reviewer-backend-tests/SKILL.md) |
| Frontend | `.agents` в `larchanka-training/dmc-268-ui-t3` |

Пример запроса: «Используй `.agents/skills/reviewer-backend/SKILL.md`. Реализуй issue #N по её критериям приёмки, покажи проверки и ограничения». Для ревью: «Используй reviewer-diff-review для base/head SHA; код не изменяй».

Общие правила: [разработка](rules/development.md), [архитектура](rules/architecture.md). Локальные шаблоны: [написание кода](templates/implement.md), [тестирование](templates/test-plan.md), [отправка на ревью](templates/pull-request.md). Проверки самого пакета: [validation](validation/README.md).

Учебный маршрут: [практикум и самопроверка](learning.md).

## Статус исходной базы

Проверено 2026-09-10: backend main `34fa08b` содержит `main.py`, `/health`, `pyproject.toml` с FastAPI/Uvicorn. PR #1 (`worktree-backend-skeleton`) предлагает `app/`, `/healthcheck`, uv, SQLAlchemy, PostgreSQL, Ollama, Ruff, Pylint и pytest. PR ещё не принят; его пути и команды нельзя считать доступными в main. Этот набор не переносит изменения из PR #1.

Frontend main содержит React 18, TypeScript strict и Vite; ESLint, Vitest, Zod, Zustand и pnpm lockfile пока отсутствуют. Принятый целевой стек описан в правилах, но установка инструментов остаётся отдельной реализационной задачей.

Открытые решения: конкретные маршруты запуска/rerun и DTO, JWT library/claims policy, брокерный клиент, механизм rate limit, Redis, подробная топология sub-network. Шаблоны обозначают проектируемые контракты и не выдают их за работающие API.

## Версии и источники

Версия пакета и промптов: `1.0.0`. Основания: задание спринта, текущие репозитории команды и локальный документ требований «GitHub Code Review Requirements RU», версия 1.0 от 2026-09-08 (FR-03, FR-05, FR-11–17, NFR-03). Документ использован как контекст, численные NFR остаются предложениями.

Справка по инструментам: [skills](https://learn.chatgpt.com/docs/build-skills), [uv projects](https://docs.astral.sh/uv/guides/projects/), [Vitest](https://vitest.dev/guide/), [Skylos](https://github.com/duriantaco/skylos). Версии и команды всегда сверять с файлами рабочей ветки.
