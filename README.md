# Argon

Форк [Titanium](https://github.com/jqssun/android-titanium-browser) для Android: расширения (включая Manifest V2) и российский УЦ только для `.ru`, `.рф` и `.su`.

Механизм внедрения сертификата через Chromium `AdditionalCertificates` и базовое ограничение доверия доменами `.ru`, `.рф` и `.su` адаптированы из [Ruthenium for Android](https://github.com/rutheniumteam/ruthenium-android) по лицензии BSD-3-Clause. Argon дополнительно проверяет ту же политику во встроенном верификаторе Chromium, привязывает её к точному DER-корню и запрещает IP SAN.

Цели сборки: `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`.

Сборка: **Actions → Build Argon → Run workflow**. `arch=all` — все архитектуры; требуется ≥100 GiB на каждую сборку.

**Полный APK пока не собран и не проверен.**

[Проверки](VALIDATION.md) · [Исходный README](README.upstream.md) · [Лицензия](LICENSE) · [Лицензия Ruthenium](licenses/Ruthenium-BSD-3-Clause.txt)
