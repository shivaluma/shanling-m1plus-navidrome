# Backend evidence — actual Navidrome 0.64.2

Executed on 2026-10-10 on the development Mac (Darwin arm64), using Go 1.24.5,
the official Navidrome release, local ffmpeg, and the current backend source.
This is an actual server integration test. Firmware executables and the M1 Plus
device were not executed by this test.

## Pinned server identity

Downloaded the official
[Navidrome 0.64.2 Darwin arm64 release archive](https://github.com/navidrome/navidrome/releases/download/v0.64.2/navidrome_0.64.2_darwin_arm64.tar.gz).
Its SHA-256 matched the digest supplied by GitHub's
[release API](https://api.github.com/repos/navidrome/navidrome/releases/tags/v0.64.2):

`8b0a7798001453719ad50c12f7025a6044546c4a3568efe51feb296681f29956`

The extracted server reports `0.64.2 (10114574)`. It ran from a temporary
directory, with an independent temporary database and music folder, bound to
an ephemeral port on `127.0.0.1`. External services were disabled. Test server
and backend logs were discarded; temporary credentials, configuration, database
and media were removed after terminating both processes.

The initial admin was created through the release's supported first-user HTTP
route, after reading
[server/server.go](https://github.com/navidrome/navidrome/blob/v0.64.2/server/server.go#L204)
and [server/auth.go](https://github.com/navidrome/navidrome/blob/v0.64.2/server/auth.go).
The test used `POST /auth/createAdmin` with newly generated local credentials.
It did not use the development auto-admin option, whose source logs its password.
No user server credentials were requested or used.

## Real-server results

`backend/e2e_navidrome.py` built the host backend and generated a 2-second FLAC
with ffmpeg. Fixture metadata: artist `宇多田 Test`, album `Requiem café`, title
`光 & Search`, track 1, disc 2. The FLAC was 32,537 bytes, SHA-256:

`b1b7b75c18470d72f9da4102908d3b7f110e7f79501d458d4daa2ee1d32b79c6`

The following checks passed against actual Navidrome responses:

- Initial local admin creation, real library scan and Unicode metadata indexing.
- Backend artist list → artist albums → album tracks, including track/disc normalization.
- Alphabetical album paging: first page contains the fixture; the next is empty.
- `search3` with Unicode query `光`: first page finds the track; next is empty.
- Actual playlist creation through Subsonic, backend playlist listing and playlist tracks.
- Backend prepare produces a random loopback capability URL.
- Full proxied FLAC is byte-for-byte identical to the original file.
- A GET for bytes 17–100 returns 206, the exact 84 expected bytes and matching Content-Range.
- If-Range is forwarded with the real server's ETag when present.
- HEAD with the same Range returns 206, Content-Length 84 and an empty body.

Safe machine-readable results are in `backend/e2e-results.json`. Run again with
a previously verified copy of the pinned host executable:

```sh
cd backend
python3 e2e_navidrome.py --navidrome /absolute/path/to/navidrome --report e2e-results.json
```

The test needs the fixed backend port 17831 free. It refuses to reuse another
service. The script accepts only server version 0.64.2; the operator must verify
the release archive's SHA-256 before providing the executable. A temporary
directory and processes are cleaned up on ordinary success or failure.

## Public server TLS bundle probe

The extracted firmware rootfs has no usable bundled trusted root certificates.
The project supplies Mozilla roots in `vendor/ca/cacert.pem` (data dated
2026-09-25), SHA-256:

`a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505`

An opt-in Go test used **only this certificate pool**, excluding host system and
Keychain roots, to request `https://music.example.org/rest/ping.view`. No username,
password, token, salt, Authorization header, or authentication query parameters
were sent. Result: verified TLS chain and HTTP 200. This establishes public TLS
connectivity with the supplied CA bundle; it does not establish authenticated
library access. The response body was discarded.

```sh
cd backend
NAVIDROME_TLS_PROBE_URL=https://music.example.org/ \
NAVIDROME_CA_BUNDLE=../vendor/ca/cacert.pem \
go test -run '^TestExplicitTLSBundleProbe$' -v .
```

The normal test suite skips this probe unless both explicit environment variables
are present. Device launch must provide the supplied CA bundle through
`SSL_CERT_FILE` or the backend's `ca_file` configuration.

## Device-target backend build

Cross-compiled `dist/navidrome-service` with installed Go 1.24.5:

```sh
cd backend
CGO_ENABLED=0 GOOS=linux GOARCH=mipsle GOMIPS=softfloat \
go build -trimpath -ldflags='-s -w' -o ../dist/navidrome-service .
```

Observed artifact metadata:

| Property | Result |
| --- | --- |
| Size | 7,078,103 bytes |
| SHA-256 | `ff9871261910879a4fc83a6254e5bcb48a0aabae154cedfc143f5120f4799ea6` |
| Format | ELF32, little-endian, `EM_MIPS`, `ET_EXEC` |
| ISA / flags | MIPS32 revision 1; ELF flags `0x50001004` |
| FP ABI | MIPS ABI flag value 3 (soft float); Go build metadata `GOMIPS=softfloat` |
| Entry | `0xb6690` |
| Dynamic loader/dependencies | No PT_INTERP and no `.dynamic` section; statically linked |
| Toolchain metadata | `CGO_ENABLED=0`, `GOOS=linux`, `GOARCH=mipsle`, Go 1.24.5 |

`file`, pyelftools and `go version -m` corroborated these fields. The firmware
userspace is little-endian MIPS32; this build avoids depending on its C runtime
or FPU ABI. Successful target compilation is not evidence of device execution.
Actual boot, memory use, loopback audio decoding, touch UI, suspend/resume and
recovery remain device-validation work.

## Other automated checks

`go test -race ./...` passes, and `go vet ./...` passed. Mock-server tests also
cover malformed/oversized JSON, upstream failure redaction, HTTP opt-in, untrusted
TLS rejection, custom CA success, foreign redirects, partial streaming before
upstream completion, token expiration, session bounds and unsatisfiable ranges.
