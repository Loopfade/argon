# Titanium RU для Android (arm64)

Форк [Titanium](https://github.com/jqssun/android-titanium-browser) с российским корневым УЦ, ограниченным доменами `.ru`, `.рф` (`.xn--p1ai`) и `.su`. Поддержка расширений Titanium, включая Manifest V2, сохранена.

**Статус: исходники и сценарий сборки подготовлены; полный APK пока не собран.**
Локально прошли 32 проверки политики на закреплённом BoringSSL, 6 Python-тестов и проверка применения патча к исходникам Chromium после соответствующих патчей Vanadium. Это не заменяет сборку всего браузера и испытания на Android.

## Что изменено

- Корневой Russian Trusted Root CA взят из [Ruthenium](https://github.com/rutheniumteam/ruthenium-android); его DER SHA-256 закреплён: `d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31`.
- УЦ добавляется через `trust_anchors_with_additional_constraints`. Системное хранилище Android не изменяется.
- Разрешены только имена ниже трёх DNS-зон. Голые TLD и имена вроде `bank.ru.example.com` не разрешены.
- Все DNS-имена сертификата должны укладываться в ограничение. Сертификат одновременно на `bank.ru` и `example.com` отклоняется даже при открытии `bank.ru`.
- Дополнительная проверка каждой кандидатной цепочки запрещает **все IPv4/IPv6 SAN**, в том числе `0.0.0.0`, `::`, а также смешанные DNS/IP-сертификаты под этим корнем. Используется ограниченный тип IP с пустым списком разрешённых сетей, без исключений для отдельных адресов.
- Имя хоста, подписи, срок действия, обычная политика отзыва и другие проверки Chromium продолжают работать. Подпись корневым УЦ не превращается в обход TLS-ошибок.
- Та же дополнительная проверка применяется, если идентичный закреплённый корневой сертификат уже установлен в системе. Другие корни не изменяются.
- Российский промежуточный УЦ не объявлен самостоятельным корнем доверия: сервер должен отдавать промежуточную цепочку.
- Имя приложения — **Titanium RU**, package — `app.titaniumru.browser`. Оно может устанавливаться рядом с оригинальным Titanium.
- Только `arm64-v8a`; версии Chromium, Vanadium, BoringSSL, depot_tools и предустановленного расширения зафиксированы в `build-lock.json`. Автоматического перехода на новые версии УЦ или движка нет.

## Расширения

В браузере доступны `chrome://extensions`, установка из Chrome Web Store и загрузка распакованных расширений. Включение расширения в инкогнито требует обычного разрешения пользователя. Код этих возможностей унаследован от Titanium и пока не проверен на собранном APK этого форка. Подробности — в [README исходного проекта](README.upstream.md).

## Сборка APK в GitHub Actions

1. Разместите этот код в своём форке GitHub, сохранив submodule `vanadium`.
2. Откройте **Actions → Build Titanium RU arm64 → Run workflow**.
3. `runner=ubuntu-latest` запускает сборку на обычном GitHub runner; перед сборкой освобождается место только на одноразовой машине GitHub. Если оставшегося места или лимита времени недостаточно, используйте метку своего Linux x64 runner.
4. `signing=test` создаёт устанавливаемый APK с временной подписью. `signing=release` использует постоянный ключ из secrets.
5. Только после успешной сборки, проверки подписи, package, имени, ABI, ZIP и выравнивания APK появится в artifact `Titanium-RU-arm64-<commit>` вместе с SHA-256, открытым сертификатом подписи, лицензиями и `build-info.json`.

Сценарий требует как минимум **100 GiB свободного места**. Для своей машины разумно выделить 150–200 GiB и 32 GiB RAM; нужен Ubuntu/Linux x64 с `sudo` для установки сборочных зависимостей. Требования Chromium: https://chromium.googlesource.com/chromium/src/+/main/docs/android_build_instructions.md . Полная сборка может занять несколько часов и расходует минуты GitHub Actions. Успех на стандартном runner пока не проверен.

Сборка из нового локального checkout:

```sh
git submodule update --init
SIGNING_MODE=test bash build-arm64.sh
```

Повторный запуск в частично собранном дереве намеренно останавливается. Используйте новый рабочий checkout. Скрипт не удаляет существующий Chromium checkout автоматически и не меняет закреплённый submodule Vanadium.

## Подпись для постоянных обновлений

Для `signing=release` нужны repository secrets:

| Secret | Значение |
|---|---|
| `TITANIUM_RU_KEYSTORE_BASE64` | JKS, закодированный в base64 |
| `TITANIUM_RU_STORE_PASSWORD` | Пароль хранилища |
| `TITANIUM_RU_KEY_PASSWORD` | Пароль ключа |
| `TITANIUM_RU_KEY_ALIAS` | Alias ключа |

Закрытый ключ не помещается в git или build artifacts. Режим `test` каждый раз генерирует новый временный ключ: следующий такой APK не обновит установленную предыдущую тестовую сборку; удаление старого приложения стирает его данные. Для постоянного использования нужен сохранённый release-ключ.

## Проверки

```sh
python3 scripts/preflight.py --inputs-only
python3 -m unittest discover -s tests -v
cmake -S tests -B .build/policy-tests -DBORINGSSL_SOURCE_DIR=/path/to/pinned/boringssl -DCMAKE_BUILD_TYPE=Release
cmake --build .build/policy-tests --target scoped_ca_test -j 4
ctest --test-dir .build/policy-tests --output-on-failure
```

Workflow `Validate scoped Russian CA` выполняет эти проверки на обычном GitHub runner. Проверки C++ вызывают производственную реализацию ограничения через настоящий BoringSSL, а не повторяют её алгоритм на другом языке.

После получения APK необходимо проверить на Android: запуск приложения, страницу с российской цепочкой в разрешённой зоне, обычные HTTPS-сайты, установку и работу расширения, инкогнито и поведение при неверных сертификатах. Эти испытания пока не выполнены.

## Источники и лицензии

База Titanium: `1c05bb4cb552b54bbcfc29ee6f208be4d8129b36`.
Vanadium: `9919fca315ddb291441122f7c694bd1cb74be33e`.
Chromium: `153.0.8010.47`, `73934a44f61e6b3878d1943064c141a5a820f5f7`.
Ruthenium как образец интеграции УЦ: `6264df31e89f4f372bcccbeb976e699735007476`.

Лицензия Titanium сохранена в `LICENSE`, лицензия заимствованной интеграции Ruthenium — в `licenses/Ruthenium-BSD-3-Clause.txt`. Уведомления Chromium и зависимостей остаются в исходниках и `chrome://credits`. Форк не является официальной сборкой Titanium или Ruthenium.
