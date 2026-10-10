#ifndef ND_ABI_H
#define ND_ABI_H
#include <stddef.h>
#include <stdint.h>

/* Minimal ABI recovered from M1 Plus V2.1 demo. Toolkit objects stay opaque. */
typedef struct nd_widget widget_t;
typedef struct nd_event event_t;
typedef struct nd_idle_info idle_info_t;
typedef struct nd_timer_info timer_info_t;
typedef int ret_t;
typedef ret_t (*event_cb)(void *, event_t *);
typedef ret_t (*idle_cb)(const idle_info_t *);
typedef ret_t (*timer_cb)(const timer_info_t *);
enum { ND_OK = 0, ND_REMOVE = 7, ND_REPEAT = 8,
       ND_EVT_DESTROY = 12, ND_EVT_CLICK = 0x10c };

/* Cstl registered stSongInfo: 0x58 bytes on MIPS o32. Strings are deep copied. */
typedef struct nd_song {
  int32_t id;
  char *extra_id, *filename, *url, *title, *album, *artist, *genre;
  char *composer, *albumartist, *cover, *extra;
  int32_t year, item_kind, source_type, track, disc, mqa;
  int32_t start_time, end_time, create_time;
  char initial;
  char padding[3];
} nd_song;
#if UINTPTR_MAX == 0xffffffffU
typedef char nd_song_size_must_be_88[(sizeof(nd_song) == 88) ? 1 : -1];
typedef char nd_song_url_must_be_12[(offsetof(nd_song, url) == 12) ? 1 : -1];
typedef char nd_song_time_must_be_72[(offsetof(nd_song, start_time) == 72) ? 1 : -1];
#endif

typedef struct nd_api {
  void *(*main_loop)(void);
  ret_t (*idle_queue)(idle_cb, void *);
  uint32_t (*timer_add)(timer_cb, void *, uint32_t);
  widget_t *(*window_manager)(void);
  widget_t *(*window_create)(widget_t *, int32_t, int32_t, int32_t, int32_t);
  widget_t *(*view_create)(widget_t *, int32_t, int32_t, int32_t, int32_t);
  widget_t *(*button_create)(widget_t *, int32_t, int32_t, int32_t, int32_t);
  widget_t *(*label_create)(widget_t *, int32_t, int32_t, int32_t, int32_t);
  void *(*widget_factory)(void);
  widget_t *(*widget_factory_create_widget)(void *, const char *, widget_t *,
                                           int32_t, int32_t, int32_t, int32_t);
  widget_t *(*widget_lookup)(widget_t *, const char *, int);
  int32_t (*widget_count_children)(widget_t *);
  ret_t (*slide_indicator_set_max)(widget_t *, uint32_t);
  uint32_t (*widget_on)(widget_t *, uint32_t, event_cb, void *);
  ret_t (*widget_set_name)(widget_t *, const char *);
  ret_t (*widget_set_text_utf8)(widget_t *, const char *);
  ret_t (*widget_get_text_utf8)(widget_t *, char *, uint32_t);
  ret_t (*widget_set_prop_str)(widget_t *, const char *, const char *);
  int32_t (*widget_get_prop_int)(widget_t *, const char *, int32_t);
  ret_t (*widget_set_enable)(widget_t *, int);
  ret_t (*widget_set_visible)(widget_t *, int, ...);
  ret_t (*widget_use_style)(widget_t *, const char *);
  int (*widget_is_style_exist)(widget_t *, const char *, const char *);
  ret_t (*widget_destroy_children)(widget_t *);
  ret_t (*widget_destroy)(widget_t *);
  ret_t (*window_close)(widget_t *);
  ret_t (*edit_set_keyboard)(widget_t *, const char *);
  ret_t (*edit_set_tips)(widget_t *, const char *);
  void *(*create_deque)(const char *);
  void (*deque_init)(void *);
  void (*deque_push_back)(void *, ...);
  unsigned int (*deque_size)(const void *);
  void (*deque_destroy)(void *);
  void (*mclLoadPlayList)(void *, int, int);
  void (*mclSetStartSeekTime)(int);
  int (*start_playqueue)(int);
  /* Optional: absence only loses list relayout or the now-playing shortcut. */
  ret_t (*widget_layout)(widget_t *);
  ret_t (*navigator_switch_to_with_context)(const char *, void *, int);
} nd_api;

/* Stack context of the stock "back to player" button at 0x4cd58c: {NULL, 0, 255, 2}.
   Tidal playback stores a queue/index in the first fields; the button leaves them empty. */
typedef struct nd_player_context {
  void *queue;
  int32_t index, source, mode;
} nd_player_context;
#if UINTPTR_MAX == 0xffffffffU
typedef char nd_player_context_must_be_16[(sizeof(nd_player_context) == 16) ? 1 : -1];
#endif

/* Called only on the GUI thread. Returns -1 if the shipped ABI is unavailable. */
__attribute__((visibility("default"))) int nd_ui_start(void);
/* Internal startup helper: resolve and post initial installation, no widget access. */
int nd_ui_schedule_start(void);
#endif
