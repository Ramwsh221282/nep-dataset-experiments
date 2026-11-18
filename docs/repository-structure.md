# Структура репозитория:

**Папка:** ./docs/
**Описание:** документация текущего проекта.

**Папка:** ./docs/nep-data-examples
**Описание:** документация про данные, структуру данных обучающего формата и примеры для задачи Next Edit Prediction.

**Файл:** ./docs/nep-data-examples/NEP_FILTERED_EXAMPLES_10.jsonl
**Описание:** примеры формата обучения и input (входных примеров) и output (выходных примеров) для обучения задачи Next Edit Prediction.

**Файл:** ./docs/nep-data-examples/input-format-description.md
**Описание:** структура выходного ответа (`output`), примеры правок и обязательные проверки. Несмотря на имя файла, он описывает выходной формат.

**Файл:** ./docs/nep-data-examples/output-format-description.md
**Описание:** структура входного промпта (`input`), контекст, история изменений, регионы редактирования и обязательные проверки. Несмотря на имя файла, он описывает входной формат.

**Файл:** ./docs/nep-data-examples/jsonl-description.md
**Описание:** пример JSONL-записи и соответствие полей исходных и корректирующих обучающих данных.

**Файл:** ./docs/nep-data-examples/tokens-separators.md
**Описание:** основные разделители и маркеры формата NEP и их назначение.

**Файл:** ./docs/repository-structure.md
**Описание:** документация про структуру файлов и папок текущего проекта.

**Папка:** ./generator/codefinetuner
**Описание:** форк проекта https://github.com/cuolm/codefinetuner для подготовки данных:  `src/codefinetuner/nep` для сырого NEP JSONL.

**Файл:** ./docs/nep-generator-design.md
**Описание:** dataset-only NEP-генератор данных для обучения.

**Файл:** ./docs/nep-generator-improvement-plan.md
**Описание:** план расширения генератора: синтаксически пошаговые траектории, зависимые rename/signature edits, несколько изменений, Rust/TypeScript semantic adapters, валидация и баланс примеров. Это план, не описание уже реализованных возможностей.
