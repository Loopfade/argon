# Argon

[![Build Argon](https://github.com/Loopfade/argon/actions/workflows/build.yml/badge.svg?branch=main)](https://github.com/Loopfade/argon/actions/workflows/build.yml)

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

`153.0.8010.52` — commit `78e5e45d4bb41035e17ea4da2cc257f496416ac9`.
