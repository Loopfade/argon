# Scoped certificate verification fixture

`check_extra_constraints.cc.in` is the unchanged `CheckExtraConstraints` method
from `net/cert/cert_verify_proc_builtin.cc` at the Chromium commit in
`build-lock.json` (`a733b5afbb3ba618844ff8876b89ad1a774b2f18`). It uses Chromium's
BSD license, reproduced in `../homepage/LICENSE.chromium`.
`check_upstream_patches.py` checks that this method still matches upstream.

The CMake test applies the production patch to this method and compiles it with
the pinned BoringSSL. Only its surrounding delegate and Chromium buffer/CIDR
types are adapted. This catches accidental execution of the generic all-SAN
check before or after Argon's hostname check.

`multi-san.pem` is a synthetic, self-signed leaf with `bank.ru`, `www.bank.ru`,
`other.com` and the documentation IP `192.0.2.1`. No private key is retained in
the repository. The test supplies a candidate path ending in the pinned root
to exercise post-path trust restrictions; it does not claim that this test
leaf was signed by that root or replace Chromium's path validation.
