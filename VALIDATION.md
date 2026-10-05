# CI-проверки Argon

Этот документ описывает текущую CI-схему Argon: проверку исходников и патчей,
подготовку Chromium, сборку двух Android ARM-архитектур и публикацию релиза.
Исторические Actions runs и локальная сборка здесь не документируются.

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
и вручную. Он:

1. Выполняет preflight и генерирует GN args для `arm64`, `arm`, `x64`
   и `x86`.
2. Запускает Python unittest suite.
3. Проверяет shell-синтаксис build/CI scripts.
4. Проверяет применение CA/JNI overlay к закреплённым upstream-исходникам.
5. Собирает и запускает `scoped_ca_test` с закреплённым BoringSSL для
   production DNS/IP constraints.

Эта стадия проверяет конфигурацию, патчи и policy tests, но сама по себе
не является полной сборкой APK. `x64` и `x86` здесь проверяются только
на уровне конфигурации/preflight и не входят в текущий release pipeline.

## Подготовленный Chromium image

Workflow `.github/workflows/build-chromium-image.yml` запускается, когда
изменяются входы подготовленного Chromium: pins, patches, overlays,
extensions, ресурсы, GN/build scripts и связанные файлы.

Он подготавливает закреплённые Chromium sources/toolchain и публикует общий
OCI image в GHCR. Один и тот же prepared image затем используется обеими
ARM-сборками. Между `Build prepared Chromium image` и `Build Argon`
настроена только односторонняя зависимость, чтобы workflows не образовывали
цикл.

## Build Argon: две архитектуры

Основной workflow `.github/workflows/build.yml` запускает matrix с:

- `arm64` → Android ABI `arm64-v8a`;
- `arm` → Android ABI `armeabi-v7a`.

Обе jobs используют reusable workflow
`.github/workflows/build-arch.yml`, один prepared Chromium image и
выполняются независимо с `fail-fast: false`.

Compiler cache разделён по архитектурам:

- `argon-ccache-v2-arm64-*`;
- `argon-ccache-v2-arm-*`.

Это исключает смешивание объектов ARM64 и ARMv7. На доверенных сборках
`main` используется release key; pull request builds используют временную
test-подпись.

Каждая job собирает, подписывает и проверяет APK, после чего загружает
artifact с APK, checksum, provenance и лицензиями.

## Checkpoints и автоматическое продолжение

Длительная компиляция разбита на checkpoint-этапы с сохранением
architecture-specific `ccache`. Если job исчерпала выделенный build-time
budget, continuation-controller после завершения run вызывает GitHub
`rerun-failed-jobs`.

Поэтому уже успешная архитектура повторно не собирается:

`arm64 ✅ + arm ❌ → retry только arm`.

Если failed обе архитектуры, повторяются обе failed jobs. Автоматическое
продолжение предназначено для исчерпания временного бюджета; реальная ошибка
сборки, ранний OOM/SIGKILL или отмена не маскируются автоматическим retry.

## Публикация релиза

`.github/workflows/publish-release.yml` запускается только после завершения
`Build Argon`. Автоматическая публикация выполняется лишь для успешного
доверенного run на `main`.

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
Release notes формируются на английском и русском языках.

Если хотя бы одна архитектура не завершилась успешно, весь `Build Argon`
не считается успешным и автоматический publisher релиз не создаёт.

## Что CI не подтверждает автоматически

CI не заменяет проверки на реальном Android-устройстве. Отдельно при
необходимости проверяются:

- установка и запуск APK;
- UI настроек сертификатов и расширений;
- end-to-end TLS-сценарии;
- публикация через магазин приложений.

Также текущий release pipeline не собирает `x86_64` и `x86`: публичные
APK выпускаются только для `arm64-v8a` и `armeabi-v7a`.

## Когда запускается пересборка

Изменения prepared Chromium inputs запускают
`Build prepared Chromium image`, после успешного завершения которого
запускается `Build Argon`.

Изменения runtime CI, signing/release logic и основного build workflow,
включённые в `paths` `.github/workflows/build.yml`, запускают
`Build Argon` напрямую и используют последний совместимый prepared image.

Изменения только документации, включая `README.md` и `VALIDATION.md`,
не запускают APK или prepared-image rebuild. Для них выполняется только
`Validate scoped Russian CA`.
