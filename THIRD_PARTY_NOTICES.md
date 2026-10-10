# Third-party notices

Project-authored source is covered by [LICENSE](LICENSE). Third-party source, data and tools retain their own terms.

| Component | Use | Notice/source |
| --- | --- | --- |
| cJSON 1.7.19 | Vendored JSON parser for the native plugin | MIT; [included license](vendor/cJSON-LICENSE), [upstream](https://github.com/DaveGamble/cJSON/tree/v1.7.19) |
| curl 7.73.0 headers | Native compile-time declarations; links to firmware libcurl | [Included curl license](vendor/curl/COPYING), [upstream archive](https://curl.se/download/curl-7.73.0.tar.xz) |
| Mozilla CA bundle, 2026-09-25 | Downloaded local HTTPS trust roots | [curl CA extraction page](https://curl.se/docs/caextract.html); bundle includes its source/license header |
| SquashFS tools 4.7.5 | Downloaded local repacker | GPL-2.0-or-later; [upstream source and license](https://github.com/plougher/squashfs-tools/tree/4.7.5) |
| GCC cross-toolchain 15.2.0 | Downloaded local build tool, not a firmware payload | [Upstream packaging and notices](https://github.com/messense/homebrew-macos-cross-toolchains) |
| Go and Python analysis libraries | Build/test tools | Their respective upstream licenses; pinned in `requirements-dev.txt` and module metadata |

Shanling firmware, its application, kernel and extracted libraries are supplied separately by each user. They are excluded from this source publication and are not relicensed by the project. Shanling and Navidrome names identify interoperability targets; no affiliation or endorsement is implied.
