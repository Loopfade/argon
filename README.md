# Argon

[![Build Argon](https://github.com/Loopfade/argon/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/Loopfade/argon/actions/workflows/build.yml)
[![Version](https://img.shields.io/github/v/release/Loopfade/argon?display_name=tag&style=flat&logo=github&label=%D0%92%D0%B5%D1%80%D1%81%D0%B8%D1%8F)](https://github.com/Loopfade/argon/releases/latest)

Argon — Android-браузер на базе [Titanium](https://github.com/jqssun/android-titanium-browser) и Chromium с поддержкой расширений и ограниченным доверием к встроенному российскому корневому УЦ.

## Реализовано

- Поддержка расширений Chromium, включая Manifest V2.
- Встроенный российский корневой УЦ с базовым ограничением доверия доменами `.ru`, `.рф` / `.xn--p1ai` и `.su`.
- Дополнительная проверка политики во встроенном верификаторе Chromium: доверие привязано к точному DER-корню, IP SAN запрещены, существующие ограничения не ослабляются.
- Пользовательский список дополнительных DNS-доменов в **Настройки → Конфиденциальность и безопасность → Сертификаты Argon**:
  - добавление и удаление записей;
  - хранение в Chromium PrefService;
  - нормализация регистра, завершающей точки и IDN;
  - запрет IP-адресов, wildcard-записей и публичных суффиксов вроде `.com`, `.net` и `.org`;
  - применение только к явно добавленному домену и его поддоменам.
- Автоматические проверки генератора патча, ограничений доверия и пользовательского списка доменов.


## Сборка, CI и релизы

CI проверяет закреплённые зависимости, патчи и ограничения сертификатов. Релизная сборка APK для `arm64-v8a` и `armeabi-v7a` запускается вручную через [Build Argon](https://github.com/Loopfade/argon/actions/workflows/build.yml) и после успешной проверки автоматически публикуется как GitHub Release.

Текущую сборку можно отслеживать в [GitHub Actions](https://github.com/Loopfade/argon/actions/workflows/build.yml), а статистику последних опубликованных сборок — в [Argon CI dashboard](https://loopfade.github.io/argon/).

Подробности CI и release pipeline описаны в [VALIDATION.md](VALIDATION.md).

## Происхождение и лицензии

Механизм внедрения сертификата через Chromium `AdditionalCertificates` и базовое ограничение доверия доменами `.ru`, `.рф` и `.su` адаптированы из [Ruthenium for Android](https://github.com/rutheniumteam/ruthenium-android) по лицензии BSD-3-Clause.

[Исходный README Titanium](README.upstream.md) · [Лицензия Argon](LICENSE) · [Лицензия Ruthenium](licenses/Ruthenium-BSD-3-Clause.txt)

## Версия Chromium

`154.0.8037.57` — commit `73c14f6228d7cd537c855007e8f88678969cc0eb`.
