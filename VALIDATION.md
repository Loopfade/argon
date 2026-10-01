# Проверки подготовленных исходников

Дата: 2026-09-20.

## Экспериментальный список дополнительных доменов УЦ

| Проверка | Результат |
|---|---|
| Profile list-pref, его наблюдение и live-update `AdditionalCertificates` | Проверены по точному `profile_network_context_service.cc` Chromium `78e5e45d...` |
| Точки расширения Android Java/resources/C++ | Патчер дважды применён к результату всей закреплённой серии Vanadium/Titanium patches |
| JNI Android settings | Добавлен отдельный `generate_jni` target; один target подключён к Java `srcjar_deps` и C++ deps, прямое дублирование Java source в `chrome/android/BUILD.gn` удалено |
| Безопасное копирование DER | Сохранён `base::ToVector(base::span(net::kTitaniumRussianRootDer))`; арифметика указателей не используется |
| Exact/subdomain-семантика BoringSSL | В production test добавлены `example.com`, его поддомены и отрицательные sibling/suffix cases |
| Нормализация, IDN, Public Suffix List, wildcard/IP/URL и встроенные зоны | Добавлен Chromium `net_unittests` gtest на production helper |
| Python regression/idempotence tests | 26 тестов пройдено локально |
| Preflight закреплённых входов и синтаксис shell | Пройдены |
| EasyList/EasyPrivacy inputs | Все три URL закреплены SHA-256; hook пишет итоговый файл атомарно только после проверки каждого digest |
| GitHub Actions | Сторонние Actions закреплены commit SHA; изменения интеграции запускают полный test-signed arm64 build и компиляцию `net_unittests` |

В текущей среде отсутствуют `cmake`, Ninja и полный Chromium checkout, поэтому
новые C++/Java цели здесь локально не собраны. Workflow `Validate scoped Russian CA`
собирает BoringSSL test, а workflow `Build Argon` теперь компилирует `net_unittests`
и APK для изменений интеграции. Запуск Android gtest и UI/TLS smoke-тестов всё ещё
требует устройства или эмулятора.

## Расширение архитектур

Добавлены цели `arm` (`armeabi-v7a`), `x64` (`x86_64`) и `x86`; `arm64` (`arm64-v8a`) остаётся по умолчанию. Настройки сопоставлены с `build/config/android/abi.gni` закреплённого Chromium и ограничением DrumBrake из закреплённого V8 и патчей Vanadium.

| Проверка изменения | Результат |
|---|---|
| Генерация GN для четырёх CPU и их имён ABI, сохранение расширений/package и запрет второго ABI | Пройдена |
| DrumBrake и его bounds checks | Включены только для `arm64`/`x64`; выключены для `arm`/`x86` |
| Неверные архитектуры, отсутствующие/дублированные параметры шаблона | Отклоняются до сборки |
| APK с неверным, смешанным или отсутствующим ABI; повреждённый ZIP | Отклоняются на тестовых ZIP-файлах |
| Имена APK, SHA-256, ABI/CPU в метаданных, сертификаты и лицензии | Проверены для четырёх целей с имитацией команд SDK/JDK; файлы других ABI сохраняются |
| Совместимость `build-arm64.sh` и arm64 по умолчанию | Пройдена |
| Python-тесты | 17 пройдены (11 новых и 6 существующих) |
| Синтаксис shell/Python/YAML и `git diff --check` | Пройдены |

Полная сборка Chromium, GN generation в полном дереве и запуск новых APK здесь не выполнены: доступны около 29 GiB вместо требуемых 100 GiB, нет Android SDK и полного checkout зависимостей. Тесты упаковки не подтверждают действительность подписи реального APK. Для полной проверки каждой архитектуры используйте ручной workflow `Build Argon` с `arch=all`, затем испытания на соответствующих устройствах/эмуляторах.

## Ранее выполненные проверки базового форка

| Проверка | Результат |
|---|---|
| DER SHA-256 российского корня и сгенерированный C++ header | Совпадают с закреплённым значением |
| Версия Chromium и commit submodule Vanadium | Совпадают с `build-lock.json` |
| C++ helper на BoringSSL `defe5810ee8be430bcdeccf46a199bec0e93abdb` | Скомпилирован GCC 13.3.0 |
| Разрешённые и запрещённые DNS-имена, смешанные SAN, IPv4/IPv6, идентификация корня | 32 проверки пройдены |
| Целостность патча и сертификата, повторное применение, отказ при изменённых якорях | 6 Python-тестов пройдены |
| Применение к изменяемым файлам Chromium `153.0.8010.47` после трёх соответствующих патчей Vanadium | Успешно, повторное применение не меняет файлы |
| Синтаксис shell/Python/YAML, `git diff --check` | Успешно |
| GitHub Actions: закреплённые входные данные, 6 Python-тестов, сборка C++ helper и 32 проверки BoringSSL | [Успешно, запуск №2](https://github.com/Loopfade/argon/actions/runs/35429972730), commit `f919a2a17cec9cc11d7e1a9819216acbebfbd553` |
| Совпадение опубликованного кода с локально проверенным | Все blob SHA и commit submodule совпали; новые скрипты запускаются через `bash`/`python3` |
| Полная локальная сборка APK | Не выполнена: preflight обнаружил 29.1 GiB вместо требуемых 100 GiB |
| Полная сборка APK в GitHub Actions | [Запуск №1](https://github.com/Loopfade/argon/actions/runs/35430044143) остановился до компиляции на hook патчей подпроектов Vanadium; preflight, базовые патчи и синхронизация зависимостей пройдены, после очистки runner было 108 GiB свободно |
| Исправление hook подпроектов | Локально воспроизведён тот же `IndexError` при Git identity только в родительском репозитории. Передача `GIT_COMMITTER_NAME` / `GIT_COMMITTER_EMAIL` дочерним процессам устранила ошибку; тестовый патч применился тем же скриптом Vanadium. Глобальная конфигурация Git не меняется |
| Второй запуск полной сборки | [Запуск №2](https://github.com/Loopfade/argon/actions/runs/35430962395) успешно применил все патчи подпроектов, загрузил закреплённое расширение, применил патч УЦ и прошёл C++-тест политики. Остановился перед GN: отсутствовал `python3_bin_reldir.txt` в depot_tools |
| Исправление bootstrap depot_tools | Добавлены `ensure_bootstrap`, ранняя проверка Python и повторная проверка commit depot_tools. Bootstrap Python локально выполнен: `Python 3.11.8`, commit остался `0306e4682b4ac35287c726fa35a983157a625902` |
| Подпись/ABI/package реального APK | Не проверены; выполняются скриптом после будущей успешной сборки |
| Запуск браузера и расширений на Android | Не проверены |

Локальные тесты не доказывают успешную компиляцию всего Chromium, работоспособность
APK или интеграцию TLS в работающем Android-браузере. Актуальные запуски полной
сборки и артефакты доступны в [GitHub Actions](https://github.com/Loopfade/argon/actions/workflows/build.yml).
