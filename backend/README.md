# Loopback Navidrome backend

Standard-library Go service; bind address is fixed to `127.0.0.1:17831`.
It does not start the firmware player or modify firmware files. The integration
launcher must run it alongside the native interface and arrange playback.

Configuration is read once at startup from `-config` (default
`/mnt/mmc/navidrome/config.json`). Restart after editing it. Required fields:
`server_url`, `username`, `password`. Optional: `allow_http` (false), `ca_file`,
and for offline downloads `offline_dir` (absolute, default `/mnt/mmc/Navidrome`),
`offline_reserve_mb` (free space always kept, default 500), `offline_format` and
`offline_max_bitrate` (empty format = original files; e.g. `"opus"`/`192` asks
Navidrome to transcode).
Use your own Navidrome server URL and supply your account locally. Never put credentials in source or shell arguments.
HTTP servers require explicit `allow_http: true`; HTTPS certificate and hostname
verification remain enabled. A PEM CA file augments platform trust, useful when
the device has no usable CA bundle or the server uses a private CA.

`-ui-dir` defaults to `/mnt/mmc/navidrome/ui` and serves static files at `/`;
an empty string disables it. The service rejects foreign Host and Origin headers.
Native clients should address the exact loopback URL above. No CORS or credential
setup API is exposed.

## API

Responses use JSON, `Cache-Control: no-store`, and snake_case field names.
Failures have `{"error":{"code":"...","message":"..."}}`. Upstream error
messages and authenticated URLs are withheld. Empty lists are `[]`, never null.

| Route | Response |
| --- | --- |
| `GET /v1/status` | `configured`, `server`, `allow_http`, `version` |
| `GET /v1/artists` | `artists` |
| `GET /v1/artist?id=...` | `artist`, `albums` |
| `GET /v1/albums?offset=0&size=30` | `albums`, `offset`, `size` |
| `GET /v1/album?id=...` | `album`, `tracks` |
| `GET /v1/search?q=...&offset=0&size=30` | `artists`, `albums`, `tracks`, `offset`, `size` |
| `GET /v1/playlists` | `playlists` |
| `GET /v1/playlist?id=...` | `playlist`, `tracks` |
| `POST /v1/prepare` with JSON `{"id":"song-id"}` | `url`, `expires_in` |
| `POST /v1/prepare` with JSON `{"ids":["song-id", …]}` (1–128) | `urls` in the same order, `expires_in` |
| `GET` or `HEAD /stream/<capability>` | audio, forwarded range semantics |
| `GET /v1/downloads` | `jobs` (newest first), `free_bytes`, `reserve_bytes`, `folder`, `downloaded_tracks` |
| `POST /v1/downloads` with JSON `{"kind":"album"\|"playlist","id":"..."}` | 202, `job` |
| `POST /v1/downloads/cancel` with the same JSON | `cancelled` |
| `POST /v1/offline/remove` with the same JSON | `removed_files` |

Artists: `id,name,album_count`.
Albums: `id,name,artist,artist_id,song_count,year,duration`.
Tracks: `id,title,artist,album,artist_id,album_id,duration,track,disc,content_type,suffix,size,downloaded`.
`/v1/album` and `/v1/playlist` also return `offline`: `downloaded,total,state,done,error`.
Jobs: `id` (`album:<id>`/`playlist:<id>`), `kind,source_id,name,state` (`queued`,
`active`, `done`, `failed`, `cancelled`, `removed`), `total,done,bytes,total_bytes,current,error`.
Playlists: `id,name,song_count,duration`.

Duration is in seconds. IDs are opaque UTF-8 strings. Query parameters must be
URL encoded. `size` is 1–100; `offset` is 0–1,000,000. Search applies the same
offset/size independently to each result category. Album lists are alphabetical
by name. The full indexed artist list is fetched because getArtists has no paging.

Prepare creates a random 128-bit bearer capability, not an upstream URL. It does
not contact the server or validate song existence; errors are detected at stream
time. Capabilities expire after 12 hours, disappear on restart, and are limited
to 256 (oldest evicted). Treat the returned URL as private and prepare near
playback time. Streams request original media (`format=raw`), forward Range and
If-Range, and preserve 200/206/416 plus Content-Range and related audio headers.
They are copied incrementally; the service does not buffer a whole track.

API requests have a 20-second total timeout and an 8 MiB response cap. Media
requests have connection/header timeouts and no total playback deadline.
Disconnecting the client cancels the upstream request. Cross-origin redirects
are refused for both API and audio to avoid sending authentication elsewhere.
The service never reports playback/scrobbles or rewrites the library. Streaming
never transcodes; offline downloads transcode only when `offline_format` is set.

## Offline downloads

One worker downloads queued albums/playlists a song at a time through Subsonic
`download` (original file), into
`<offline_dir>/<album artist>/<album (year)>/<[disc-]track> <title>.<suffix>`.
Names are NFC, FAT32-safe (no `\ / : * ? " < > |`, no trailing dots/spaces,
at most 100 bytes per component). Each file is written to a hidden
`.<name>.part`, synced, checked against the server's size, then renamed, so the
stock scanner never indexes partial audio. Interrupted files resume with `Range`
(restarting if the server ignores it); network errors are retried three times,
and a stalled transfer is abandoned after 60 seconds. Before each file the
worker checks free space minus the reserve; files over the FAT32 4 GB limit are
refused. If the server disables downloads (Subsonic error 50 / HTTP 403), the
job fails with that message: there is no streaming fallback.

`<offline_dir>/.navidrome-offline.json` records which files this feature
created, their album and owners (`album:<id>`, `playlist:<id>`), created
`cover.jpg` files (JPEG artwork only), playlist `.m3u8` files under `Playlists/`
and pending jobs, which resume after a restart. Existing files are never
overwritten: a clashing name gets " (2)". Removing an album deletes all its
downloaded songs; removing a playlist deletes only songs no other download
owns. Empty folders and leftover `.part` files are then pruned. A corrupt
manifest is kept as `.navidrome-offline.json.corrupt`; no audio is deleted.

The stock library is not updated automatically: its "scan specific folders"
path drops the whole song table first. Run the stock "Update music library"
(the UI opens that page) after downloading.

## Validation

`go test -race ./...` and `go vet ./...` pass on the development host. Tests use
local fake Subsonic servers, including TLS and range responses. This verifies
client/proxy behavior, not compatibility with the user's server or device.

Authentication follows the [OpenSubsonic API reference](https://opensubsonic.netlify.app/docs/api-reference/):
fresh random salt and MD5(password + salt), protocol 1.16.1, JSON response.
Search paging follows [search3](https://opensubsonic.netlify.app/docs/endpoints/search3/),
artist indexing follows [getArtists](https://opensubsonic.netlify.app/docs/endpoints/getartists/),
album paging follows [getAlbumList2](https://opensubsonic.netlify.app/docs/endpoints/getalbumlist2/),
and raw-media requests follow [stream](https://opensubsonic.netlify.app/docs/endpoints/stream/).
MD5 is mandated by this protocol; HTTPS supplies transport protection.
