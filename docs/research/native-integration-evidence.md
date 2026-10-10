# Native integration evidence — Shanling M1 Plus V2.1

This is static analysis of the supplied firmware, not an observation of execution
on a player. The implementation is experimental until the device acceptance
checks pass. No firmware binary was executed during this investigation.

Analysis paths in this document are relative to the project root. Target:
`.work/analysis-v2.1/rootfs/release/bin/demo`, ELF32 little-endian MIPS,
SHA-256 `29e23119bc2a0f153f3ea0ba942a39754d1aab0879c1040fae2269df8bd9b3f1`.
Addresses below are virtual addresses in this exact executable. Evidence is in
`.work/analysis-v2.1/symbols/release__bin__demo.dynsym.json` and the named function
files under `.work/analysis-v2.1/disassembly/`. Disassembly annotations are local
syntactic candidates; raw instructions, call setup, and delay slots were checked
for the conclusions below. They do not constitute recovered control-flow graphs.

## GUI initialization and thread handoff

Observed:

| Function | Address | Relevant behavior |
| --- | --- | --- |
| `main` | `0x470280` | `tk_init` with 320×480 defaults, assets and extended-widget initialization, `application_init` at `0x470440`, then `tk_run` at `0x470450`. |
| `application_init` | `0x5485a0` | Registers table view and gesture widgets; runs `platform_init`, opens `system_bar`, loads configuration, opens `home_page`. |
| `platform_init` | `0x50b8a4` | Calls `initMclModule` at `0x50bc64` and `curl_init` at `0x50bea8` before returning. |
| `curl_init` | `0x4e87ec` | Calls imported `curl_global_init(3)` at `0x4e8808`. |
| `main_loop_init` | `0x637250` | Linux framebuffer `/dev/fb0`, raw native window, `main_loop_simple_init`; no SDL startup boundary. |
| `main_loop` | `0x658ee4` | Returns the current main-loop pointer. |
| `idle_queue` | `0x654388` | Forwards `(callback, context, NULL, NULL)` to `idle_queue_ex`. |
| `idle_queue_ex` | `0x654330` | Constructs event type `0x1000`; posts through `main_loop_queue_event`. |
| `main_loop_queue_event` | `0x658f18` | Validates loop, event and loop queue-function pointer; invokes the queue function at loop offset `0x18`. |
| `main_loop_simple_init` | `0x693eac` | Creates queue capacity 20, installs mutex at offset `0x54`, queue implementation at offset `0x18`, then publishes the main loop. |
| unnamed queue implementation | `0x6937a8` | Locks the loop mutex, sends the event, and on full queue removes the oldest event before retrying; unlocks afterwards. |
| unnamed GUI step | `0x6938cc` | Receives events; type `0x1000` branch at `0x693a54` schedules the callback using `idle_add`. |
| `timer_add` | `0x663530` | Exported native timer API. |

The plugin therefore waits for non-null `main_loop()` in a bounded startup
worker, posts one `idle_queue` installation callback, and only creates timers and
widgets on the GUI thread. This avoids interposing executable-internal direct
calls to `application_init` or `tk_run`. A timer also drains completed network
results because a full stock event queue can discard an older posted event.

The Linux `__libc_start_main` interposer replaces the main callback, removes
`LD_PRELOAD` before invoking the original main, and starts the bounded waiter.
Removal prevents subsequently launched `hciplayer` and helper processes from
loading the application plugin. No firmware instruction patch is required.

The first network operation is initiated after installation in `tk_run`; stock
`platform_init` has already called `curl_global_init`. The plugin does not call
old libcurl's process-global initializer concurrently with stock threads.

## Minimal AWTK interface

The executable defines its AWTK functions directly. Its `DT_NEEDED` does not
include packaged `libawtk.so`, `libtkc.so`, or `libtable_view.so`; replacing those
libraries alone is not an established injection strategy.

Exported interfaces used by the plugin include `window_create`, `window_close`,
`window_manager`, `view_create`, `button_create`, `label_create`,
`widget_factory`, `widget_factory_create_widget`, `widget_lookup`, `widget_on`,
`widget_destroy`, `widget_destroy_children`, text/property getters and setters,
`widget_use_style`, `widget_is_style_exist`, `edit_set_keyboard`, `edit_set_tips`,
`idle_queue`, and `timer_add`. `edit_create`, `widget_set_style_int`, and
`widget_set_style_color` are **not** exported in this executable. Edits are
created through the exported widget factory; style properties use
`widget_set_prop_str`.

The native header keeps widget, event, idle-info and timer-info objects opaque.
Callbacks do not dereference their fields. Event callbacks receive `(void *ctx,
event_t *)`; idle/timer callbacks receive their respective info pointer. Native
return values used are `RET_OK=0`, `RET_REMOVE=7`, `RET_REPEAT=8`.
`EVT_CLICK=0x10c` is checked against the actual search-page registration at
`0x543744`; `EVT_DESTROY=12` agrees with the compatible upstream event enum.

Interface crosschecks use the AWTK upstream commit
`b5dd398523c34a4464ae6416e7adde454eadb59f`:
[widget interface](https://github.com/zlgopen/awtk/blob/b5dd398523c34a4464ae6416e7adde454eadb59f/src/base/widget.h),
[widget factory](https://github.com/zlgopen/awtk/blob/b5dd398523c34a4464ae6416e7adde454eadb59f/src/base/widget_factory.h),
[event callbacks and enum](https://github.com/zlgopen/awtk/blob/b5dd398523c34a4464ae6416e7adde454eadb59f/src/tkc/event.h),
[idle queue](https://github.com/zlgopen/awtk/blob/b5dd398523c34a4464ae6416e7adde454eadb59f/src/base/idle.h),
[timer](https://github.com/zlgopen/awtk/blob/b5dd398523c34a4464ae6416e7adde454eadb59f/src/base/timer.h),
[edit keyboard](https://github.com/zlgopen/awtk/blob/b5dd398523c34a4464ae6416e7adde454eadb59f/src/widgets/edit.h).
This commit is an interface comparison, **not** an identified firmware toolkit
version. The firmware's June 26, 2025 build string does not prove the vendored
AWTK revision.

The shipped `home_page.bin` has `home_sview`, a 364px slide view, a slide
indicator, and a now-playing strip starting at y=384 in a 450px content area.
The extension adds one named page to that slide view instead of covering native
tiles. The extra page's button opens a separate native window. The standard
system bar remains outside that window. Native home-page builder `0x534064`
explicitly calls `slide_indicator_set_max` (`0x616b70`) after creating its pages.
The extension likewise sets the indicator maximum from exported
`widget_count_children` (`0x669f00`) after inserting a page; it does not assume
dynamic children automatically update the manually assigned maximum. Device
validation must still confirm gesture behavior after dynamic page insertion.

## Stock page construction reused by the interface

Decoded `ui/*.bin` (AWTK UI binary: magic `0x11221212`, 32-byte type, four
int32 rectangle fields, NUL-separated properties) and `styles/default.bin`
(theme magic `0xFAFBFCFD`, 100-byte index entries) give the stock page shapes:
a 320×50 `navigationbar_view`, then a list view at y=50 with 60 px items and a
10 px `scroll_bar_m`. Builders confirm how rows are filled at runtime:

| Function | Address | Observed construction |
| --- | --- | --- |
| `playlist_listitem_layout` | `0x53e44c` | `list_item_style`; `icon_playlist` image; `hscroll_label` with `srlabel_item_white_style`, ellipses and speed; `right_arrow` image. |
| `album_listitem_layout` | `0x524228` | `albumcover_s` cover image; `btn_rightarrow_style`; `list_item_style`. |
| `music_create_tablerow` | `0x528560` | `btn_tablerow_style` row; `img_icon`; title `srlabel_item_white_style`; detail `srlabel_item_gray26_style`. |
| `tidal_tidal_top_init` | `0x4c6808` | Navigation bar: search button (224,0,60,50) `search_style`; back-to-player (270,0,60,50) `navbar_btnbackplayer_style`; title `hscroll_label` (36,14,200,22) `srlabel_leftwhite22_style`; return button (0,0,80,50) `navbar_btnreturn_style`, created last. |
| unnamed home tile builder | `0x533d8c` | 154×118 tile views; 66×66 image at (44,20) styled `home_%s_style`; label (0,92,154,36) `label_centerwhitehome_white`. |
| `on_backtoplayer_onclick` | `0x4cd58c` | Stack context `{0, 0, 255, 2}`; direct call to `navigator_switch_to_with_context("playing_page", &context, 0)` at `0x4d1f4c`. |

`navigator_switch_to_with_context` switches to an open `playing_page` window or
otherwise opens it through `navigator_to_with_context` (`0x4d1d48`). The
extension issues the identical request after queueing; the stock Now Playing
page is therefore the same page the stock button opens. Its behavior over a
non-navigator extension window is a device-test item.

The stock executable imports `font_loader_stb` and the theme names only one
font, `default`. `default.ttf` (SHA-256
`9a16af0aead992a2ad64f86d0de8d38bea60d6f605d42ffd0501684666161872`) is a
Microsoft YaHei + Arial merge at 256 units/em with no TrueType instructions and
lacks 49 Vietnamese letters. See [architecture](../architecture.md#ui-font).

## Library scanning (offline downloads)

| Function | Address | Observed behavior |
| --- | --- | --- |
| `scanAllMusicFile` | `0x4e9df4` | `toolsInitDatabase("/mnt/data/database.db", "songtable")`, `toolsClearDbCount`, `toolsLoadAllFile`, `toolsDatabaseCommit`. |
| `scanSpecFolder` | `0x4e9e90` | Same initialization, then loads only the selected folders (`deque_at` + `toolsLoadAllFile`). |
| `toolsInitDatabase` | `0x5ed734` | Checks `sqlite_master` for the table and executes `drop table %s` before recreating it. |
| `on_startscan_onclick` | `0x4ac9f8` | Builds a `char*` deque of folders and opens `dialog/scanmusic_dialog`, which stops the player. |
| `navigator_to` | `0x4d1d80` | Tail call to `navigator_to_with_context(name, NULL)`. |

A folder-only scan therefore rebuilds the song table from those folders alone. The extension does not start scans; it opens `systemset/updatemusic_page` (UI `ui/systemset/updatemusic_page.bin`: auto-update toggle, "update now", "specified folders") for the user.

## Playback insertion ABI

Recovered C ABI, limited to the arguments and effects used here:

```c
void * _create_deque(const char *registered_type_name);
void deque_init(void *deque);
void _deque_push_back(void *deque, ...); /* stSongInfo argument is a record pointer */
unsigned int deque_size(const void *deque); /* 32-bit size_t on the device */
void deque_destroy(void *deque);
void mclLoadPlayList(void *source_deque, int index, int playtype);
void mclSetStartSeekTime(int time); /* unit unknown; extension uses zero only */
int start_playqueue(int index);
int mclStartPlayer(void);
int mcl_tidalStartPlayer(const char *url);
```

| Function | Address | Evidence and limitations |
| --- | --- | --- |
| `mclLoadPlayList` | `0x5d1f28` | Clears stock queue, `deque_assign`s source queue, saves index/playtype, rebuilds random-order indices. Argument `a1` is saved at `0x5d1f54` and stored at `0x5d1f8c` to global-base minus `0x5424`, the same location read by `mclGetPlayPos` and written by `mclSetPlayPos`. Stock `refresh_playqueue` passes zero or size minus one. No meaningful return is consumed by native callers. |
| `mclSetStartSeekTime` | `0x5d4480` | Stores its integer argument; zero is sufficient for starting a new stream. |
| `mclSetPlayPos` | `0x5d42d8` | Stores playback index. |
| `start_playqueue` | `0x4e47d0` | Stops AirPlay, restores output channel, sets index, calls `mclStartPlayer`, loads metadata and emits stock playback/Bluetooth/lyrics notifications. Returns 1 independently of the engine's later success. |
| `mclStartPlayer` | `0x5d5300` | Bounds-checks queue index. Types `0xf0/0xf1` invoke vendor-service callbacks. Default branch, including playtype zero, reads URL at record offset `0x0c` and starts it directly. |
| `mcl_tidalStartPlayer` | `0x5d5184` | Accepts URL in `a0`, copies it into a 1536-byte stack buffer, uses pending seek or current record time. Null URL is unsafe; a direct call alone does not establish stock queue/metadata. Not used by the plugin. |
| `get_id3info` | `0x4dfd58` | Type zero uses record metadata rather than the type-one local TagLib path. Copies URL into a 1024-byte destination; metadata strings use bounded formatting. |

The chosen sequence is:

```c
mclLoadPlayList(temporary_queue, selected_index, 0);
deque_destroy(temporary_queue);
mclSetStartSeekTime(0);
start_playqueue(selected_index);
```

This is a static candidate for coherent URL playback and stock next/previous
navigation. Hardware testing must establish actual stream opening, codec
support, seeking, automatic advancement, stock now-playing behavior and return
to local playback. The UI says “Queued tracks”, because the vendor return value
does not confirm audible playback.

## `stSongInfo` layout and ownership

The registered type name is at `0x745a28`. The registration routine at
`0x5dbbf0` supplies size `0x58` at `0x5dbc44`; initializer `0x5dad5c` zeroes the
record. Copy callback `0x5db54c` deep-copies all string pointer fields and copies
scalar fields. Destructor `0x5dabfc` frees pointer fields. `toolsFreeStSongInfo`
at `0x5e6dc4` independently agrees with string-member offsets. SQL row callback
`0x4e9058` establishes the metadata mapping.

| Offset | Field | Confidence/use |
| --- | --- | --- |
| `0x00` | 32-bit ID | Observed scalar; extension uses a deterministic negative hash, excludes sentinel -1, and resolves collisions within the queue chunk. |
| `0x04` | extra string ID | Vendor online-ID usage; null in extension. |
| `0x08` | filename | SQL column 1; title fallback in extension. |
| `0x0c` | playable URL | SQL column 2; loopback capability in extension. |
| `0x10` | title | SQL column 6. |
| `0x14` | album | SQL column 3. |
| `0x18` | artist | SQL column 4, named `songer` by vendor. |
| `0x1c` | genre | SQL column 5. |
| `0x20` | composer | SQL column 7. |
| `0x24` | album artist | SQL column 8, vendor `albumsonger`. |
| `0x28` | cover/extra string | Cover use in nonzero online types; null for extension. |
| `0x2c` | extra string | Semantic name unknown; null in extension. |
| `0x30` | year | 32-bit scalar. |
| `0x34` | item kind | Music rows use 8. |
| `0x38` | source/play type | Zero selects ordinary direct-URL path. |
| `0x3c` | track number | 32-bit scalar. |
| `0x40` | disc number | 32-bit scalar. |
| `0x44` | MQA-related flag | SQL bit extraction; zero in extension. |
| `0x48` | start time | Unit unestablished; zero. |
| `0x4c` | end time | Unit unestablished; zero. |
| `0x50` | creation time | 32-bit scalar; zero. |
| `0x54` | initial character | One byte followed by three padding bytes. |

`_deque_push_back` clones the temporary record's strings; `mclLoadPlayList` clones
the source deque again. Borrowed JSON strings and prepared URLs can therefore be
released after the call and the temporary deque can be destroyed. Compile-time
MIPS32 size/offset assertions are in `native/nd_abi.h`. Host harness layouts follow
host pointer size; they cannot prove target ABI compatibility by themselves.

Null string members are supported: copy callback `0x5db698` loads source offset
4, `0x5db69c` branches over cloning when null, and equivalent tests exist for
the other pointer members through offset `0x2c`. Its destination was zeroed at
`0x5db684–0x5db694`. Destructor `0x5dac20–0x5dad28` tests every pointer before
freeing it. Thus unknown optional strings can remain null.

The playback entry and metadata functions above do not use the numeric ID to
select the stream. Negative synthetic IDs keep extension records separate from
the scanner's ordinary positive database IDs; they are not server IDs. Behavior
of every proprietary persistence/favorites handler has not been established.

## Implementation bounds and remaining unknowns

`native/nd_ui.c` serializes all bridge HTTP work on one worker, cancels stale jobs
by generation, and delivers owned results on the GUI thread. Closing a window
invalidates its generation. Row callback contexts are freed by widget destruction.
No worker retains a widget pointer or a pointer into the GUI's current JSON.

Requests use only the compiled loopback origin `http://127.0.0.1:17831`, with proxy
use disabled, redirects disabled, HTTP protocol restriction, a 2MB response cap,
2-second connect timeout and 12-second request timeout. Playback preparation has
a 30-second total deadline. Prepared URLs must have the exact loopback capability
prefix, 32 lowercase hexadecimal token characters, and fit the tighter 1024-byte
stock metadata URL buffer.

Four native-size rows appear per browse page. Albums/playlists queue a contiguous
chunk of at most 128 tracks containing the selected track; the status identifies
that chunk. Search queues its loaded result page. Artwork integration, gapless
behavior, seek/range interoperability, and recovery after server disconnection
remain device-test questions. There is no binary proof that adding a dynamic
home slide will satisfy every proprietary gesture handler; this is explicitly
included in acceptance testing.
