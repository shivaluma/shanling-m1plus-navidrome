# Native Navidrome extension

`nd_ui.c` implements a separate AWTK window using the M1 Plus's exported native
controls and styles. `startup.c` installs it through `__libc_start_main` and a
GUI idle callback. `nd_abi.h` is the minimal recovered V2.1 interface; toolkit
objects remain opaque. Evidence and limitations are in
`../docs/research/native-integration-evidence.md`.

Build the two C files plus `../vendor/cJSON.c` as a MIPS o32 shared object using
the firmware libc/sysroot, `-std=c99 -fPIC -fvisibility=hidden`, and link with
`-Wl,-Bsymbolic -lcurl -ldl -lpthread`. Do not link the packaged `libawtk.so`:
the stock executable supplies toolkit symbols. `-Bsymbolic` keeps vendored
cJSON's internal calls from binding to a different copy inside the stock app.
Only `__libc_start_main` and `nd_ui_start` should be publicly exported.
Widgets reuse stock theme styles by name (`list_item_style`,
`srlabel_item_white_style`, `navbar_btnreturn_style`, `Play_all_style`, …) and
stock images (`icon_artist`, `albumcover_s`, `icon_flac`, `right_arrow`, …). The
compiled bridge origin defaults to `http://127.0.0.1:17831`.

Host tests compile with `ND_HOST_TEST`, which excludes the libc startup hook,
and export mock firmware symbols from the executable. Calling `nd_ui_start()`
on that executable's GUI thread uses the same symbol resolver, timer, controls,
worker, and queue code. It returns `-1` when required symbols are unavailable.
Idle/timer callback arguments are opaque and ignored; mocks need no undocumented
structure offsets. Workers enqueue results using `idle_queue`; the GUI timer
also drains them to tolerate discarded native queue events.

On the device, swipe to the added Navidrome home page and tap Artists, Albums,
Playlists or Search. Lists scroll like stock pages and load 50 rows at a time
("Show more" / "Previous"). Search uses the stock T9 keyboard; the orange tabs
switch between songs, albums and artists. Selecting a song, or "Play all" on an
album or playlist, queues its chunk of up to 128 tracks and opens the stock Now
Playing page; the navigation-bar player icon does the same. The download icon
in an album or playlist header saves it to `/Navidrome` on the SD card (tap
again to cancel; once saved, tap twice to remove). The Downloads tile shows
progress and opens the stock "Update music library" page, which must be run
before saved music appears in the local library. Back restores the
preceding view and closes the window from its first view. The stock player
retains ownership of the copied queue after this window closes.

The plugin is experimental until on-device playback and UI acceptance checks
pass. Successful host tests do not establish hardware behavior.
