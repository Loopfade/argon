# Проверка Argon

Этот документ описывает **текущую** проверку Argon. Исторические журналы M153,
экспериментальные ветки и одноразовые CI-процедуры намеренно не дублируются:
удалённые архивные ветки и Actions runs больше не служат источником этих журналов.

## Закреплённые входы

Актуальные значения задаются в `build-lock.json`:

- Chromium: `154.0.8037.57`, commit
  `73c14f6228d7cd537c855007e8f88678969cc0eb`;
- Titanium: `5c93149e4ca2f8fb659cf7e9fce7ee5e66cbf905`;
- Vanadium: `83085d1694c4de653eac382fa2be6d008f193bce`;
- BoringSSL: `ac39ea6853833c1f18fd23614091d11855e71752`.

`scripts/preflight.py` проверяет согласованность этих входов, gitlink Vanadium,
версии Chromium, сертификата, filter-list pins и GN-конфигурации для каждой ABI.

## Что проверяет Validate scoped Russian CA

Workflow `.github/workflows/validate.yml` запускается на push, pull request и
вручную. Он:

1. Выполняет preflight и генерирует GN args для `arm64`, `arm`, `x64` и
   `x86`.
2. Запускает весь Python unittest suite.
3. Проверяет shell-синтаксис `build.sh`, compatibility entrypoint
   `build-arm64.sh` и CI shell helpers.
4. Применяет CA/JNI overlay к закреплённым upstream-исходникам через
   `tests/check_upstream_patches.py`.
5. Загружает закреплённый BoringSSL, собирает `scoped_ca_test` и запускает
   CTest для production DNS/IP constraints.

Эта проверка подтверждает корректность pins, патчей и тестовой логики, но не
заменяет полную сборку Chromium/APK.

## Полная M154-сборка

Контрольная сборка после сжатия истории успешно прошла 2026-10-01:

- prepared Chromium image — GitHub Actions run `36816737929`;
- release-signed arm64 APK — GitHub Actions run `36816738462`;
- шаг `Build, sign and verify APK` завершился успешно;
- artifact с APK и provenance был загружен успешно.

Для этой M154-сборки `ccache` показал:

- 47 317 cacheable calls;
- 47 316 hits;
- 1 miss;
- лимит 7.0 GB был заполнен; фактический каталог на runner занимал около
  6.6 GB.

CI сохраняет прогресс компиляции через **до 20 checkpoint-этапов**. Этапы
останавливаются раньше, если APK-цель уже достигнута или необходимо оставить
временной резерв для финальной сборки и подписи.

## Release provenance

Publisher принимает только проверенный artifact из доверенного `Build Argon`
run на `main`. Перед публикацией он сверяет source SHA, ABI, режим подписи,
checksum и допустимый drift текущего `main`.

После контрольной M154-сборки publisher корректно отказался создавать новый
релиз, когда `main` уже содержал последующее недокументальное изменение CI.
Это подтверждает работу stale-build guard.

Текущий опубликованный релиз `v154.0.8037.57-argon.2` получен из проверенной
сборки [`36852923075`](https://github.com/Loopfade/argon/actions/runs/36852923075)
на commit `41154d58e8df1a0671e917ca5c0f5b13093ceb02`.

Существующий тег `v154.0.8037.57-argon.1` остаётся привязан к исходному
release commit и не должен переставляться.

## Что ещё не подтверждается автоматически

Полный CI сейчас не доказывает:

- полноценную сборку `arm`, `x64` и `x86` для каждого изменения;
- установку и запуск APK на реальном Android-устройстве;
- UI-поведение настроек сертификатов и расширений;
- end-to-end TLS-сценарии на устройстве;
- публикацию через магазин приложений.

Для release-кандидата эти проверки выполняются отдельно по необходимости.
Локальная release-сборка всех ABI описана в [BUILDING.md](BUILDING.md).

## Критерий безопасного изменения build pipeline

Перед слиянием изменения должно пройти `Validate scoped Russian CA`.
Если затронуты подготовленные Chromium inputs, `build.sh`, `patch.sh`,
`chromium_overlay/**`, `docker/chromium/**`, extensions или pins, необходимо
также дождаться успешных `Build prepared Chromium image` и `Build Argon`.

Изменения только документации не требуют пересборки APK.
