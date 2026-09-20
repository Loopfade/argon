# Argon

Форк [Titanium](https://github.com/jqssun/android-titanium-browser) для Android: расширения (включая Manifest V2) и российский УЦ для `.ru`, `.рф`, `.su` и явно добавленных пользователем доменов.

Механизм внедрения сертификата через Chromium `AdditionalCertificates` и базовое ограничение доверия доменами `.ru`, `.рф` и `.su` адаптированы из [Ruthenium for Android](https://github.com/rutheniumteam/ruthenium-android) по лицензии BSD-3-Clause. Argon дополнительно проверяет ту же политику во встроенном верификаторе Chromium, привязывает её к точному DER-корню и запрещает IP SAN. Дополнительные домены настраиваются в **Настройки → Конфиденциальность и безопасность → Сертификаты Argon**; публичные суффиксы, IP-адреса и `*` не принимаются.

Цели сборки: `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`.

Сборка: [локально на Debian](BUILDING.md) или **Actions → Build Argon → Run workflow**. `arch=all` — все архитектуры в Actions; локально `build.sh` собирает по одной ABI. Требуется ≥100 GiB на каждую сборку.

[Проверки](VALIDATION.md) · [Исходный README](README.upstream.md) · [Лицензия](LICENSE) · [Лицензия Ruthenium](licenses/Ruthenium-BSD-3-Clause.txt)

Версия Chromium, на которой основана сборка: `153.0.8010.52`(commit`78e5e45d4bb41035e17ea4da2cc257f496416ac9`).
