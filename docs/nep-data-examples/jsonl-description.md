# Точная JSONL-запись для основного примера

Ниже один JSON-объект на одной физической строке. `\n` — JSON-экранирование переводов строк, а не два буквальных символа в декодированном промпте.

```json
{
  "id": "example-discount",
  "input": "<|fim_suffix|>print(result)\n<|fim_prefix|><|file_sep|>pricing.py\ndef calculate(price, tax, discount):\n    return price * (1 + tax) - discount\n<|file_sep|>edit_history\n--- a/pricing.py\n+++ b/pricing.py\n@@ -1 +1 @@\n-def calculate(price, tax):\n+def calculate(price, tax, discount):\n<|file_sep|>checkout.py\nprice = 100\ntax = 0.2\ndiscount = 10\n<|marker_1|>result = calculate(price, tax)<|user_cursor|>\n<|marker_2|>log(result)\n<|marker_3|><|fim_middle|>",
  "output": "<|marker_1|>result = calculate(price, tax, discount)<|user_cursor|>\n<|marker_2|><|endoftext|>"
}
```

# Соответствие полей в корректирующих данных

| Исходный JSONL | Коррекционные пары / SFT-корпус | Значение                                                                                                               |
| -------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `input`        | `prompt`                        | Тот же сырой NEP-вход                                                                                                  |
| `output`       | `chosen`                        | Положительный обучающий ответ; может быть эталоном или принятой генерацией, поэтому не всегда равен исходному `output` |
| Нет аналога    | `rejected`                      | Реальный отрицательный NEP-ответ модели, если есть пара                                                                |

Пример структуры коррекционной записи (значения сокращены, это не исполняемый NEP-пример):

```json
{
  "source": "/.../final_data_set_v2/nessie_set.jsonl",
  "line": 123,
  "prompt_hash": "...",
  "prompt": "...сырой input...",
  "chosen": "...положительный NEP-ответ с EOS...",
  "rejected": "...отрицательный ответ...",
  "chosen_origin": "reference"
}
```
