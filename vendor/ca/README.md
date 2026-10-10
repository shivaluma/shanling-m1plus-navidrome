# CA bundle

Generated locally by `python scripts/bootstrap_dependencies.py`; excluded from Git.

Source: https://curl.se/ca/cacert-2026-09-25.pem

SHA-256: `a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505`

The launcher supplies this bundle through `SSL_CERT_FILE` because the analyzed firmware has no populated CA directory. Certificate and hostname verification remain enabled. See [curl's CA extraction notes](https://curl.se/docs/caextract.html) and the license header in the downloaded bundle.
