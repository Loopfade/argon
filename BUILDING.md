# Локальная release-сборка Argon на Debian

Эта инструкция рассчитана на x86-64 ПК с Debian, Intel Core i7-13700H и 16 ГБ RAM. На одном таком ПК можно кросс-компилировать все четыре Android ABI; отдельная машина для каждой архитектуры не нужна.

## Главное перед началом

- Собирайте ABI **последовательно**, не параллельно: при 16 ГБ RAM ограничивающим ресурсом будет память, а не процессор.
- Используйте `BUILD_JOBS=4`. Если процесс завершается с `Killed` или кодом 137, уменьшите до `2`.
- `scripts/preflight.py` требует минимум 100 GiB свободного места в файловой системе checkout. Практический запас — около 150 GiB для одной сборки.
- Один вызов `build.sh` собирает одну ABI и требует свежий checkout без `chromium/src` и `depot_tools`.
- Для всех ABI и всех будущих релизов используйте **один и тот же постоянный release-keystore**. Потеря ключа лишит возможности выпускать обновления с той же подписью.
- `build.sh` и GitHub Actions собирают по одной ABI; режим `all` не поддерживается.

Если хранить четыре полных checkout одновременно, потребуется как минимум в четыре раза больше места. При ограниченном диске после каждой успешной сборки скопируйте и проверьте `artifacts/`, затем удалите только соответствующий каталог сборки и создайте свежий checkout для следующей ABI.

## 1. Подготовьте Debian

Проверьте архитектуру, память, swap и свободное место:

```bash
uname -m
free -h
swapon --show
df -h .
```

`uname -m` должен вывести `x86_64`.

Установите минимальные инструменты. Остальные зависимости Chromium установит сам `build.sh` через `sudo apt-get`.

```bash
sudo apt-get update
sudo apt-get install -y git python3 ca-certificates default-jdk-headless
```

Во время сборки скрипт также добавит архитектуру пакетов `i386` в APT и установит системные зависимости Chromium.

### Swap для 16 ГБ RAM

Рекомендуется иметь 16–32 ГБ swap; разумный вариант для этой конфигурации — 24 ГБ. Сначала проверьте `swapon --show`. Следующие команды выполняйте только если подходящего swap ещё нет и файл `/swapfile` не существует:

```bash
sudo fallocate -l 24G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

Чтобы swap включался после перезагрузки, добавьте в `/etc/fstab` ровно одну строку:

```text
/swapfile none swap sw 0 0
```

## 2. Создайте отдельный ключ Argon

Этот раздел выполняется **один раз**. Не создавайте новый ключ для каждой ABI или версии и не используйте ключ Titanium, если нужна отдельная подпись Argon.

```bash
export ARGON_KEY_DIR="$HOME/.local/share/argon-signing"
export ARGON_KEYSTORE="$ARGON_KEY_DIR/argon-release.p12"

install -d -m 700 "$ARGON_KEY_DIR"

keytool -genkeypair \
  -keystore "$ARGON_KEYSTORE" \
  -storetype PKCS12 \
  -alias argon-release \
  -keyalg RSA \
  -keysize 4096 \
  -sigalg SHA256withRSA \
  -validity 10000 \
  -dname "CN=Argon Release,O=Argon"

chmod 600 "$ARGON_KEYSTORE"
```

`keytool` запросит один пароль и его повтор для подтверждения. В созданном этой командой PKCS12 пароль приватного ключа совпадает с паролем хранилища. Сохраните файл `.p12` и пароль в надёжной резервной копии вне репозитория. В GitHub Secrets задайте одинаковое значение для `TITANIUM_RU_STORE_PASSWORD` и `TITANIUM_RU_KEY_PASSWORD`.

Перед каждой серией сборок загрузите ключ и пароли в текущий shell:

```bash
export ARGON_KEY_DIR="$HOME/.local/share/argon-signing"
export ARGON_KEYSTORE="$ARGON_KEY_DIR/argon-release.p12"

export TITANIUM_RU_KEY_ALIAS="argon-release"

IFS= read -rsp 'Пароль PKCS12: ' TITANIUM_RU_STORE_PASSWORD
printf '\n'
TITANIUM_RU_KEY_PASSWORD="$TITANIUM_RU_STORE_PASSWORD"

TITANIUM_RU_KEYSTORE_BASE64="$(base64 -w0 "$ARGON_KEYSTORE")"

export TITANIUM_RU_STORE_PASSWORD
export TITANIUM_RU_KEY_PASSWORD
export TITANIUM_RU_KEYSTORE_BASE64
```

Имена переменных `TITANIUM_RU_*` пока сохранены в коде Argon для совместимости сборочных скриптов. Они не означают, что используется ключ Titanium: подпись определяется содержимым вашего PKCS12-ключа.

Проверьте ключ и запишите его SHA-256 fingerprint:

```bash
keytool -exportcert -rfc \
  -keystore "$ARGON_KEYSTORE" \
  -storetype PKCS12 \
  -storepass:env TITANIUM_RU_STORE_PASSWORD \
  -alias "$TITANIUM_RU_KEY_ALIAS" |
openssl x509 -noout -fingerprint -sha256 -subject -dates
```

В GitHub Actions push в `main` запускает режим `release` с постоянным ключом из GitHub Secrets. При ручном запуске `release` выбран по умолчанию; для диагностики можно явно выбрать `test`. Сборки pull request используют временную тестовую подпись без release-секретов. Автоматическое продолжение сохраняет выбранный режим подписи.

Режим `release` проверяет PKCS12, оба пароля, alias и соответствие приватного ключа сертификату до прогрева кэша. Проверка не выводит секреты в лог; временный файл ключа удаляется после проверки. Если секреты отсутствуют или неверны, сборка завершается ошибкой до компиляции; автоматической замены на тестовую подпись нет.

## 3. Зафиксируйте исходный commit

Если собираете несколько ABI последовательно, используйте один commit. Один раз сохраните текущий commit `main`:

```bash
export ARGON_REF="$(
  git ls-remote https://github.com/Loopfade/argon.git refs/heads/main |
  awk '{print $1}'
)"
test -n "$ARGON_REF"
printf 'Argon source commit: %s\n' "$ARGON_REF"
```

Вместо текущей ветки можно явно задать SHA проверенного commit:

```bash
export ARGON_REF="<полный commit SHA>"
```

Подготовьте каталоги:

```bash
export ARGON_BUILDS="$HOME/argon-builds"
export ARGON_RELEASES="$HOME/argon-releases"
export BUILD_JOBS=4

mkdir -p "$ARGON_BUILDS"
install -d -m 700 "$ARGON_RELEASES"
df -h "$ARGON_BUILDS"
```

## 4. Соберите одну архитектуру

Добавьте в текущий shell функцию:

```bash
build_argon() {
  local arch="$1"
  local checkout="$ARGON_BUILDS/argon-$arch"

  if [[ -e "$checkout" ]]; then
    printf 'Checkout уже существует: %s\nНужен новый пустой путь.\n' "$checkout" >&2
    return 1
  fi

  git clone https://github.com/Loopfade/argon.git "$checkout"
  git -C "$checkout" checkout --detach "$ARGON_REF"
  git -C "$checkout" submodule update --init --recursive

  (
    cd "$checkout"
    SIGNING_MODE=release BUILD_JOBS="$BUILD_JOBS" bash build.sh "$arch"
    cp -a artifacts/. "$ARGON_RELEASES/"
  )
}
```

Запускайте команды **строго по одной**, дожидаясь полного завершения предыдущей:

```bash
build_argon arm64
build_argon arm
build_argon x64
build_argon x86
```

Соответствие аргументов, ABI и результатов:

| Команда | Android ABI | Каталог результата |
|---|---|---|
| `build_argon arm64` | `arm64-v8a` | `$ARGON_RELEASES/arm64-v8a/` |
| `build_argon arm` | `armeabi-v7a` | `$ARGON_RELEASES/armeabi-v7a/` |
| `build_argon x64` | `x86_64` | `$ARGON_RELEASES/x86_64/` |
| `build_argon x86` | `x86` | `$ARGON_RELEASES/x86/` |

При небольшом диске не запускайте следующую команду сразу. Сначала выполните проверки ниже, сохраните каталог соответствующей ABI, а уже затем удалите конкретный checkout `$ARGON_BUILDS/argon-<arch>`. Не удаляйте `$ARGON_KEY_DIR` и `$ARGON_RELEASES`.

## 5. Проверьте результаты

В каждом каталоге ABI должны появиться:

- `Argon-<version>-release-<abi>.apk`;
- файл `.apk.sha256`;
- `apk-signature.txt`;
- `signing-certificate.pem`;
- `build-info.json`;
- файлы лицензий.

Проверьте контрольные суммы, режим подписи, ABI и исходный commit:

```bash
for abi in arm64-v8a armeabi-v7a x86_64 x86; do
  dir="$ARGON_RELEASES/$abi"
  printf '\n== %s ==\n' "$abi"
  (
    cd "$dir"
    sha256sum -c ./*.apk.sha256
    grep -E '"source_commit"|"signing_mode"|"abi"' build-info.json
  )
done
```

Во всех четырёх `build-info.json` должны быть:

- одинаковый `source_commit`, равный `$ARGON_REF`;
- `"signing_mode": "release"`;
- соответствующая каталогу ABI.

Выведите fingerprint сертификата каждого APK:

```bash
for abi in arm64-v8a armeabi-v7a x86_64 x86; do
  printf '%s: ' "$abi"
  openssl x509 \
    -in "$ARGON_RELEASES/$abi/signing-certificate.pem" \
    -noout -fingerprint -sha256
done
```

Все четыре fingerprint должны быть одинаковыми и совпадать с fingerprint PKCS12-ключа из раздела 2. Также можно открыть `apk-signature.txt`: `sign_and_verify.py` уже выполняет `apksigner verify --verbose --print-certs`, проверяет ZIP, zipalign, package ID, имя приложения и единственную ожидаемую ABI.

После завершения удалите секреты из текущего shell:

```bash
unset TITANIUM_RU_KEYSTORE_BASE64
unset TITANIUM_RU_STORE_PASSWORD
unset TITANIUM_RU_KEY_PASSWORD
```

## Частые ошибки

### `at least 100 GiB required`

В файловой системе, где находится checkout, меньше 100 GiB свободного места. Освободите место или перенесите `ARGON_BUILDS` на более ёмкий SSD. Для одной сборки рекомендуется около 150 GiB запаса.

### `Killed`, код 137 или ошибка линковщика из-за памяти

Закройте тяжёлые приложения, убедитесь, что swap активен, и повторите сборку в **новом checkout** с:

```bash
export BUILD_JOBS=2
```

### `Use a fresh dedicated checkout`

В каталоге уже есть `chromium/src` или `depot_tools`. Повторный запуск в нём намеренно заблокирован; создайте новый checkout.

### `Missing release keystore/password/alias`

Снова выполните команды экспорта из раздела 2 в том же shell, из которого запускается `build_argon`.

### APK не устанавливается поверх тестовой сборки

Тестовый режим создаёт временный ключ, поэтому release-подпись отличается. При одинаковом package ID Android не разрешит обновление APK с другим сертификатом: тестовую сборку придётся удалить перед установкой release APK.
