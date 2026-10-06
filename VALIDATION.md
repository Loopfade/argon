# CI-проверки Argon

Этот документ описывает текущую CI-схему Argon: проверку исходников и патчей,
подготовку Chromium, ручную релизную сборку двух Android ARM-архитектур,
автоматическое продолжение долгих jobs, публикацию Release и dashboard.

## Закреплённые входы

Актуальные версии и commit SHA задаются в `build-lock.json`:

- Chromium: `154.0.8037.57`, commit
  `73c14f6228d7cd537c855007e8f88678969cc0eb`;
- Titanium: `5c93149e4ca2f8fb659cf7e9fce7ee5e66cbf905`;
- Vanadium: `83085d1694c4de653eac382fa2be6d008f193bce`;
- BoringSSL: `ac39ea6853833c1f18fd23614091d11855e71752`;
- release revision: `2`.

`scripts/preflight.py` проверяет согласованность pins, Vanadium gitlink,
версии Chromium, сертификата, filter-list pins и GN-конфигурации.

## Validate scoped Russian CA

Workflow `.github/workflows/validate.yml` запускается на push, pull request
и вручную. Изменения только dashboard-файлов и workflows его обновления/
публикации исключены и для push, и для pull request.

На push дополнительно выполняется cleanup закрытых PR: отменяются оставшиеся
активными PR-сборки и удаляются их Actions caches. При закрытии PR запускается
только этот cleanup; основная validation job не выполняется.

Validation:

1. Выполняет preflight и генерирует GN args для `arm64`, `arm`, `x64`
   и `x86`.
2. Запускает Python unittest suite.
3. Проверяет shell-синтаксис build/CI scripts.
4. Проверяет применение CA/JNI overlay к закреплённым upstream-исходникам.
5. Собирает и запускает `scoped_ca_test` с закреплённым BoringSSL для
   production DNS/IP constraints.

Эта стадия не собирает релизный APK. `x64` и `x86` проверяются только
на уровне конфигурации/preflight и не входят в release pipeline.

## Подготовленный Chromium image

Workflow `.github/workflows/build-chromium-image.yml` автоматически
запускается на push в `main`, когда меняются входы подготовленного Chromium:
pins, patches, overlays, extensions, ресурсы, GN/build scripts и связанные
файлы. Его также можно запустить вручную.

Workflow подготавливает закреплённые Chromium sources/toolchain и публикует
OCI image в GHCR. Один совместимый prepared image используется обеими
ARM-сборками.

Успешное завершение `Build prepared Chromium image` **не запускает
`Build Argon` автоматически**. Перед сборкой совместимость prepared image
проверяется gate и самой architecture job. Если успешный prepared-image run
уже отсутствует в Actions history, сборка не блокируется только из-за
очищенной истории: фактический GHCR image всё равно проверяется перед
компиляцией.

## Build Argon: запуск и архитектуры

Workflow `.github/workflows/build.yml` имеет два типа запуска:

- в `main` — только ручной `workflow_dispatch`;
- в pull request — автоматически, если PR меняет хотя бы один путь из
  `pull_request.paths` в `build.yml` (build workflow, reusable build/
  continuation/publisher, `.github/ci/**` или signing/release scripts).

Push в `main` и завершение prepared-image workflow сами по себе
`Build Argon` не запускают.

Ручной запуск принимает:

- `runner` — label Linux x64 runner;
- `signing=release|test`;
- `continuation` — внутренний счётчик автоматических продолжений;
  при обычном ручном запуске остаётся `0`.

Matrix содержит:

- `arm64` → Android ABI `arm64-v8a`;
- `arm` → Android ABI `armeabi-v7a`.

Обе jobs используют reusable workflow `.github/workflows/build-arch.yml`,
один prepared Chromium image и выполняются независимо с `fail-fast: false`.

Compiler cache разделён по архитектурам:

- `argon-ccache-v2-arm64-*`;
- `argon-ccache-v2-arm-*`.

Ручной `signing=release` использует постоянный release key.
Ручной `signing=test` и pull request builds используют временную тестовую
подпись.

Каждая architecture job собирает, подписывает и проверяет APK, после чего
загружает artifact с APK, checksum, provenance и лицензиями.

## Checkpoints и автоматическое продолжение

Длительная компиляция разбита на checkpoint-этапы с сохранением
architecture-specific `ccache`. Если job исчерпала выделенный build-time
budget, она инициирует `Continue Argon build`.

Continuation workflow не создаёт новый самостоятельный `Build Argon`.
Он вызывает rerun failed jobs **исходного run**, сохраняя его SHA, event и
cache scope. Поэтому уже успешная архитектура повторно не собирается:

`arm64 ✅ + arm ❌ → retry только arm`.

Если failed обе архитектуры, повторяются обе failed jobs. Автоматическое
продолжение предназначено для исчерпания временного бюджета; реальная ошибка
сборки, ранний OOM/SIGKILL или ручная отмена не должны маскироваться retry.

## Публикация релиза

`.github/workflows/publish-release.yml` является reusable
`workflow_call` и отдельно вручную не запускается.

Publisher вызывается внутри `Build Argon` только если одновременно
выполнены условия:

- обе architecture jobs завершились успешно;
- исходная ветка — `main`;
- исходный event — ручной `workflow_dispatch`;
- выбран `signing=release`.

Ручной `signing=test` и pull request builds релиз не публикуют.

Publisher скачивает **оба** artifacts из одного `Build Argon` run и
проверяет:

- одинаковые source SHA, Chromium version и release revision;
- `arm64-v8a` / `arm64` для первой сборки;
- `armeabi-v7a` / `arm` для второй;
- release signing;
- SHA-256 APK и checksum files;
- ожидаемые имена APK;
- идентичность Chromium, Ruthenium и Titanium license payload между
  архитектурами.

Публичный релиз содержит семь assets: два APK, две SHA-256 checksum и три
license files. Tag имеет формат `v<chromium_version>-<release_revision>`.
Release notes формируются **только на русском языке**.

Если хотя бы одна архитектура не завершилась успешно, publisher не
запускается и публичный релиз не создаётся.

## Dashboard и GitHub Pages

Статический dashboard доступен по
[https://loopfade.github.io/argon/](https://loopfade.github.io/argon/).

Исходники сайта находятся прямо в `main`:

- `index.html` — UI;
- `dashboard-data.json` — read-only snapshot;
- `.nojekyll` — публикация без Jekyll.

Workflow `.github/workflows/update-dashboard.yml` запускается после
завершения `Build Argon` и может быть запущен вручную. При автоматическом
запуске snapshot изменяется только если завершившийся run соответствует
последнему опубликованному Release. В snapshot сохраняются до трёх последних
валидных опубликованных релизных билдов с jobs, step timings, artifacts и
release metadata.

Workflow `.github/workflows/deploy-dashboard.yml` публикует
`index.html`, `dashboard-data.json` и `.nojekyll` через официальный
GitHub Pages deployment из `main`. Браузер не обращается к GitHub API и
не получает токены.

Dashboard является read-only: он не может запускать, перезапускать или
отменять сборки. Активный ещё не опубликованный `Build Argon` следует
смотреть непосредственно в GitHub Actions.

## Что CI не подтверждает автоматически

CI не заменяет проверки на реальном Android-устройстве. Отдельно при
необходимости проверяются:

- установка и запуск APK;
- UI настроек сертификатов и расширений;
- end-to-end TLS-сценарии;
- публикация через магазин приложений.

Текущий release pipeline не собирает `x86_64` и `x86`: публичные APK
выпускаются только для `arm64-v8a` и `armeabi-v7a`.

## Что запускается при изменениях

- Prepared Chromium inputs → автоматически запускается
  `Build prepared Chromium image`.
- Push в `main` → **не запускает релизный `Build Argon`**.
- Релизный `Build Argon` в `main` → запускается только вручную.
- Pull request, меняющий путь из `pull_request.paths` в `build.yml` →
  автоматически запускает тестовый `Build Argon` без публикации релиза.
- `README.md` и `VALIDATION.md` → не запускают APK или prepared-image
  build; для них выполняется обычная validation.
- Dashboard-only файлы и workflows snapshot/deploy → не запускают
  `Build Argon`; изменения snapshot/site публикуют только dashboard.
