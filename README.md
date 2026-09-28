# Argon

[![Build Argon](https://github.com/Loopfade/argon/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/Loopfade/argon/actions/workflows/build.yml)
[![GitHub stars](https://img.shields.io/github/stars/Loopfade/argon?style=flat&logo=github&label=%D0%97%D0%B2%D1%91%D0%B7%D0%B4%D1%8B)](https://github.com/Loopfade/argon/stargazers)
[![Downloads](https://img.shields.io/github/downloads/Loopfade/argon/total?style=flat&logo=github&label=%D0%A1%D0%BA%D0%B0%D1%87%D0%B8%D0%B2%D0%B0%D0%BD%D0%B8%D1%8F)](https://github.com/Loopfade/argon/releases)

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


## Сборка и проверка

Сборка доступна [локально на Debian](BUILDING.md) Один запуск собирает одну архитектуру; по умолчанию это `arm64`. Для полной локальной подготовки требуется не менее 100 GiB свободного места.
Подробные сценарии проверки описаны в [VALIDATION.md](VALIDATION.md).

## Происхождение и лицензии

Механизм внедрения сертификата через Chromium `AdditionalCertificates` и базовое ограничение доверия доменами `.ru`, `.рф` и `.su` адаптированы из [Ruthenium for Android](https://github.com/rutheniumteam/ruthenium-android) по лицензии BSD-3-Clause.

[Исходный README Titanium](README.upstream.md) · [Лицензия Argon](LICENSE) · [Лицензия Ruthenium](licenses/Ruthenium-BSD-3-Clause.txt)

## Версия Chromium

`154.0.8037.57` — commit `73c14f6228d7cd537c855007e8f88678969cc0eb`.
