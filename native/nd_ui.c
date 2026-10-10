#define _GNU_SOURCE
#define _DARWIN_C_SOURCE
#include "nd_abi.h"
#include "../vendor/cJSON.h"
#include <curl/curl.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef ND_BRIDGE_ORIGIN
#define ND_BRIDGE_ORIGIN "http://127.0.0.1:17831"
#endif
/* Rows per rendered window; the host harness lowers it to exercise paging. */
#ifndef ND_PAGE
#define ND_PAGE 50
#endif
#define ND_QUEUE_MAX 128
#ifndef ND_POLL_SECONDS
#define ND_POLL_SECONDS 2
#endif
#define ND_BODY_MAX (2U * 1024U * 1024U)
#define ND_HISTORY_MAX 32
/* Geometry and colors follow the stock V2.1 pages (album_page.bin, search_page.bin). */
#define ND_NAV_H 50
#define ND_TAB_H 48
#define ND_ROW_H 60
#define ND_LIST_W 310
#define ND_ACCENT "#FE7E40"

enum route { R_ARTISTS, R_ARTIST, R_ALBUMS, R_ALBUM, R_SEARCH, R_LISTS, R_LIST, R_DOWNLOADS };
enum kind { K_ARTISTS, K_ALBUMS, K_TRACKS, K_LISTS, K_JOBS };
/* View loads, playback preparation, download actions and background progress polls. */
enum mode { M_VIEW, M_PLAY, M_POST, M_POLL };
enum action { A_BACK = 1, A_SEARCH_OPEN, A_SEARCH_GO, A_PLAYER, A_PREV, A_NEXT,
              A_RETRY, A_TAB_TRACKS, A_TAB_ALBUMS, A_TAB_ARTISTS, A_PLAY_ALL, A_DOWNLOAD,
              A_LIBRARY };
enum section { S_ARTISTS = 1, S_ALBUMS, S_LISTS, S_SEARCH, S_DOWNLOADS };
typedef struct view_state {
  enum route route;
  enum kind kind;
  unsigned int offset;
  char param[768];
  char title[192];
} view_state;
typedef struct job {
  struct job *next;
  unsigned long generation;
  int mode, selected;
  unsigned int first_track;
  char path[2600];
  char body[1600];
  cJSON *tracks;
  time_t deadline;
} job;
typedef struct result {
  struct result *next;
  unsigned long generation;
  int mode, selected;
  unsigned int first_track;
  cJSON *json;
  cJSON *tracks;
  char **urls;
  unsigned int count;
  char error[160];
} result;
typedef struct row_context {
  unsigned long list;
  int index;
} row_context;
typedef struct buffer { char *data; size_t size; } buffer;

static nd_api api;
static int symbols_resolved, installed, worker_started;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t wake_worker = PTHREAD_COND_INITIALIZER;
static unsigned long generation = 1;
static job *pending_job;
static result *results_head, *results_tail;
/* The fields below are accessed by GUI callbacks only. */
static widget_t *win, *content, *list, *rows_box;
static widget_t *title_label, *search_open_button, *player_button;
static widget_t *search_box, *search_edit, *search_go_button, *search_tabs[3];
static widget_t *status_label, *spinner, *retry_button, *toast;
static view_state history[ND_HISTORY_MAX];
static unsigned int depth;
static cJSON *current_json;
static int busy, spinning, spin_frame;
static time_t busy_deadline, toast_deadline;
static unsigned long list_serial;
/* Offline state of the open album/playlist and the download page's row widgets. */
#define ND_JOBS_MAX 30
typedef struct offline_view { char state[16]; int done, total, downloaded; } offline_view;
static offline_view offline_now;
static widget_t *header_summary, *header_download;
static widget_t *job_detail[ND_JOBS_MAX], *job_icon[ND_JOBS_MAX], *jobs_summary;
static char job_ids[ND_JOBS_MAX][160];
static int job_rows, polling;
static time_t next_poll, poll_deadline, remove_armed;

static ret_t timer_tick(const timer_info_t *unused);
static ret_t deliver_results(const idle_info_t *unused);
static ret_t on_action(void *ctx, event_t *event);
static ret_t open_window(void *ctx, event_t *event);
static void render_rows(void);
static void fetch_current(void);

static const char *json_string(const cJSON *object, const char *key) {
  const cJSON *v = cJSON_GetObjectItemCaseSensitive(object, key);
  return cJSON_IsString(v) && v->valuestring ? v->valuestring : "";
}
static int json_int(const cJSON *object, const char *key) {
  const cJSON *v = cJSON_GetObjectItemCaseSensitive(object, key);
  return cJSON_IsNumber(v) ? v->valueint : 0;
}
static void copy_text(char *dst, size_t size, const char *src) {
  if (size) snprintf(dst, size, "%s", src ? src : "");
}
static unsigned long epoch(void) {
  unsigned long value;
  pthread_mutex_lock(&lock);
  value = generation;
  pthread_mutex_unlock(&lock);
  return value;
}
static void free_job(job *j) {
  if (!j) return;
  cJSON_Delete(j->tracks);
  free(j);
}
static unsigned long cancel_previous(void) {
  job *old;
  unsigned long value;
  pthread_mutex_lock(&lock);
  value = ++generation;
  old = pending_job;
  pending_job = NULL;
  pthread_mutex_unlock(&lock);
  free_job(old);
  return value;
}
static void free_result(result *r) {
  unsigned int i;
  if (!r) return;
  cJSON_Delete(r->json);
  cJSON_Delete(r->tracks);
  if (r->urls) for (i = 0; i < r->count; ++i) free(r->urls[i]);
  free(r->urls);
  free(r);
}
static int resolve_api(void) {
#define LOAD(field, name) do { \
    void *p = dlsym(RTLD_DEFAULT, name); \
    if (!p) { fputs("Navidrome: required firmware symbol missing: " name "\n", stderr); return -1; } \
    memcpy(&api.field, &p, sizeof(api.field)); \
  } while (0)
#define OPTIONAL(field, name) do { \
    void *p = dlsym(RTLD_DEFAULT, name); \
    memcpy(&api.field, &p, sizeof(api.field)); \
  } while (0)
  if (symbols_resolved) return 0;
  LOAD(main_loop, "main_loop");
  LOAD(idle_queue, "idle_queue");
  LOAD(timer_add, "timer_add");
  LOAD(window_manager, "window_manager");
  LOAD(window_create, "window_create");
  LOAD(view_create, "view_create");
  LOAD(button_create, "button_create");
  LOAD(label_create, "label_create");
  LOAD(widget_factory, "widget_factory");
  LOAD(widget_factory_create_widget, "widget_factory_create_widget");
  LOAD(widget_lookup, "widget_lookup");
  LOAD(widget_count_children, "widget_count_children");
  LOAD(slide_indicator_set_max, "slide_indicator_set_max");
  LOAD(widget_on, "widget_on");
  LOAD(widget_set_name, "widget_set_name");
  LOAD(widget_set_text_utf8, "widget_set_text_utf8");
  LOAD(widget_get_text_utf8, "widget_get_text_utf8");
  LOAD(widget_set_prop_str, "widget_set_prop_str");
  LOAD(widget_get_prop_int, "widget_get_prop_int");
  LOAD(widget_set_enable, "widget_set_enable");
  LOAD(widget_set_visible, "widget_set_visible");
  LOAD(widget_use_style, "widget_use_style");
  LOAD(widget_is_style_exist, "widget_is_style_exist");
  LOAD(widget_destroy_children, "widget_destroy_children");
  LOAD(widget_destroy, "widget_destroy");
  LOAD(window_close, "window_close");
  LOAD(edit_set_keyboard, "edit_set_keyboard");
  LOAD(edit_set_tips, "edit_set_tips");
  LOAD(create_deque, "_create_deque");
  LOAD(deque_init, "deque_init");
  LOAD(deque_push_back, "_deque_push_back");
  LOAD(deque_size, "deque_size");
  LOAD(deque_destroy, "deque_destroy");
  LOAD(mclLoadPlayList, "mclLoadPlayList");
  LOAD(mclSetStartSeekTime, "mclSetStartSeekTime");
  LOAD(start_playqueue, "start_playqueue");
  OPTIONAL(widget_layout, "widget_layout");
  OPTIONAL(navigator_switch_to_with_context, "navigator_switch_to_with_context");
  OPTIONAL(navigator_to, "navigator_to");
  symbols_resolved = 1;
  return 0;
#undef LOAD
#undef OPTIONAL
}

static size_t receive_body(char *data, size_t size, size_t count, void *ctx) {
  buffer *b = (buffer *)ctx;
  size_t n;
  char *p;
  if (size && count > ND_BODY_MAX / size) return 0;
  n = size * count;
  if (n > ND_BODY_MAX - b->size) return 0;
  p = (char *)realloc(b->data, b->size + n + 1);
  if (!p) return 0;
  b->data = p;
  memcpy(p + b->size, data, n);
  b->size += n;
  p[b->size] = 0;
  return n;
}
static int request_progress(void *ctx, double a, double b, double c, double d) {
  const job *j = (const job *)ctx;
  (void)a; (void)b; (void)c; (void)d;
  return epoch() != j->generation || time(NULL) >= j->deadline;
}
static cJSON *request_json(job *j, const char *path, const char *post, char *error) {
  CURL *curl;
  struct curl_slist *headers = NULL;
  buffer body = {NULL, 0};
  cJSON *parsed = NULL;
  CURLcode code;
  long status = 0;
  char url[2800];
  if (request_progress(j, 0, 0, 0, 0)) return NULL;
  if (path[0] != '/' || snprintf(url, sizeof(url), "%s%s", ND_BRIDGE_ORIGIN, path) >= (int)sizeof(url)) {
    copy_text(error, 160, "Request is too long"); return NULL;
  }
  curl = curl_easy_init();
  if (!curl) { copy_text(error, 160, "Network unavailable"); return NULL; }
  curl_easy_setopt(curl, CURLOPT_URL, url);
  curl_easy_setopt(curl, CURLOPT_PROXY, "");
  curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
  curl_easy_setopt(curl, CURLOPT_PROTOCOLS, (long)CURLPROTO_HTTP);
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 2L);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, 12L);
  curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, receive_body);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &body);
  curl_easy_setopt(curl, CURLOPT_NOPROGRESS, 0L);
  curl_easy_setopt(curl, CURLOPT_PROGRESSFUNCTION, request_progress);
  curl_easy_setopt(curl, CURLOPT_PROGRESSDATA, j);
  if (post) {
    headers = curl_slist_append(NULL, "Content-Type: application/json");
    if (!headers) { copy_text(error, 160, "Not enough memory"); goto done; }
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, post);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, (long)strlen(post));
  }
  code = curl_easy_perform(curl);
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
  if (code != CURLE_OK) {
    copy_text(error, 160, "Bridge unavailable; check connection"); goto done;
  }
  if (status < 200 || status > 299) {
    /* The bridge's own messages are safe to show: they never contain upstream text. */
    cJSON *reply = body.data ? cJSON_ParseWithLength(body.data, body.size) : NULL;
    const char *message = json_string(cJSON_GetObjectItemCaseSensitive(reply, "error"), "message");
    if (message[0]) snprintf(error, 160, "%s", message);
    else snprintf(error, 160, "Bridge error %ld; retry", status);
    cJSON_Delete(reply);
    goto done;
  }
  parsed = body.data ? cJSON_ParseWithLength(body.data, body.size) : NULL;
  if (!cJSON_IsObject(parsed)) {
    cJSON_Delete(parsed); parsed = NULL;
    copy_text(error, 160, "Invalid bridge response");
  }
done:
  curl_slist_free_all(headers);
  curl_easy_cleanup(curl);
  free(body.data);
  return parsed;
}
static void prepare_tracks(job *j, result *r) {
  unsigned int i;
  const size_t prefix_size = sizeof(ND_BRIDGE_ORIGIN "/stream/") - 1;
  r->tracks = j->tracks;
  j->tracks = NULL;
  r->count = (unsigned int)cJSON_GetArraySize(r->tracks);
  r->urls = (char **)calloc(r->count, sizeof(char *));
  if (!r->urls) { copy_text(r->error, 160, "Not enough memory"); return; }
  for (i = 0; i < r->count; ++i) {
    cJSON *track = cJSON_GetArrayItem(r->tracks, (int)i);
    cJSON *payload = cJSON_CreateObject(), *reply;
    char *post;
    const char *url;
    if (request_progress(j, 0, 0, 0, 0)) {
      copy_text(r->error, 160, "Preparation cancelled or timed out"); return;
    }
    if (!payload || !cJSON_AddStringToObject(payload, "id", json_string(track, "id"))) {
      cJSON_Delete(payload); copy_text(r->error, 160, "Not enough memory"); return;
    }
    post = cJSON_PrintUnformatted(payload);
    cJSON_Delete(payload);
    if (!post) { copy_text(r->error, 160, "Not enough memory"); return; }
    reply = request_json(j, "/v1/prepare", post, r->error);
    free(post);
    if (!reply) return;
    url = json_string(reply, "url");
    /* Player gets a loopback capability only; never server credentials/URLs. */
    if (strncmp(url, ND_BRIDGE_ORIGIN "/stream/", prefix_size) ||
        strlen(url) >= 1024 || strlen(url + prefix_size) != 32 ||
        strspn(url + prefix_size, "0123456789abcdef") != 32) {
      cJSON_Delete(reply); copy_text(r->error, 160, "Invalid stream address"); return;
    }
    r->urls[i] = strdup(url);
    cJSON_Delete(reply);
    if (!r->urls[i]) { copy_text(r->error, 160, "Not enough memory"); return; }
  }
}
static void *network_worker(void *unused) {
  (void)unused;
  for (;;) {
    job *j;
    result *r;
    pthread_mutex_lock(&lock);
    while (!pending_job) pthread_cond_wait(&wake_worker, &lock);
    j = pending_job;
    pending_job = NULL;
    pthread_mutex_unlock(&lock);
    r = (result *)calloc(1, sizeof(*r));
    if (!r) { /* GUI timer recovers after the bounded operation deadline. */ free_job(j); continue; }
    r->generation = j->generation;
    r->mode = j->mode;
    r->selected = j->selected;
    r->first_track = j->first_track;
    if (j->mode == M_PLAY) prepare_tracks(j, r);
    else r->json = request_json(j, j->path, j->mode == M_POST ? j->body : NULL, r->error);
    if (r->mode != M_PLAY && !r->json && !r->error[0]) copy_text(r->error, 160, "Request cancelled or timed out");
    free_job(j);
    pthread_mutex_lock(&lock);
    if (r->generation == generation) {
      if (results_tail) results_tail->next = r;
      else results_head = r;
      results_tail = r;
      pthread_mutex_unlock(&lock);
      /* Timer also drains results if the firmware's bounded queue drops an event. */
      api.idle_queue(deliver_results, NULL);
    } else {
      pthread_mutex_unlock(&lock);
      free_result(r);
    }
  }
  return NULL;
}


/* ---- Widgets: stock V2.1 styles, with inline fallbacks for missing theme entries. ---- */
static void prop(widget_t *w, const char *key, const char *value) {
  if (w) api.widget_set_prop_str(w, key, value);
}
static void state_props(widget_t *w, const char *key, const char *value) {
  static const char *states[] = {"normal", "pressed", "over", "focused", "disable",
                                 "empty", "empty_focus", "empty_over", "changed", "error"};
  char name[64];
  size_t i;
  for (i = 0; i < sizeof(states) / sizeof(states[0]); ++i) {
    snprintf(name, sizeof(name), "style:%s:%s", states[i], key);
    prop(w, name, value);
  }
}
static int native_style(widget_t *w, const char *name) {
  if (!w || !name || !api.widget_is_style_exist(w, name, "normal")) return 0;
  api.widget_use_style(w, name);
  return 1;
}
static void text_style(widget_t *w, const char *name, const char *color, const char *size) {
  if (native_style(w, name)) return;
  prop(w, "style:normal:text_color", color);
  prop(w, "style:normal:font_size", size);
}
static widget_t *make(const char *type, widget_t *parent, int x, int y, int width, int height,
                      const char *name) {
  widget_t *w = api.widget_factory_create_widget(api.widget_factory(), type, parent, x, y, width, height);
  if (w && name) api.widget_set_name(w, name);
  return w;
}
static widget_t *make_button(widget_t *parent, int x, int y, int width, int height,
                             const char *name, const char *text, const char *style,
                             enum action action) {
  widget_t *w = api.button_create(parent, x, y, width, height);
  if (!w) return NULL;
  api.widget_set_name(w, name);
  if (text) api.widget_set_text_utf8(w, text);
  if (!native_style(w, style)) {
    state_props(w, "bg_color", "#131313");
    state_props(w, "border_color", "#00000000");
    state_props(w, "text_color", "#FFFFFF");
  }
  if (action && !api.widget_on(w, ND_EVT_CLICK, on_action, (void *)(uintptr_t)action)) return NULL;
  return w;
}
/* Single-line text: ellipsized, scrolls only when focused, like stock list rows. */
static widget_t *make_text(widget_t *parent, int x, int y, int width, int height, const char *name,
                           const char *text, const char *style, const char *color, const char *size) {
  widget_t *w = make("hscroll_label", parent, x, y, width, height, name);
  if (!w) return NULL;
  text_style(w, style, color, size);
  prop(w, "ellipses", "true");
  prop(w, "only_focus", "true");
  api.widget_set_text_utf8(w, text);
  return w;
}
static widget_t *make_label(widget_t *parent, int x, int y, int width, int height, const char *name,
                            const char *text, const char *style, const char *color, const char *size) {
  widget_t *w = api.label_create(parent, x, y, width, height);
  if (!w) return NULL;
  if (name) api.widget_set_name(w, name);
  text_style(w, style, color, size);
  api.widget_set_text_utf8(w, text);
  return w;
}
static widget_t *make_image(widget_t *parent, int x, int y, int width, int height, const char *name,
                            const char *image, const char *draw_type) {
  widget_t *w = make("image", parent, x, y, width, height, name);
  if (!w) return NULL;
  prop(w, "image", image);
  prop(w, "draw_type", draw_type);
  return w;
}

/* ---- Status, spinner and toast. ---- */
static int spin_timer;
static void hide_status(void) {
  api.widget_set_visible(status_label, 0);
  api.widget_set_visible(spinner, 0);
  api.widget_set_visible(retry_button, 0);
  spinning = 0;
}
static ret_t spin_tick(const timer_info_t *unused) {
  char frame[24];
  (void)unused;
  if (!win || !spinning) { spin_timer = 0; return ND_REMOVE; }
  spin_frame = spin_frame % 12 + 1;
  snprintf(frame, sizeof(frame), "imge_wait%d", spin_frame);
  prop(spinner, "image", frame);
  return ND_REPEAT;
}
static void show_status(const char *text, int retry, int spin) {
  if (!win) return;
  api.widget_set_text_utf8(status_label, text);
  api.widget_set_visible(status_label, 1);
  api.widget_set_visible(retry_button, retry);
  api.widget_set_visible(spinner, spin);
  spinning = spin;
  if (spin && !spin_timer && api.timer_add(spin_tick, NULL, 90)) spin_timer = 1;
}
static void notify(const char *text, int seconds) {
  if (!win) return;
  api.widget_set_text_utf8(toast, text);
  api.widget_set_visible(toast, 1);
  toast_deadline = time(NULL) + seconds;
}
static void show_player(void) {
  nd_player_context context = {NULL, 0, 255, 2};
  /* Same request as the stock navigation-bar "back to player" button. */
  if (api.navigator_switch_to_with_context)
    api.navigator_switch_to_with_context("playing_page", &context, 0);
}

/* ---- Content. ---- */
static cJSON *items(void) {
  const char *keys[] = {"artists", "albums", "tracks", "playlists", "jobs"};
  cJSON *a = cJSON_GetObjectItemCaseSensitive(current_json, keys[history[depth].kind]);
  return cJSON_IsArray(a) ? a : NULL;
}
static int remote_paging(void) {
  return history[depth].route == R_ALBUMS || history[depth].route == R_SEARCH;
}
static void destroy_list(void) {
  if (list) api.widget_destroy(list);
  list = rows_box = NULL;
  header_summary = header_download = jobs_summary = NULL;
  job_rows = 0;
  ++list_serial;
}
static int new_list(void) {
  int top = history[depth].route == R_SEARCH ? ND_NAV_H + ND_TAB_H : ND_NAV_H;
  int height = 450 - top;
  destroy_list();
  list = make("list_view", content, 0, top, 320, height, "nd_list");
  if (!list) return -1;
  prop(list, "default_item_height", "60");
  state_props(list, "bg_color", "#000000");
  state_props(list, "border_color", "#00000000");
  prop(list, "style:normal:border_width", "0");
  rows_box = make("scroll_view", list, 0, 0, ND_LIST_W, height, "nd_rows");
  if (!rows_box || !make("scroll_bar_m", list, ND_LIST_W, 0, 320 - ND_LIST_W, height, "nd_scroll")) {
    destroy_list();
    return -1;
  }
  return 0;
}
static void reset_content(void) {
  destroy_list();
  cJSON_Delete(current_json);
  current_json = NULL;
}
static void format_duration(char *out, size_t size, int seconds) {
  if (seconds <= 0) copy_text(out, size, "");
  else if (seconds >= 3600) snprintf(out, size, "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60);
  else snprintf(out, size, "%d:%02d", seconds / 60, seconds % 60);
}
static const char *format_icon(const char *content_type) {
  static const char *map[][2] = {
    {"audio/flac", "icon_flac"}, {"audio/x-flac", "icon_flac"}, {"audio/mpeg", "icon_mp3"},
    {"audio/mp3", "icon_mp3"}, {"audio/aac", "icon_aac"}, {"audio/mp4", "icon_m4a"},
    {"audio/x-m4a", "icon_m4a"}, {"audio/m4a", "icon_m4a"}, {"audio/x-alac", "icon_alac"},
    {"audio/wav", "icon_wav"}, {"audio/x-wav", "icon_wav"}, {"audio/ogg", "icon_ogg"},
    {"audio/opus", "icon_opus"}, {"audio/ape", "icon_ape"}, {"audio/x-ape", "icon_ape"},
    {"audio/aiff", "icon_aiff"}, {"audio/x-aiff", "icon_aiff"}, {"audio/dsf", "icon_dsf"},
    {"audio/x-dsf", "icon_dsf"}, {"audio/x-dff", "icon_dff"}, {"audio/x-ms-wma", "icon_wma"},
  };
  size_t i;
  for (i = 0; i < sizeof(map) / sizeof(map[0]); ++i)
    if (!strcmp(content_type, map[i][0])) return map[i][1];
  return "icon_playlist";
}
/* Title and grey detail line, matching stock list rows. */
static void describe(const cJSON *item, enum kind kind, char *title, size_t title_size,
                     char *detail, size_t detail_size, const char **icon) {
  char time_text[24];
  const char *artist = json_string(item, "artist"), *album = json_string(item, "album");
  int count;
  copy_text(title, title_size, json_string(item, kind == K_TRACKS ? "title" : "name"));
  if (!title[0]) copy_text(title, title_size, "Untitled");
  detail[0] = 0;
  switch (kind) {
    case K_ARTISTS:
      *icon = "icon_artist";
      count = json_int(item, "album_count");
      if (count) snprintf(detail, detail_size, "%d album%s", count, count == 1 ? "" : "s");
      break;
    case K_ALBUMS:
      *icon = "albumcover_s";
      if (json_int(item, "year") > 0) snprintf(detail, detail_size, "%s%s%d", artist, artist[0] ? " · " : "", json_int(item, "year"));
      else copy_text(detail, detail_size, artist);
      break;
    case K_LISTS:
      *icon = "icon_playlist";
      count = json_int(item, "song_count");
      format_duration(time_text, sizeof(time_text), json_int(item, "duration"));
      snprintf(detail, detail_size, "%d song%s%s%s", count, count == 1 ? "" : "s", time_text[0] ? " · " : "", time_text);
      break;
    case K_TRACKS:
      *icon = format_icon(json_string(item, "content_type"));
      format_duration(time_text, sizeof(time_text), json_int(item, "duration"));
      if (history[depth].route == R_SEARCH && album[0])
        snprintf(detail, detail_size, "%s%s%s", artist, artist[0] ? " · " : "", album);
      else snprintf(detail, detail_size, "%s%s%s", artist, artist[0] && time_text[0] ? " · " : "", time_text);
      break;    case K_JOBS:
      break;
  }
}
static ret_t free_row(void *ctx, event_t *event) {
  (void)event;
  free(ctx);
  return ND_OK;
}
static void prepare_playback(int index);
static void navigate(enum route route, enum kind kind, const char *param, const char *title, int root);
static ret_t on_row(void *ctx, event_t *event) {
  const row_context *row = (const row_context *)ctx;
  cJSON *item;
  char id[768], title[192];
  enum kind kind;
  (void)event;
  if (!win || busy || row->list != list_serial) return ND_OK;
  kind = history[depth].kind;
  item = cJSON_GetArrayItem(items(), row->index);
  if (!item) return ND_OK;
  if (kind == K_TRACKS) { prepare_playback(row->index); return ND_OK; }
  copy_text(id, sizeof(id), json_string(item, "id"));
  copy_text(title, sizeof(title), json_string(item, "name"));
  if (!id[0]) { notify("Item has no server ID", 3); return ND_OK; }
  /* Copy all context before navigation synchronously destroys this row. */
  if (kind == K_ARTISTS) navigate(R_ARTIST, K_ALBUMS, id, title, 0);
  else if (kind == K_ALBUMS) navigate(R_ALBUM, K_TRACKS, id, title, 0);
  else navigate(R_LIST, K_TRACKS, id, title, 0);
  return ND_OK;
}
static widget_t *make_row(const char *name, int index, const char *icon, const char *title,
                          const char *detail, int arrow, int saved) {
  widget_t *row = make("list_item", rows_box, 0, 0, ND_LIST_W, ND_ROW_H, name);
  row_context *context;
  if (!row) return NULL;
  if (!native_style(row, "list_item_style")) {
    prop(row, "style:normal:bg_color", "#131313");
    prop(row, "style:pressed:bg_color", "#362221");
  }
  if (!strcmp(icon, "albumcover_s")) make_image(row, 10, 8, 44, 44, NULL, icon, "scale_auto");
  else make_image(row, 12, 10, 40, 40, NULL, icon, "icon");
  make_text(row, 64, detail[0] ? 6 : 17, arrow || saved || index < 0 ? 206 : 234, 26, NULL, title,
            "srlabel_item_white_style", "#FFFFFF", "20");
  if (detail[0])
    make_text(row, 64, 33, arrow || saved || index < 0 ? 206 : 234, 20, index < 0 ? "nd_job_detail" : NULL, detail,
              "srlabel_item_gray18_style", "#AAAAAA", "16");
  if (arrow) make_image(row, 284, 19, 13, 22, NULL, "right_arrow", "icon");
  /* Stock orange check: this song is already on the SD card. */
  else if (saved) make_image(row, 276, 17, 26, 26, "nd_saved", "mulselected", "icon");
  if (index < 0) return row;
  context = (row_context *)calloc(1, sizeof(*context));
  if (!context) return NULL;
  context->list = list_serial;
  context->index = index;
  /* Destroy owns row context. Click callback never outlives its widget. */
  if (!api.widget_on(row, ND_EVT_DESTROY, free_row, context)) { free(context); return NULL; }
  api.widget_on(row, ND_EVT_CLICK, on_row, context);
  return row;
}
static widget_t *make_more_row(const char *name, const char *text, enum action action) {
  widget_t *row = make("list_item", rows_box, 0, 0, ND_LIST_W, ND_ROW_H, name);
  if (!row) return NULL;
  if (!native_style(row, "list_item_style")) prop(row, "style:pressed:bg_color", "#362221");
  if (!make_label(row, 0, 0, ND_LIST_W, ND_ROW_H - 4, NULL, text, "label_centergray20_white", "#AAAAAA", "18") ||
      !api.widget_on(row, ND_EVT_CLICK, on_action, (void *)(uintptr_t)action)) return NULL;
  return row;
}
/* ---- Offline downloads. ---- */
static int collection(const char **kind, const char **id) {
  const view_state *v = &history[depth];
  if (v->route != R_ALBUM && v->route != R_LIST) return 0;
  *kind = v->route == R_ALBUM ? "album" : "playlist";
  *id = v->param;
  return 1;
}
static int offline_busy(void) {
  return !strcmp(offline_now.state, "queued") || !strcmp(offline_now.state, "active");
}
static void load_offline(void) {
  const cJSON *o = cJSON_GetObjectItemCaseSensitive(current_json, "offline");
  memset(&offline_now, 0, sizeof(offline_now));
  copy_text(offline_now.state, sizeof(offline_now.state), json_string(o, "state"));
  offline_now.done = json_int(o, "done");
  offline_now.total = json_int(o, "total");
  offline_now.downloaded = json_int(o, "downloaded");
}
static void set_icon(widget_t *button, const char *icon) {
  prop(button, "style:normal:icon", icon);
  prop(button, "style:pressed:icon", icon);
}
/* Header icon: download, cancel while running, check when complete (trash once armed). */
static void update_header(void) {
  char text[96], time_text[24];
  const char *icon = "download_more";
  int count = cJSON_GetArraySize(items());
  if (!header_summary || !header_download) return;
  if (offline_busy()) {
    icon = "cancel";
    if (!strcmp(offline_now.state, "queued")) copy_text(text, sizeof(text), "Waiting to download…");
    else snprintf(text, sizeof(text), "Downloading %d/%d", offline_now.done, offline_now.total ? offline_now.total : count);
  } else if (count && offline_now.downloaded >= count) {
    icon = time(NULL) < remove_armed ? "delete_more" : "mulselected";
    snprintf(text, sizeof(text), "On SD card · %d song%s", count, count == 1 ? "" : "s");
  } else if (offline_now.downloaded) {
    snprintf(text, sizeof(text), "%d of %d on SD card", offline_now.downloaded, count);
  } else if (!strcmp(offline_now.state, "failed")) {
    copy_text(text, sizeof(text), "Download failed · tap to retry");
  } else {
    const cJSON *info = cJSON_GetObjectItemCaseSensitive(current_json, history[depth].route == R_ALBUM ? "album" : "playlist");
    format_duration(time_text, sizeof(time_text), json_int(info, "duration"));
    snprintf(text, sizeof(text), "%d song%s%s%s", count, count == 1 ? "" : "s", time_text[0] ? " · " : "", time_text);
  }
  api.widget_set_text_utf8(header_summary, text);
  set_icon(header_download, icon);
}
/* Album and playlist header: stock "Play all" pill, summary and download state. */
static int make_header(void) {
  widget_t *header = api.view_create(rows_box, 0, 0, ND_LIST_W, 56);
  if (!header) return -1;
  api.widget_set_name(header, "nd_header");
  if (!make_button(header, 12, 14, 90, 28, "nd_play_all", NULL, "Play_all_style", A_PLAY_ALL)) return -1;
  header_summary = make_text(header, 112, 15, 146, 26, "nd_summary", "", "srlabel_leftgray16_style", "#AAAAAA", "16");
  header_download = make_button(header, 262, 6, 44, 44, "nd_download", NULL, "btn_nottablerow_style", A_DOWNLOAD);
  if (!header_summary || !header_download) return -1;
  update_header();
  return 0;
}
static void submit(job *j);
/* POST {"kind","id"} to a download endpoint; the reply arrives as an M_POST result. */
static void post_action(const char *path, const char *kind, const char *id) {
  job *j = (job *)calloc(1, sizeof(*j));
  cJSON *payload = cJSON_CreateObject();
  char *text = NULL;
  if (j && payload && cJSON_AddStringToObject(payload, "kind", kind) && cJSON_AddStringToObject(payload, "id", id))
    text = cJSON_PrintUnformatted(payload);
  cJSON_Delete(payload);
  if (!text || strlen(text) >= sizeof(j->body)) {
    free(text); free_job(j); notify("Not enough memory", 3); return;
  }
  j->mode = M_POST;
  copy_text(j->path, sizeof(j->path), path);
  copy_text(j->body, sizeof(j->body), text);
  free(text);
  submit(j);
}
static void open_library(void) {
  if (api.navigator_to) api.navigator_to("systemset/updatemusic_page");
}
static void on_download_button(void) {
  const char *kind, *id;
  int count = cJSON_GetArraySize(items());
  if (busy || !collection(&kind, &id)) return;
  if (offline_busy()) {
    post_action("/v1/downloads/cancel", kind, id);
  } else if (count && offline_now.downloaded >= count) {
    if (time(NULL) < remove_armed) {
      remove_armed = 0;
      post_action("/v1/offline/remove", kind, id);
    } else {
      remove_armed = time(NULL) + 4;
      notify("Tap the bin again to remove from the SD card", 4);
      update_header();
    }
  } else {
    post_action("/v1/downloads", kind, id);
  }
}
static void format_size(char *out, size_t size, double bytes) {
  if (bytes >= 1073741824.0) snprintf(out, size, "%.1f GB", bytes / 1073741824.0);
  else snprintf(out, size, "%d MB", (int)(bytes / 1048576.0 + 0.5));
}
static const char *job_icon_name(const char *state) {
  if (!strcmp(state, "queued") || !strcmp(state, "active")) return "cancel";
  if (!strcmp(state, "done")) return "mulselected";
  return "download_more";
}
static void job_text(const cJSON *job, char *out, size_t size) {
  const char *state = json_string(job, "state");
  double total = cJSON_GetNumberValue(cJSON_GetObjectItemCaseSensitive(job, "total_bytes"));
  double bytes = cJSON_GetNumberValue(cJSON_GetObjectItemCaseSensitive(job, "bytes"));
  int done = json_int(job, "done"), count = json_int(job, "total");
  if (!strcmp(state, "queued")) copy_text(out, size, "Waiting");
  else if (!strcmp(state, "active")) {
    if (total > 0 && bytes == bytes) snprintf(out, size, "Downloading %d/%d · %d%%", done, count, (int)(bytes * 100.0 / total));
    else snprintf(out, size, "Downloading %d/%d", done, count);
  } else if (!strcmp(state, "done")) snprintf(out, size, "On SD card · %d song%s", count, count == 1 ? "" : "s");
  else if (!strcmp(state, "failed")) snprintf(out, size, "Failed: %s", json_string(job, "error"));
  else if (!strcmp(state, "cancelled")) copy_text(out, size, "Cancelled · tap to resume");
  else copy_text(out, size, "Removed · tap to download again");
}
static void jobs_header_text(char *out, size_t size) {
  double free_bytes = cJSON_GetNumberValue(cJSON_GetObjectItemCaseSensitive(current_json, "free_bytes"));
  int saved = json_int(current_json, "downloaded_tracks");
  char space[32];
  if (free_bytes >= 0 && free_bytes == free_bytes) {
    format_size(space, sizeof(space), free_bytes);
    snprintf(out, size, "%d saved · %s free", saved, space);
  } else snprintf(out, size, "%d saved", saved);
}
static ret_t on_job_row(void *ctx, event_t *event) {
  const row_context *row = (const row_context *)ctx;
  const cJSON *job;
  char kind[16], id[768], name[192];
  const char *state;
  (void)event;
  if (!win || busy || row->list != list_serial) return ND_OK;
  job = cJSON_GetArrayItem(items(), row->index);
  if (!job) return ND_OK;
  state = json_string(job, "state");
  copy_text(kind, sizeof(kind), json_string(job, "kind"));
  copy_text(id, sizeof(id), json_string(job, "source_id"));
  copy_text(name, sizeof(name), json_string(job, "name"));
  if (!id[0]) return ND_OK;
  if (!strcmp(state, "queued") || !strcmp(state, "active")) post_action("/v1/downloads/cancel", kind, id);
  else if (!strcmp(state, "done")) navigate(!strcmp(kind, "album") ? R_ALBUM : R_LIST, K_TRACKS, id, name, 0);
  else post_action("/v1/downloads", kind, id);
  return ND_OK;
}
/* The download page keeps its rows and updates them in place while polling. */
static void render_downloads(void) {
  cJSON *jobs = items();
  int n = cJSON_GetArraySize(jobs), i;
  widget_t *header;
  char text[256];
  if (new_list()) { show_status("Not enough memory", 1, 0); return; }
  header = api.view_create(rows_box, 0, 0, ND_LIST_W, 56);
  if (!header) goto failed;
  api.widget_set_name(header, "nd_jobs_header");
  jobs_header_text(text, sizeof(text));
  jobs_summary = make_text(header, 12, 15, api.navigator_to ? 176 : 290, 26, "nd_jobs_summary", text,
                           "srlabel_leftgray16_style", "#AAAAAA", "16");
  if (!jobs_summary) goto failed;
  if (api.navigator_to) {
    widget_t *update = make_button(header, 192, 10, 112, 36, "nd_library", "Update library", "btn_tablerow_style", A_LIBRARY);
    if (!update) goto failed;
    prop(update, "style:normal:font_size", "16");
    prop(update, "style:pressed:font_size", "16");
  }
  if (!n) {
    show_status("Open an album or playlist and tap the download icon", 0, 0);
    if (api.widget_layout) api.widget_layout(list);
    return;
  }
  hide_status();
  for (i = 0; i < n && i < ND_JOBS_MAX; ++i) {
    cJSON *job = cJSON_GetArrayItem(jobs, i);
    const char *kind = json_string(job, "kind"), *name = json_string(job, "name");
    char row_name[32];
    widget_t *row;
    row_context *context;
    snprintf(row_name, sizeof(row_name), "nd_job_%d", i);
    job_text(job, text, sizeof(text));
    row = make_row(row_name, -1, !strcmp(kind, "album") ? "albumcover_s" : "icon_playlist",
                   name[0] ? name : (!strcmp(kind, "album") ? "Album" : "Playlist"), text, 0, 0);
    if (!row) goto failed;
    /* make_row's detail label is its third child; keep it and add a state icon. */
    job_detail[i] = api.widget_lookup(row, "nd_job_detail", 0);
    job_icon[i] = make_image(row, 276, 17, 26, 26, "nd_job_icon", job_icon_name(json_string(job, "state")), "icon");
    context = (row_context *)calloc(1, sizeof(*context));
    if (!job_icon[i] || !context) { free(context); goto failed; }
    context->list = list_serial;
    context->index = i;
    if (!api.widget_on(row, ND_EVT_DESTROY, free_row, context)) { free(context); goto failed; }
    api.widget_on(row, ND_EVT_CLICK, on_job_row, context);
    copy_text(job_ids[i], sizeof(job_ids[i]), json_string(job, "id"));
    job_rows = i + 1;
  }
  if (api.widget_layout) api.widget_layout(list);
  return;
failed:
  destroy_list();
  show_status("Not enough memory", 1, 0);
}
/* Returns 0 if the same jobs are shown, so only their texts and icons change. */
static int update_downloads(void) {
  cJSON *jobs = items();
  int n = cJSON_GetArraySize(jobs), i;
  char text[256];
  if (!list || n != job_rows || !jobs_summary) return -1;
  for (i = 0; i < n; ++i)
    if (strcmp(job_ids[i], json_string(cJSON_GetArrayItem(jobs, i), "id"))) return -1;
  jobs_header_text(text, sizeof(text));
  api.widget_set_text_utf8(jobs_summary, text);
  for (i = 0; i < n; ++i) {
    cJSON *job = cJSON_GetArrayItem(jobs, i);
    job_text(job, text, sizeof(text));
    if (job_detail[i]) api.widget_set_text_utf8(job_detail[i], text);
    prop(job_icon[i], "image", job_icon_name(json_string(job, "state")));
  }
  return 0;
}
static void render_rows(void) {
  view_state *v = &history[depth];
  cJSON *array;
  if (v->route == R_DOWNLOADS) { render_downloads(); return; }
  array = items();
  int n = cJSON_GetArraySize(array), i, remote = remote_paging();
  int first = remote ? 0 : (int)v->offset, end = remote ? n : (int)v->offset + ND_PAGE;
  int more = remote ? n >= ND_PAGE : end < n;
  char text[64];
  if (end > n) end = n;
  if (new_list()) { show_status("Not enough memory", 1, 0); return; }
  if (first >= end && !v->offset) {
    show_status(v->route == R_SEARCH ? "No results" : "Nothing here yet", 0, 0);
    return;
  }
  hide_status();
  if (v->kind == K_TRACKS && v->route != R_SEARCH) load_offline();
  if (v->kind == K_TRACKS && v->route != R_SEARCH && !v->offset && make_header()) goto failed;
  if (v->offset) {
    snprintf(text, sizeof(text), "Previous %d", ND_PAGE);
    if (!make_more_row("nd_more_prev", text, A_PREV)) goto failed;
  }
  for (i = first; i < end; ++i) {
    char name[32], title[512], detail[512];
    const char *icon = "icon_playlist";
    cJSON *item = cJSON_GetArrayItem(array, i);
    describe(item, v->kind, title, sizeof(title), detail, sizeof(detail), &icon);
    snprintf(name, sizeof(name), "nd_row_%d", i - first);
    if (!make_row(name, i, icon, title, detail, v->kind != K_TRACKS,
                  cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(item, "downloaded")))) goto failed;
  }
  if (more && !make_more_row("nd_more_next", "Show more", A_NEXT)) goto failed;
  snprintf(text, sizeof(text), "%u–%u", v->offset + 1, v->offset + (unsigned int)(end - first));
  api.widget_set_text_utf8(status_label, text);
  if (api.widget_layout) api.widget_layout(list);
  return;
failed:
  destroy_list();
  show_status("Not enough memory", 1, 0);
}

/* ---- Navigation and requests. ---- */
static int encode_query(char *out, size_t capacity, const char *s) {
  static const char hex[] = "0123456789ABCDEF";
  size_t p = 0;
  while (*s) {
    unsigned char c = (unsigned char)*s++;
    int plain = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
      (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~';
    if (p + (plain ? 1U : 3U) >= capacity) return -1;
    if (plain) out[p++] = (char)c;
    else { out[p++] = '%'; out[p++] = hex[c >> 4]; out[p++] = hex[c & 15]; }
  }
  out[p] = 0;
  return 0;
}
static void submit(job *j) {
  j->generation = cancel_previous();
  j->deadline = time(NULL) + (j->mode == M_PLAY ? 30 : 15);
  pthread_mutex_lock(&lock);
  pending_job = j;
  pthread_cond_signal(&wake_worker);
  pthread_mutex_unlock(&lock);
  /* Any newer request supersedes a background poll (its result is discarded). */
  if (j->mode == M_POLL) {
    polling = 1;
    poll_deadline = j->deadline;
  } else {
    busy = 1;
    polling = 0;
    busy_deadline = j->deadline;
  }
}
static int needs_poll(void) {
  enum route route = history[depth].route;
  return route == R_DOWNLOADS || ((route == R_ALBUM || route == R_LIST) && offline_busy());
}
static void start_poll(void) {
  job *j = (job *)calloc(1, sizeof(*j));
  if (!j) return;
  j->mode = M_POLL;
  copy_text(j->path, sizeof(j->path), "/v1/downloads");
  submit(j);
}
/* Apply /v1/downloads progress to the open download page or album/playlist header. */
static void apply_poll(cJSON *json) {
  view_state *v = &history[depth];
  const char *kind, *id;
  char key[800], previous[16];
  cJSON *job;
  if (v->route == R_DOWNLOADS) {
    cJSON_Delete(current_json);
    current_json = json;
    if (!items()) { show_status("Invalid bridge response", 1, 0); return; }
    if (update_downloads()) render_rows();
    return;
  }
  if (!collection(&kind, &id)) { cJSON_Delete(json); return; }
  snprintf(key, sizeof(key), "%s:%s", kind, id);
  copy_text(previous, sizeof(previous), offline_now.state);
  cJSON_ArrayForEach(job, cJSON_GetObjectItemCaseSensitive(json, "jobs")) {
    if (strcmp(json_string(job, "id"), key)) continue;
    copy_text(offline_now.state, sizeof(offline_now.state), json_string(job, "state"));
    offline_now.done = json_int(job, "done");
    offline_now.total = json_int(job, "total");
    if (!strcmp(offline_now.state, "failed")) {
      char text[200];
      snprintf(text, sizeof(text), "Download failed: %s", json_string(job, "error"));
      notify(text, 5);
    }
  }
  cJSON_Delete(json);
  if ((!strcmp(previous, "queued") || !strcmp(previous, "active")) && !offline_busy()) {
    if (!strcmp(offline_now.state, "done")) notify("Downloaded · Update library to play offline", 5);
    /* Reload so each song's check mark reflects the card. */
    fetch_current();
    return;
  }
  update_header();
}
static void update_chrome(void) {
  const view_state *v = &history[depth];
  static const enum kind tab_kinds[3] = {K_TRACKS, K_ALBUMS, K_ARTISTS};
  int search = v->route == R_SEARCH, i;
  api.widget_set_visible(title_label, !search);
  api.widget_set_visible(search_open_button, !search);
  api.widget_set_visible(player_button, !search);
  api.widget_set_visible(search_box, search);
  api.widget_set_visible(search_go_button, search);
  if (!search) api.widget_set_text_utf8(title_label, v->title);
  for (i = 0; i < 3; ++i) {
    int active = search && v->kind == tab_kinds[i];
    api.widget_set_visible(search_tabs[i], search);
    state_props(search_tabs[i], "bg_color", active ? ND_ACCENT : "#131313");
    prop(search_tabs[i], "style:pressed:bg_color", active ? ND_ACCENT : "#362221");
  }
}
static void fetch_current(void) {
  view_state *v = &history[depth];
  job *j;
  char encoded[2304];
  int n = 0;
  update_chrome();
  reset_content();
  memset(&offline_now, 0, sizeof(offline_now));
  remove_armed = 0;
  if (v->route == R_SEARCH) api.widget_set_text_utf8(search_edit, v->param);
  if (v->route == R_SEARCH && !v->param[0]) {
    cancel_previous();
    busy = 0;
    show_status("Search artists, albums and songs", 0, 0);
    return;
  }
  j = (job *)calloc(1, sizeof(*j));
  if (!j || encode_query(encoded, sizeof(encoded), v->param)) {
    free_job(j); busy = 0; show_status("Request too long or no memory", 1, 0); return;
  }
  switch (v->route) {
    case R_ARTISTS: n = snprintf(j->path, sizeof(j->path), "/v1/artists"); break;
    case R_ARTIST: n = snprintf(j->path, sizeof(j->path), "/v1/artist?id=%s", encoded); break;
    case R_ALBUMS: n = snprintf(j->path, sizeof(j->path), "/v1/albums?offset=%u&size=%u", v->offset, ND_PAGE); break;
    case R_ALBUM: n = snprintf(j->path, sizeof(j->path), "/v1/album?id=%s", encoded); break;
    case R_SEARCH: n = snprintf(j->path, sizeof(j->path), "/v1/search?q=%s&offset=%u&size=%u", encoded, v->offset, ND_PAGE); break;
    case R_LISTS: n = snprintf(j->path, sizeof(j->path), "/v1/playlists"); break;
    case R_LIST: n = snprintf(j->path, sizeof(j->path), "/v1/playlist?id=%s", encoded); break;
    case R_DOWNLOADS: n = snprintf(j->path, sizeof(j->path), "/v1/downloads"); break;
  }
  if (n < 0 || n >= (int)sizeof(j->path)) {
    free_job(j); busy = 0; show_status("Request is too long", 1, 0); return;
  }
  submit(j);
  show_status("Loading…", 0, 1);
}
static void navigate(enum route route, enum kind kind, const char *param, const char *title, int root) {
  view_state next;
  memset(&next, 0, sizeof(next));
  next.route = route;
  next.kind = kind;
  copy_text(next.param, sizeof(next.param), param);
  copy_text(next.title, sizeof(next.title), title);
  if (root) depth = 0;
  else if (depth + 1 < ND_HISTORY_MAX) ++depth;
  else { notify("Go back to open another item", 3); return; }
  history[depth] = next;
  fetch_current();
}
static void prepare_playback(int index) {
  cJSON *all = items();
  unsigned int n = (unsigned int)cJSON_GetArraySize(all), i;
  unsigned int first = ((unsigned int)index / ND_QUEUE_MAX) * ND_QUEUE_MAX;
  unsigned int end = first + ND_QUEUE_MAX;
  job *j;
  if (index < 0 || (unsigned int)index >= n || busy) return;
  if (end > n) end = n;
  j = (job *)calloc(1, sizeof(*j));
  if (!j) { notify("Not enough memory", 3); return; }
  j->mode = M_PLAY;
  j->selected = index - (int)first;
  j->first_track = first + (remote_paging() ? history[depth].offset : 0);
  j->tracks = cJSON_CreateArray();
  if (!j->tracks) { free_job(j); notify("Not enough memory", 3); return; }
  for (i = first; i < end; ++i) {
    cJSON *t = cJSON_Duplicate(cJSON_GetArrayItem(all, (int)i), 1);
    if (!t || !cJSON_AddItemToArray(j->tracks, t)) {
      cJSON_Delete(t); free_job(j); notify("Not enough memory", 3); return;
    }
  }
  submit(j);
  notify("Preparing playback…", 30);
}
static int apply_playback(result *r) {
  unsigned int i;
  int32_t ids[ND_QUEUE_MAX];
  void *q;
  if (!r->count || r->count > ND_QUEUE_MAX || r->selected < 0 || (unsigned int)r->selected >= r->count) return -1;
  q = api.create_deque("stSongInfo");
  if (!q) return -1;
  api.deque_init(q);
  for (i = 0; i < r->count; ++i) {
    nd_song song;
    cJSON *track = cJSON_GetArrayItem(r->tracks, (int)i);
    const unsigned char *key = (const unsigned char *)json_string(track, "id");
    uint32_t hash = 2166136261U;
    unsigned int k;
    int collision;
    if (!r->urls[i]) { api.deque_destroy(q); return -1; }
    memset(&song, 0, sizeof(song));
    while (*key) hash = (hash ^ *key++) * 16777619U;
    /* Separate from positive scanner DB IDs; -1 remains a stock sentinel. */
    song.id = -(int32_t)((hash & 0x3fffffffU) + 2U);
    do {
      collision = 0;
      for (k = 0; k < i; ++k) if (ids[k] == song.id) { collision = 1; break; }
      if (collision) song.id = song.id == -1073741825 ? -2 : song.id - 1;
    } while (collision);
    ids[i] = song.id;
    song.filename = (char *)json_string(track, "title");
    if (!song.filename[0]) song.filename = "Untitled";
    song.url = r->urls[i];
    song.title = song.filename;
    song.album = (char *)json_string(track, "album");
    song.artist = (char *)json_string(track, "artist");
    song.albumartist = song.artist;
    song.genre = song.composer = "";
    song.item_kind = 8;
    song.source_type = 0;
    song.track = json_int(track, "track");
    song.disc = json_int(track, "disc");
    /* push_back and mclLoadPlayList both deep copy registered string members. */
    api.deque_push_back(q, &song);
  }
  if (api.deque_size(q) != r->count) { api.deque_destroy(q); return -1; }
  api.mclLoadPlayList(q, r->selected, 0);
  api.deque_destroy(q);
  api.mclSetStartSeekTime(0);
  /* Vendor start_playqueue returns 1 even if the engine's later network open fails. */
  api.start_playqueue(r->selected);
  return 0;
}
static ret_t deliver_results(const idle_info_t *unused) {
  result *r;
  (void)unused;
  for (;;) {
    pthread_mutex_lock(&lock);
    r = results_head;
    if (r) {
      results_head = r->next;
      if (!results_head) results_tail = NULL;
    }
    pthread_mutex_unlock(&lock);
    if (!r) break;
    if (win && r->generation == epoch() && r->mode == M_POLL) {
      polling = 0;
      next_poll = time(NULL) + ND_POLL_SECONDS;
      if (!r->error[0]) { apply_poll(r->json); r->json = NULL; }
    } else if (win && r->generation == epoch() && r->mode == M_POST) {
      busy = 0;
      if (r->error[0]) notify(r->error, 4);
      else if (cJSON_GetObjectItemCaseSensitive(r->json, "job")) {
        notify("Downloading to the SD card…", 3);
        copy_text(offline_now.state, sizeof(offline_now.state), "queued");
      } else if (cJSON_GetObjectItemCaseSensitive(r->json, "cancelled")) {
        notify("Download cancelled", 3);
        copy_text(offline_now.state, sizeof(offline_now.state), "cancelled");
      } else {
        notify("Removed from the SD card", 3);
        offline_now.downloaded = 0;
        offline_now.state[0] = 0;
        /* Reload so per-song check marks disappear. */
        if (history[depth].route != R_DOWNLOADS) { fetch_current(); r->mode = -1; }
      }
      if (r->mode != -1) {
        update_header();
        next_poll = 0;
      }
    } else if (win && r->generation == epoch()) {
      busy = 0;
      if (r->mode == M_PLAY) {
        if (r->error[0]) notify(r->error, 4);
        else if (apply_playback(r)) notify("Could not create playback queue", 4);
        else {
          char text[160];
          snprintf(text, sizeof(text), "Queued tracks %u–%u", r->first_track + 1, r->first_track + r->count);
          notify(text, 3);
          show_player();
        }
      } else if (r->error[0]) {
        show_status(r->error, 1, 0);
      } else {
        current_json = r->json;
        r->json = NULL;
        if (!items()) show_status("Invalid bridge response", 1, 0);
        else render_rows();
      }
    }
    free_result(r);
  }
  return ND_REMOVE;
}

/* ---- Window. ---- */
static void release_window(void) {
  cancel_previous();
  cJSON_Delete(current_json);
  current_json = NULL;
  win = content = list = rows_box = NULL;
  title_label = search_open_button = player_button = NULL;
  search_box = search_edit = search_go_button = NULL;
  status_label = spinner = retry_button = toast = NULL;
  header_summary = header_download = jobs_summary = NULL;
  memset(search_tabs, 0, sizeof(search_tabs));
  memset(&offline_now, 0, sizeof(offline_now));
  job_rows = polling = 0;
  next_poll = remove_armed = 0;
  ++list_serial;
  depth = 0;
  busy = spinning = 0;
  toast_deadline = 0;
}
static ret_t on_window_destroy(void *ctx, event_t *event) {
  (void)event;
  if (win == (widget_t *)ctx) release_window();
  return ND_OK;
}
static void close_window(void) {
  widget_t *closing = win;
  release_window();
  api.window_close(closing);
}
static void submit_search(void) {
  char query[768] = {0};
  view_state *v = &history[depth];
  api.widget_get_text_utf8(search_edit, query, sizeof(query));
  if (!query[0]) { notify("Enter an artist, album or song", 3); return; }
  /* Searching again replaces the current search instead of growing history. */
  copy_text(v->param, sizeof(v->param), query);
  v->offset = 0;
  fetch_current();
}
static ret_t on_action(void *ctx, event_t *event) {
  enum action action = (enum action)(uintptr_t)ctx;
  view_state *v = &history[depth];
  (void)event;
  if (!win) return ND_OK;
  switch (action) {
    case A_BACK:
      if (depth) { --depth; fetch_current(); }
      else close_window();
      break;
    case A_SEARCH_OPEN: navigate(R_SEARCH, K_TRACKS, "", "Search", 0); break;
    case A_SEARCH_GO: if (v->route == R_SEARCH) submit_search(); break;
    case A_PLAYER: show_player(); break;
    case A_TAB_TRACKS: case A_TAB_ALBUMS: case A_TAB_ARTISTS: {
      static const enum kind kinds[] = {K_TRACKS, K_ALBUMS, K_ARTISTS};
      if (v->route != R_SEARCH) break;
      v->kind = kinds[action - A_TAB_TRACKS];
      v->offset = 0;
      fetch_current();
      break;
    }
    case A_PREV: case A_NEXT:
      if (busy) break;
      if (action == A_PREV) v->offset = v->offset >= ND_PAGE ? v->offset - ND_PAGE : 0;
      else v->offset += ND_PAGE;
      if (remote_paging()) fetch_current();
      else render_rows();
      break;
    case A_PLAY_ALL: if (!busy) prepare_playback(0); break;
    case A_DOWNLOAD: on_download_button(); break;
    case A_LIBRARY: open_library(); break;
    case A_RETRY: fetch_current(); break;
  }
  return ND_OK;
}
static widget_t *make_search_box(void) {
  widget_t *box = api.view_create(win, 36, 6, 232, 38);
  if (!box) return NULL;
  api.widget_set_name(box, "nd_search_box");
  prop(box, "style:normal:bg_image", "search_bg");
  prop(box, "style:normal:bg_image_draw_type", "scale");
  search_edit = make("edit", box, 14, 0, 208, 38, "nd_search_text");
  if (!search_edit) return NULL;
  api.edit_set_keyboard(search_edit, "kb_default_t9");
  api.edit_set_tips(search_edit, "Search artists, albums, songs");
  /* Inline styles copied from the stock search_page edit. */
  state_props(search_edit, "bg_color", "#131313");
  state_props(search_edit, "border_width", "0");
  state_props(search_edit, "text_color", "#FFFFFF");
  state_props(search_edit, "tips_text_color", "#AAAAAA");
  state_props(search_edit, "font_size", "18");
  return box;
}
static ret_t open_window(void *ctx, event_t *event) {
  enum section section = (enum section)(uintptr_t)ctx;
  widget_t *top;
  int i;
  (void)event;
  if (win) return ND_OK;
  top = api.window_create(NULL, 0, 0, 0, 0);
  if (!top) return ND_OK;
  win = top;
  if (api.widget_get_prop_int(win, "w", 320) != 320 || api.widget_get_prop_int(win, "h", 450) < 440) {
    fputs("Navidrome: unsupported display geometry; window closed\n", stderr);
    win = NULL; api.window_close(top); return ND_OK;
  }
  api.widget_set_name(win, "nd_navidrome");
  prop(win, "style:normal:bg_color", "#000000");
  if (!api.widget_on(win, ND_EVT_DESTROY, on_window_destroy, win)) goto failed;
  /* Lists live in one container below the navigation bar, status and toast. */
  content = api.view_create(win, 0, 0, 320, 450);
  if (!content) goto failed;
  api.widget_set_name(content, "nd_content");
  /* Stock navigation bar: title first, so the 80px return button stays on top of it. */
  title_label = make_text(win, 36, 12, 184, 26, "nd_title", "Navidrome", "srlabel_leftwhite22_style", "#FFFFFF", "22");
  if (!title_label || !make_button(win, 0, 0, 80, ND_NAV_H, "nd_back", NULL, "navbar_btnreturn_style", A_BACK)) goto failed;
  search_open_button = make_button(win, 224, 0, 48, ND_NAV_H, "nd_search_open", NULL, "search_style", A_SEARCH_OPEN);
  player_button = make_button(win, 272, 0, 48, ND_NAV_H, "nd_player", NULL, "navbar_btnbackplayer_style", A_PLAYER);
  search_box = make_search_box();
  search_go_button = make_button(win, 272, 0, 48, ND_NAV_H, "nd_search", NULL, "search_style", A_SEARCH_GO);
  if (!search_open_button || !player_button || !search_box || !search_go_button) goto failed;
  if (!api.navigator_switch_to_with_context) api.widget_set_visible(player_button, 0);
  for (i = 0; i < 3; ++i) {
    const char *names[] = {"nd_search_tracks", "nd_search_albums", "nd_search_artists"};
    const char *labels[] = {"Songs", "Albums", "Artists"};
    search_tabs[i] = make_button(win, i * 107, ND_NAV_H, 106, 44, names[i], labels[i],
                                 "btn_tablerow_style", (enum action)(A_TAB_TRACKS + i));
    if (!search_tabs[i]) goto failed;
    prop(search_tabs[i], "style:normal:font_size", "18");
  }
  spinner = make_image(win, 135, 160, 50, 50, "nd_spinner", "imge_wait1", "icon");
  status_label = make_label(win, 10, 216, 300, 30, "nd_status", "", "label_centergray20_white", "#AAAAAA", "20");
  retry_button = make_button(win, 100, 260, 120, 44, "nd_retry", "Retry", "btn_tablerow_style", A_RETRY);
  toast = make_label(win, 24, 388, 272, 44, "nd_toast", "", NULL, "#FFFFFF", "18");
  if (!spinner || !status_label || !retry_button || !toast) goto failed;
  prop(toast, "style:normal:bg_color", "#262626");
  prop(toast, "style:normal:round_radius", "8");
  /* Two lines fit the 44px toast; longer error messages wrap instead of clipping. */
  prop(toast, "line_wrap", "true");
  api.widget_set_visible(toast, 0);
  hide_status();
  switch (section) {
    case S_ALBUMS: navigate(R_ALBUMS, K_ALBUMS, "", "Albums", 1); break;
    case S_LISTS: navigate(R_LISTS, K_LISTS, "", "Playlists", 1); break;
    case S_SEARCH: navigate(R_SEARCH, K_TRACKS, "", "Search", 1); break;
    case S_DOWNLOADS: navigate(R_DOWNLOADS, K_JOBS, "", "Downloads", 1); break;
    default: navigate(R_ARTISTS, K_ARTISTS, "", "Artists", 1); break;
  }
  return ND_OK;
failed:
  release_window();
  api.window_close(top);
  fputs("Navidrome: widget creation failed; window closed\n", stderr);
  return ND_OK;
}

/* ---- Home page: a stock-style tile page inside the home slide view. ---- */
static ret_t open_library_tile(void *ctx, event_t *event) {
  (void)ctx; (void)event;
  open_library();
  return ND_OK;
}
static int make_tile(widget_t *page, int index, const char *name, const char *text, const char *icon,
                     event_cb callback, enum section section) {
  int x = 6 + (index % 2) * 154, y = (index / 2) * 118;
  widget_t *tile = api.view_create(page, x, y, 154, 118), *button, *label, *art;
  char id[48];
  if (!tile) return -1;
  snprintf(id, sizeof(id), "nd_tile_%s", name);
  api.widget_set_name(tile, id);
  if (icon) art = make_image(tile, 44, 20, 66, 66, NULL, icon, "default");
  else {
    /* No stock 66px search tile exists: accent disc plus the stock search glyph. */
    art = api.view_create(tile, 44, 20, 66, 66);
    prop(art, "style:normal:bg_color", ND_ACCENT);
    prop(art, "style:normal:round_radius", "33");
    if (art && !make_image(art, 21, 21, 24, 24, NULL, "search", "center")) return -1;
  }
  label = make_label(tile, 0, 90, 154, 28, NULL, text, "label_centerwhitehome_white", "#FFFFFF", "21");
  snprintf(id, sizeof(id), "nd_open_%s", name);
  button = make_button(tile, 0, 0, 154, 118, id, NULL, "btn_nottablerow_style", 0);
  if (!art || !label || !button) return -1;
  return api.widget_on(button, ND_EVT_CLICK, callback, (void *)(uintptr_t)section) ? 0 : -1;
}
static void install_home_page(void) {
  widget_t *home, *slides, *indicator, *page;
  home = api.widget_lookup(api.window_manager(), "home_page", 1);
  if (!home || api.widget_lookup(home, "nd_home_page", 1)) return;
  slides = api.widget_lookup(home, "home_sview", 1);
  indicator = api.widget_lookup(home, "home_sindicator", 1);
  if (!slides || !indicator) return;
  page = api.view_create(slides, 0, 0, 320, 364);
  if (!page) return;
  /* Small caption above the first row so the page reads as Navidrome, not the local library. */
  if (!make_label(page, 0, 0, 320, 18, "nd_home_hint", "NAVIDROME", "srlabel_leftgray16_style", "#AAAAAA", "14") ||
      make_tile(page, 0, "artists", "Artists", "songer", open_window, S_ARTISTS) ||
      make_tile(page, 1, "albums", "Albums", "album", open_window, S_ALBUMS) ||
      make_tile(page, 2, "playlists", "Playlists", "playlist", open_window, S_LISTS) ||
      make_tile(page, 3, "search", "Search", NULL, open_window, S_SEARCH) ||
      make_tile(page, 4, "downloads", "Downloads", "hifimusic", open_window, S_DOWNLOADS) ||
      (api.navigator_to && make_tile(page, 5, "library", "Update library", "sset_updatemusic", open_library_tile, 0))) {
    api.widget_destroy(page);
    return;
  }
  prop(api.widget_lookup(page, "nd_home_hint", 0), "style:normal:text_align_h", "center");
  api.widget_set_name(page, "nd_home_page");
  api.slide_indicator_set_max(indicator, (uint32_t)api.widget_count_children(slides));
}
static ret_t timer_tick(const timer_info_t *unused) {
  (void)unused;
  deliver_results(NULL);
  if (win && busy && time(NULL) >= busy_deadline) {
    cancel_previous();
    busy = 0;
    if (current_json) notify("Request timed out; try again", 4);
    else show_status("Request timed out", 1, 0);
  }
  if (win && polling && time(NULL) >= poll_deadline) {
    cancel_previous();
    polling = 0;
  }
  if (win && !busy && !polling && needs_poll() && time(NULL) >= next_poll) start_poll();
  if (win && remove_armed && time(NULL) >= remove_armed) {
    remove_armed = 0;
    update_header();
  }
  if (win && toast_deadline && time(NULL) >= toast_deadline) {
    toast_deadline = 0;
    api.widget_set_visible(toast, 0);
  }
  install_home_page();
  return ND_REPEAT;
}

__attribute__((visibility("default"))) int nd_ui_start(void) {
  pthread_t thread;
  if (installed) return 0;
  if (resolve_api() || !api.main_loop()) return -1;
  /* One worker serializes requests; a newer view replaces any pending request. */
  if (!worker_started) {
    if (pthread_create(&thread, NULL, network_worker, NULL) != 0) return -1;
    pthread_detach(thread);
    worker_started = 1;
  }
  if (!api.timer_add(timer_tick, NULL, 300)) {
    fputs("Navidrome: GUI timer unavailable; extension inactive\n", stderr);
    return -1;
  }
  installed = 1;
  timer_tick(NULL);
  return 0;
}
static ret_t install_on_gui(const idle_info_t *unused) {
  (void)unused;
  nd_ui_start();
  return ND_REMOVE;
}
int nd_ui_schedule_start(void) {
  if (resolve_api()) return -1;
  if (!api.main_loop()) return 1;
  return api.idle_queue(install_on_gui, NULL) == ND_OK ? 0 : 1;
}
