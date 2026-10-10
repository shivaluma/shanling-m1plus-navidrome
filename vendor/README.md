# Vendored dependencies

cJSON 1.7.19, MIT, fetched from the upstream DaveGamble/cJSON v1.7.19 tag.
- cJSON.c SHA256 298581a04a36c0165da4b0aade235c23088cb2faa58651d720ea2f3706ed0b0d
- cJSON.h SHA256 25b0145150d500498e4d209cec69c18c42cf818bffcc54690be3b895a2a16dee
- License SHA256 a36dda207c36db5818729c54e7ad4e8b0c6fba847491ba64f372c1a2037b6d5c

The CA bundle is fetched from https://curl.se/ca/cacert.pem and verified against
https://curl.se/ca/cacert.pem.sha256. Exact digest/date are retained beside the
bundle. It is extracted from Mozilla's trust store by curl's maintainers.
It is required because the supplied firmware's /etc/ssl/certs is empty. The launcher passes the SD-card bundle explicitly through SSL_CERT_FILE.
Do not disable TLS verification to compensate for missing device roots or time.

Curl7.73.0 headers match the stock libcurl/7.73.0 version string. Source from https://curl.se/download/curl-7.73.0.tar.xz, archive SHA256 7c4c7ca4ea88abe00fea4740dcf81075c031b1d0bb23aff2d5efde20a3c2408a. Only headers are used; the native library links to the original firmware libcurl. License retained under curl/COPYING.

Be Vietnam Pro, SIL Open Font License 1.1 (no Reserved Font Name), from google/fonts
commit c13390277dfb1fcc7415d84ef7ef9cfc1e52f8c1, `ofl/bevietnampro/`.
- fonts/BeVietnamPro-Regular.ttf SHA256 cd1ef6e9d7db28ad5cdb88a65ccbe693870e60d340b791f349d248342b4fe4c3
- fonts/BeVietnamPro-Medium.ttf SHA256 b60832bfa0fcd015158112c64d7e3fdad3b0c6287d1823f85a3103636e845268
- fonts/BeVietnamPro-OFL.txt (upstream OFL.txt) SHA256 6b7f8f73609a25ea78c891e34cf37b06f8a676b7ea986e941e43b009110f2a85
`scripts/build_font.py` checks the font digests. Only its glyph outlines are merged into the user's own firmware font at build time.
