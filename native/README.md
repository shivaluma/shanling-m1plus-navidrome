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
Only `__libc_start_main` and `nd_ui_start` should be publicly exported. The
compiled bridge origin defaults to `http://127.0.0.1:17831`.

Host tests compile with `ND_HOST_TEST`, which excludes the libc startup hook,
and export mock firmware symbols from the executable. Calling `nd_ui_start()`
on that executable's GUI thread uses the same symbol resolver, timer, controls,
worker, and queue code. It returns `-1` when required symbols are unavailable.
Idle/timer callback arguments are opaque and ignored; mocks need no undocumented
structure offsets. Workers enqueue results using `idle_queue`; the GUI timer
also drains them to tolerate discarded native queue events.

On the device, swipe to the added Navidrome home page. Browse Artists, Albums or
Playlists; search using the stock T9 keyboard and switch result categories.
Selecting a song queues its album/playlist chunk of up to 128 tracks, beginning
at that song. Previous/Next browse pages; Back restores the preceding view;
Close returns to the stock application. The stock player retains ownership of
the copied queue after this window closes.

The plugin is experimental until on-device playback and UI acceptance checks
pass. Successful host tests do not establish hardware behavior.
