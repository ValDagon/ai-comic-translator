# AI Comic Translator

Пайплайн для перевода комикса (любой исходный язык → выбранный целевой)
с сохранением контекста: детекция облачков + OCR + инпейнтинг делает готовый
open-source движок
[manga-image-translator](https://github.com/zyddnys/manga-image-translator),
перевод — через [OpenRouter](https://openrouter.ai) (по умолчанию
`x-ai/grok-4.3`), финальную
вклейку текста делает Pillow по координатам, которые дал детектор.

UI: RU / EN / JA / KO. Целевой язык перевода выбирается в job options
(см. `translate_request.SUPPORTED_TARGET_LANGUAGES`).

## Границы репозитория

**Этот репозиторий** — только наш код: `main.py`, `pipeline.py`, `extract_parse.py`,
`translate_request.py`, `render.py`, `run_extract.py`, `config_loader.py`,
`config.toml`, `config.example.toml`, `config.local.toml.example`,
`mit_extract_config.toml`, `api.py`, `worker.py`, `web/`,
`templates/`, `static/`, `fonts/`, `locales/`, `service/`, `scripts/`, `docs/`,
`tests/`, `Dockerfile`, `docker-compose.yml`, `LICENSE`.

**[manga-image-translator](https://github.com/zyddnys/manga-image-translator)** — отдельный
upstream-проект. Мы его **не форкаем и не правим** внутри этого репо: клонируете
рядом (каталог в `.gitignore`), путь через `--mit-repo` / `[server] mit_repo` /
`MIT_REPO`. `run_extract.py` вызывает его как subprocess. Автотесты и CI **не**
запускают MIT — только парсинг `*_translations.txt`, OpenRouter (мок/без сети)
и наш рендер.

## Установка

```bash
git clone https://github.com/zyddnys/manga-image-translator.git
# Закрепите commit: формат *_translations.txt — контракт с extract_parse.py.
# После обновления MIT прогоните extract на 1–2 страницах и проверьте дамп.
cd manga-image-translator
pip install -r requirements.txt --break-system-packages
cd ..

pip install -r requirements.txt --break-system-packages
```

### Конфиг (`config.toml`)

`config.toml` **коммитится** (модель, шрифт, батчи, пути…). Секрет `api_key` —
только в `config.local.toml` (gitignore) или через env:

```bash
cp config.local.toml.example config.local.toml
# впишите api_key
# либо:
export OPENROUTER_API_KEY=sk-or-...
```

В конфиге: `[openrouter] model`, `pages_per_batch`, `target_lang`
и `[render] font_path`, `font_scale`.
`target_lang` — язык промпта OpenRouter (не встроенный переводчик MIT).
Флаги CLI (`--model`, `--target-lang`, `--font`, …) перекрывают значения из файла.
Приоритет: CLI > `config.local.toml` > `config.toml` > `OPENROUTER_API_KEY`.

Ключ для OpenRouter: зарегистрируйтесь на https://openrouter.ai, затем
создайте ключ в https://openrouter.ai/keys. Баланс пополняется отдельно
(Settings → Credits) — работает по предоплате.

**Никогда не вставляйте ключ прямо в код или в чат** — если он засветился
где-то публично, отзовите его на той же странице и создайте новый.

## Заметка про модель

`x-ai/grok-4.3` — модель по умолчанию. На длинный комикс (~50 страниц) текст
уходит **пачками** по 30 страниц (`--pages-per-batch`), с переносом уже
переведённых имён в следующую пачку. Один запрос на весь комикс:
`--pages-per-batch 0`.

Если модель **отказывается** переводить (ответ текстом вместо JSON) — это
политика провайдера, а не ошибка парсера и не «слишком много страниц».
Выберите другую модель на [openrouter.ai/models](https://openrouter.ai/models)
и укажите `--model vendor/model-id`.

В `translate_request.py` есть разбор JSON, если модель добавила текст
до/после объекта (но не при явном отказе).

## Запуск на MacBook Air M2

Никаких дополнительных флагов не нужно — manga-image-translator сам
определит Apple Silicon и по возможности использует MPS (ускорение на
GPU M2) вместо CPU. Модели детекции/OCR/инпейнтинга скачаются при первом
запуске (несколько сотен МБ — 1-2 ГБ) и будут закэшированы. На M2 обработка
50-60 страниц займёт разумное время (единицы минут), CUDA не требуется —
это чисто NVIDIA-флаг, для Mac он не нужен.

При первом запуске manga-image-translator скачает свои модели (детектор
облачков, OCR, инпейнтер) — это может занять время и лучше делать на
машине с GPU, хотя на CPU тоже работает, просто медленнее.

## Запуск

Положите 50-60 страниц (jpg/png) в `raw_files/pages_input/` и:

```bash
python main.py --mit-repo ./manga-image-translator
```

Пути по умолчанию: `raw_files/pages_input` → `results/pages_out`.
При необходимости явно укажите `--input` и `--out`.

Результат — переведённые страницы в `results/pages_out/` (очищенные
промежуточные — в `results/pages_out/_clean/`).

Если извлечение уже было сделано и вы просто хотите пересобрать рендер
(например, после правки шрифта) — `--skip-extract` и при необходимости
`--translations-in results/translations.json` (без повторного запроса к модели).

```bash
python main.py --skip-extract --translations-in results/translations.json \
  --input raw_files/pages_input --out results/pages_out
```

## Структура

- `run_extract.py` — вызывает manga-image-translator: детекция + OCR +
  инпейнтинг (`translator=original` + `renderer=none`; встроенный перевод MIT не нужен)
- `extract_parse.py` — парсит `*_translations.txt` (текст + координаты облачков)
- `translate_request.py` — батч-запрос к OpenRouter с глоссарием имён/терминов
- `render.py` — вклейка перевода в очищенные облачка (Pillow)
- `pipeline.py` — оркестрация extract → translate → render (режимы для API)
- `main.py` — CLI поверх `pipeline.run_pipeline` (кэш: `results/translations.json`)
- `api.py` / `worker.py` — FastAPI + фоновый worker (SQLite)
- `web/` / `templates/` / `static/` — UI на Jinja2 + HTMX + CSS/JS
- `fonts/` — шрифт по умолчанию (`fonts/IrinaCTT.ttf`, см. `config.example.toml`)
- `config_loader.py` / `config.toml` — публичные настройки (в git); секреты в
  `config.local.toml` (см. `config.local.toml.example`)
- `mit_extract_config.toml` — параметры извлечения для MIT (`[translator]`, `[render]`)
- `service/` — SQLite, пути проектов, upload, job runner
- `tests/` — pytest (без сети и без MIT)

### Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

CI (GitHub Actions) гоняет `pytest -m "not network"` без OpenRouter и MIT.

### Деплой на VPS

Один VPS, Docker Compose, HTTPS (Caddy), login/register в приложении:
см. [docs/deploy.md](docs/deploy.md). Подписка и крипто-оплата — позже; в БД уже
есть поля `plan` / `subscription_status`.

### Локальный API (фаза 2)

Два процесса: HTTP API и worker с очередью в SQLite. Данные проектов — в
`data/projects/<id>/` (`input`, `_clean`, `out`, `translations.json`), не в
`results/`.

**Локально** (если `COMIC_APP_ENV` не задан — это `dev`): без OAuth
(авто-пользователь `dev@local.dev`), UI в режиме разработчика. На сервере
Docker Compose ставит `COMIC_APP_ENV=release` (OAuth обязателен, developer UI
выключен). Не биндите `0.0.0.0` без прокси — на публичный хост см.
[docs/deploy.md](docs/deploy.md).

```bash
pip install -r requirements.txt
# config.toml: [server] mit_repo; api_key — в config.local.toml или OPENROUTER_API_KEY
uvicorn api:app --host 127.0.0.1 --port 8000
python worker.py
```

Откройте **http://127.0.0.1:8000/app** — сразу проекты и пайплайн (нужен worker).
Swagger на **/docs**.

Пример через API: `POST /projects` → загрузка страниц `POST /projects/{id}/pages` →
`POST /projects/{id}/jobs` с телом `{"kind":"full"}` (или по шагам
`extract` / `translate` / `render`) → статус `GET .../jobs/{job_id}` →
скачать ZIP `GET /projects/{id}/download`.

Переменные окружения:

| Переменная | Назначение |
|------------|------------|
| `OPENROUTER_API_KEY` | ключ OpenRouter (если не в config.local.toml) |
| `MIT_REPO` | путь к клону manga-image-translator |
| `COMIC_DATA_DIR` | каталог проектов API (по умолчанию `data`) |
| `COMIC_DATABASE` | путь к SQLite |
| `COMIC_SESSION_SECRET` | секрет cookie-сессий (обязателен в проде) |
| `COMIC_APP_ENV` | не задан/`dev` — локально без auth; `release` — сервер (Compose) |
| `COMIC_ALLOW_REGISTER` | `1`/`0` — открытая регистрация OAuth (release) |
| `COMIC_SESSION_HTTPS_ONLY` | `1` в проде за HTTPS (Secure cookie) |
| `COMIC_API_HOST` / `COMIC_API_PORT` | подсказка host/port (см. `[server]`; uvicorn — вручную) |
| `COMIC_WORKER_POLL_SEC` | интервал опроса очереди в `worker.py` (по умолчанию `2`) |
| `OPENROUTER_DEBUG_RAW` | куда писать сырой ответ при ошибке парсинга |

## Что стоит донастроить под себя

- **Текст вылезает за облачко**: уменьшите `--font-scale` (например `1.0`
  или `0.95`); рендер также сильнее ужимает длинные строки и длинные слова.
- **Шрифт**: в `config.toml` → `[render] font_path` (относительно корня проекта)
  или `--font`. В репозитории — `fonts/IrinaCTT.ttf` (см. `config.example.toml`);
  иначе fallback на DejaVu Sans Bold / Arial (кириллица).
- **Очень длинный комикс**: батчи по 30 страниц (config / `--pages-per-batch`);
  один запрос на весь том: `--pages-per-batch 0`.
- **Строгость перевода**: `--strict` — падать, если модель пропустила id облачка.
- **Отладка OpenRouter**: при ошибке парсинга сырой ответ пишется в
  `results/openrouter_last_raw.txt` (или путь из `OPENROUTER_DEBUG_RAW`).
- **Звукоподражания (BOOM, CRASH)**: детектор комикс-текста иногда цепляет
  их отдельным регионом — проверьте `*_translations.txt` глазами перед
  переводом, если важна точность.
- Формат `*_translations.txt` проверен на MIT
  (`manga_translator/mode/local.py::_save_text_to_file`). После обновления
  апстрима зафиксируйте commit клона и при смене формата дампа обновите
  `extract_parse.py`. Docker-сборка патчит pinned MIT через
  `scripts/patch_mit_for_extract.py` (иначе `--save-text` делает `exit(-1)`).
- Лицензия нашего кода — `LICENSE` (MIT). Шрифт в `fonts/` и сам MIT —
  по своим лицензиям.
