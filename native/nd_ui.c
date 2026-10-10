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
#define ND_ROWS 4
#define ND_QUEUE_MAX 128
#define ND_BODY_MAX (2U * 1024U * 1024U)
#define ND_HISTORY_MAX 32

enum route { R_ARTISTS, R_ARTIST, R_ALBUMS, R_ALBUM, R_SEARCH, R_LISTS, R_LIST };
enum kind { K_ARTISTS, K_ALBUMS, K_TRACKS, K_LISTS };
enum action { A_BACK = 1, A_CLOSE, A_ARTISTS, A_ALBUMS, A_LISTS, A_SEARCH,
              A_PREV, A_NEXT, A_RETRY, A_SEARCH_ARTISTS, A_SEARCH_ALBUMS,
              A_SEARCH_TRACKS };
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
  int play, selected;
  unsigned int first_track;
  char path[2600];
  cJSON *tracks;
  time_t deadline;
} job;
typedef struct result {
  struct result *next;
  unsigned long generation;
  int play, selected;
  unsigned int first_track;
  cJSON *json;
  cJSON *tracks;
  char **urls;
  unsigned int count;
  char error[160];
} result;
typedef struct row_context {
  unsigned long generation;
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
static widget_t *win, *title_label, *status_label, *rows_box, *search_edit;
static widget_t *prev_button, *next_button, *page_label, *retry_button;
static widget_t *search_tabs[3];
static view_state history[ND_HISTORY_MAX];
static unsigned int depth;
static cJSON *current_json;
static int busy;
static time_t busy_deadline;

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
  symbols_resolved = 1;
  return 0;
#undef LOAD
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
  if (status != 200) {
    snprintf(error, 160, "Bridge error %ld; retry", status); goto done;
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
    r->play = j->play;
    r->selected = j->selected;
    r->first_track = j->first_track;
    if (j->play) prepare_tracks(j, r);
    else r->json = request_json(j, j->path, NULL, r->error);
    if (!r->play && !r->json && !r->error[0]) copy_text(r->error, 160, "Request cancelled or timed out");
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

static void status_text(const char *text, int retry) {
  if (!win) return;
  api.widget_set_text_utf8(status_label, text);
  api.widget_set_visible(retry_button, retry);
}
static void native_style(widget_t *w, const char *name) {
  if (w && api.widget_is_style_exist(w, name, "normal")) api.widget_use_style(w, name);
}
static widget_t *make_button(widget_t *parent, int x, int y, int width, int height,
                             const char *name, const char *text, enum action action) {
  widget_t *w = api.button_create(parent, x, y, width, height);
  if (!w) return NULL;
  api.widget_set_name(w, name);
  api.widget_set_text_utf8(w, text);
  native_style(w, "list_itemsort_style");
  if (action && !api.widget_on(w, ND_EVT_CLICK, on_action, (void *)(uintptr_t)action)) return NULL;
  return w;
}
static widget_t *make_label(widget_t *parent, int x, int y, int width, int height,
                            const char *name, const char *text) {
  widget_t *w = api.label_create(parent, x, y, width, height);
  if (!w) return NULL;
  api.widget_set_name(w, name);
  api.widget_set_text_utf8(w, text);
  native_style(w, "srlabel_leftwhite20_style");
  return w;
}
static cJSON *items(void) {
  const char *keys[] = {"artists", "albums", "tracks", "playlists"};
  cJSON *a = cJSON_GetObjectItemCaseSensitive(current_json, keys[history[depth].kind]);
  return cJSON_IsArray(a) ? a : NULL;
}
static int remote_paging(void) {
  return history[depth].route == R_ALBUMS || history[depth].route == R_SEARCH;
}
static void update_paging(void) {
  view_state *v = &history[depth];
  int n = cJSON_GetArraySize(items());
  char text[80];
  unsigned int shown = remote_paging() ? (unsigned int)n :
    (v->offset < (unsigned int)n ? (unsigned int)n - v->offset : 0);
  if (shown > ND_ROWS) shown = ND_ROWS;
  if (shown) snprintf(text, sizeof(text), "%u–%u", v->offset + 1, v->offset + shown);
  else copy_text(text, sizeof(text), "No results");
  api.widget_set_text_utf8(page_label, text);
  api.widget_set_enable(prev_button, !busy && v->offset > 0);
  api.widget_set_enable(next_button, !busy && (remote_paging() ? n >= ND_ROWS : v->offset + ND_ROWS < (unsigned int)n));
}
static void reset_content(void) {
  api.widget_destroy_children(rows_box);
  cJSON_Delete(current_json);
  current_json = NULL;
}
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
static void submit(job *j, const char *status) {
  j->generation = cancel_previous();
  j->deadline = time(NULL) + (j->play ? 30 : 15);
  busy_deadline = j->deadline;
  pthread_mutex_lock(&lock);
  pending_job = j;
  pthread_cond_signal(&wake_worker);
  pthread_mutex_unlock(&lock);
  busy = 1;
  status_text(status, 0);
  update_paging();
}
static void fetch_current(void) {
  view_state *v = &history[depth];
  job *j = (job *)calloc(1, sizeof(*j));
  char encoded[2304];
  int n = 0, i;
  if (!j || encode_query(encoded, sizeof(encoded), v->param)) {
    free_job(j); busy = 0; status_text("Request too long or no memory", 1); return;
  }
  switch (v->route) {
    case R_ARTISTS: n = snprintf(j->path, sizeof(j->path), "/v1/artists"); break;
    case R_ARTIST: n = snprintf(j->path, sizeof(j->path), "/v1/artist?id=%s", encoded); break;
    case R_ALBUMS: n = snprintf(j->path, sizeof(j->path), "/v1/albums?offset=%u&size=%u", v->offset, ND_ROWS); break;
    case R_ALBUM: n = snprintf(j->path, sizeof(j->path), "/v1/album?id=%s", encoded); break;
    case R_SEARCH: n = snprintf(j->path, sizeof(j->path), "/v1/search?q=%s&offset=%u&size=%u", encoded, v->offset, ND_ROWS); break;
    case R_LISTS: n = snprintf(j->path, sizeof(j->path), "/v1/playlists"); break;
    case R_LIST: n = snprintf(j->path, sizeof(j->path), "/v1/playlist?id=%s", encoded); break;
  }
  if (n < 0 || n >= (int)sizeof(j->path)) {
    free_job(j); busy = 0; status_text("Request is too long", 1); return;
  }
  api.widget_set_text_utf8(title_label, v->title);
  for (i = 0; i < 3; ++i) {
    api.widget_set_visible(search_tabs[i], v->route == R_SEARCH);
    api.widget_set_enable(search_tabs[i], v->kind != (enum kind)i);
  }
  reset_content();
  submit(j, "Loading…");
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
  else { status_text("Use Back to open another item", 0); return; }
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
  if (!j) { status_text("Not enough memory", 1); return; }
  j->play = 1;
  j->selected = index - (int)first;
  j->first_track = first + (remote_paging() ? history[depth].offset : 0);
  j->tracks = cJSON_CreateArray();
  if (!j->tracks) { free_job(j); status_text("Not enough memory", 1); return; }
  for (i = first; i < end; ++i) {
    cJSON *t = cJSON_Duplicate(cJSON_GetArrayItem(all, (int)i), 1);
    if (!t || !cJSON_AddItemToArray(j->tracks, t)) {
      cJSON_Delete(t); free_job(j); status_text("Not enough memory", 1); return;
    }
  }
  submit(j, "Preparing playback…");
}
static ret_t free_row(void *ctx, event_t *event) {
  (void)event;
  free(ctx);
  return ND_OK;
}
static ret_t on_row(void *ctx, event_t *event) {
  const row_context *row = (const row_context *)ctx;
  cJSON *item;
  char id[768], title[192];
  enum kind kind;
  int index;
  (void)event;
  if (!win || busy || row->generation != epoch()) return ND_OK;
  index = row->index;
  kind = history[depth].kind;
  item = cJSON_GetArrayItem(items(), index);
  if (!item) return ND_OK;
  if (kind == K_TRACKS) { prepare_playback(index); return ND_OK; }
  copy_text(id, sizeof(id), json_string(item, "id"));
  copy_text(title, sizeof(title), json_string(item, "name"));
  if (!id[0]) { status_text("Item has no server ID", 0); return ND_OK; }
  /* Copy all context before fetch_current synchronously destroys this row. */
  if (kind == K_ARTISTS) navigate(R_ARTIST, K_ALBUMS, id, title, 0);
  else if (kind == K_ALBUMS) navigate(R_ALBUM, K_TRACKS, id, title, 0);
  else navigate(R_LIST, K_TRACKS, id, title, 0);
  return ND_OK;
}
static void render_rows(void) {
  unsigned int i, first = remote_paging() ? 0 : history[depth].offset;
  cJSON *array = items();
  int n = cJSON_GetArraySize(array);
  api.widget_destroy_children(rows_box);
  for (i = 0; i < ND_ROWS && first + i < (unsigned int)n; ++i) {
    cJSON *item = cJSON_GetArrayItem(array, (int)(first + i));
    row_context *row = (row_context *)calloc(1, sizeof(*row));
    widget_t *button;
    char name[32], text[512];
    const char *main_text = json_string(item, history[depth].kind == K_TRACKS ? "title" : "name");
    const char *artist = json_string(item, "artist");
    if (!row) { status_text("Not enough memory", 1); break; }
    snprintf(name, sizeof(name), "nd_row_%u", i);
    if ((history[depth].kind == K_ALBUMS || history[depth].route == R_SEARCH) && artist[0])
      snprintf(text, sizeof(text), "%s · %s", main_text[0] ? main_text : "Untitled", artist);
    else copy_text(text, sizeof(text), main_text[0] ? main_text : "Untitled");
    button = make_button(rows_box, 0, (int)i * 44, 308, 42, name, text, 0);
    if (!button) { free(row); status_text("Not enough memory", 1); break; }
    row->generation = epoch();
    row->index = (int)(first + i);
    /* Destroy owns row context. Click callback never outlives its widget. */
    if (!api.widget_on(button, ND_EVT_DESTROY, free_row, row)) { free(row); continue; }
    api.widget_on(button, ND_EVT_CLICK, on_row, row);
  }
  update_paging();
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
    if (win && r->generation == epoch()) {
      busy = 0;
      if (r->error[0]) {
        status_text(r->error, 1);
        if (current_json) render_rows();
      }
      else if (r->play) {
        if (apply_playback(r)) status_text("Could not create playback queue", 1);
        else {
          char text[160];
          snprintf(text, sizeof(text), "Queued tracks %u–%u", r->first_track + 1, r->first_track + r->count);
          status_text(text, 0);
        }
        /* New generation was used for playback; refresh row callback generations. */
        render_rows();
      } else {
        current_json = r->json;
        r->json = NULL;
        if (!items()) status_text("Invalid bridge response", 1);
        else {
          status_text(cJSON_GetArraySize(items()) ? "Select to browse or play" : "No results", 0);
          render_rows();
        }
      }
      update_paging();
    }
    free_result(r);
  }
  return ND_REMOVE;
}
static void release_window(void) {
  cancel_previous();
  cJSON_Delete(current_json);
  current_json = NULL;
  win = title_label = status_label = rows_box = search_edit = NULL;
  prev_button = next_button = page_label = retry_button = NULL;
  memset(search_tabs, 0, sizeof(search_tabs));
  depth = 0;
  busy = 0;
}
static ret_t on_window_destroy(void *ctx, event_t *event) {
  (void)event;
  if (win == (widget_t *)ctx) release_window();
  return ND_OK;
}
static ret_t on_action(void *ctx, event_t *event) {
  enum action action = (enum action)(uintptr_t)ctx;
  view_state *v = &history[depth];
  (void)event;
  if (!win) return ND_OK;
  switch (action) {
    case A_CLOSE: {
      widget_t *closing = win;
      release_window();
      api.window_close(closing);
      break;
    }
    case A_BACK:
      if (depth) { --depth; fetch_current(); }
      else navigate(R_ARTISTS, K_ARTISTS, "", "Artists", 1);
      break;
    case A_ARTISTS: navigate(R_ARTISTS, K_ARTISTS, "", "Artists", 1); break;
    case A_ALBUMS: navigate(R_ALBUMS, K_ALBUMS, "", "Albums", 1); break;
    case A_LISTS: navigate(R_LISTS, K_LISTS, "", "Playlists", 1); break;
    case A_SEARCH: {
      char query[768] = {0};
      api.widget_get_text_utf8(search_edit, query, sizeof(query));
      if (query[0]) navigate(R_SEARCH, K_TRACKS, query, "Search", 0);
      else status_text("Enter an artist, album or song", 0);
      break;
    }
    case A_SEARCH_ARTISTS: case A_SEARCH_ALBUMS: case A_SEARCH_TRACKS:
      if (v->route == R_SEARCH) {
        v->kind = (enum kind)(action - A_SEARCH_ARTISTS);
        v->offset = 0;
        fetch_current();
      }
      break;
    case A_PREV: case A_NEXT:
      if (busy) break;
      if (action == A_PREV) v->offset = v->offset >= ND_ROWS ? v->offset - ND_ROWS : 0;
      else v->offset += ND_ROWS;
      if (remote_paging()) fetch_current();
      else render_rows();
      break;
    case A_RETRY: fetch_current(); break;
  }
  return ND_OK;
}
static ret_t open_window(void *ctx, event_t *event) {
  widget_t *top;
  int i;
  (void)ctx; (void)event;
  if (win) return ND_OK;
  top = api.window_create(NULL, 0, 0, 0, 0);
  if (!top) return ND_OK;
  win = top;
  if (api.widget_get_prop_int(win, "w", 320) != 320 || api.widget_get_prop_int(win, "h", 450) < 440) {
    fputs("Navidrome: unsupported display geometry; window closed\n", stderr);
    win = NULL; api.window_close(top); return ND_OK;
  }
  api.widget_set_name(win, "nd_navidrome");
  api.widget_set_prop_str(win, "style:normal:bg_color", "#000000");
  if (!api.widget_on(win, ND_EVT_DESTROY, on_window_destroy, win)) goto failed;
  if (!make_button(win, 4, 0, 68, 42, "nd_back", "Back", A_BACK)) goto failed;
  title_label = make_label(win, 76, 0, 168, 42, "nd_title", "Navidrome");
  if (!title_label || !make_button(win, 248, 0, 68, 42, "nd_close", "Close", A_CLOSE)) goto failed;
  if (!make_button(win, 4, 44, 102, 40, "nd_artists", "Artists", A_ARTISTS) ||
      !make_button(win, 108, 44, 102, 40, "nd_albums", "Albums", A_ALBUMS) ||
      !make_button(win, 212, 44, 104, 40, "nd_lists", "Playlists", A_LISTS)) goto failed;
  search_edit = api.widget_factory_create_widget(api.widget_factory(), "edit", win, 6, 88, 218, 40);
  if (!search_edit) goto failed;
  api.widget_set_name(search_edit, "nd_search_text");
  api.edit_set_keyboard(search_edit, "kb_default_t9");
  api.edit_set_tips(search_edit, "Search music");
  if (!make_button(win, 228, 88, 86, 40, "nd_search", "Search", A_SEARCH)) goto failed;
  status_label = make_label(win, 6, 132, 230, 44, "nd_status", "Loading…");
  retry_button = make_button(win, 240, 134, 74, 40, "nd_retry", "Retry", A_RETRY);
  if (!status_label || !retry_button) goto failed;
  for (i = 0; i < 3; ++i) {
    const char *names[] = {"nd_search_artists", "nd_search_albums", "nd_search_tracks"};
    const char *labels[] = {"Artists", "Albums", "Songs"};
    search_tabs[i] = make_button(win, 4 + i * 104, 176, 102, 32, names[i], labels[i], (enum action)(A_SEARCH_ARTISTS + i));
    if (!search_tabs[i]) goto failed;
  }
  rows_box = api.view_create(win, 6, 212, 308, 176);
  if (!rows_box) goto failed;
  api.widget_set_name(rows_box, "nd_rows");
  prev_button = make_button(win, 4, 398, 100, 42, "nd_previous", "Previous", A_PREV);
  page_label = make_label(win, 108, 398, 104, 42, "nd_page", "");
  next_button = make_button(win, 216, 398, 100, 42, "nd_next", "Next", A_NEXT);
  if (!prev_button || !page_label || !next_button) goto failed;
  navigate(R_ARTISTS, K_ARTISTS, "", "Artists", 1);
  return ND_OK;
failed:
  release_window();
  api.window_close(top);
  fputs("Navidrome: widget creation failed; window closed\n", stderr);
  return ND_OK;
}
static ret_t timer_tick(const timer_info_t *unused) {
  widget_t *home, *slides, *indicator, *page, *button;
  (void)unused;
  deliver_results(NULL);
  if (win && busy && time(NULL) >= busy_deadline) {
    cancel_previous();
    busy = 0;
    if (current_json) render_rows();
    status_text("Request timed out; retry", 1);
    update_paging();
  }
  home = api.widget_lookup(api.window_manager(), "home_page", 1);
  if (!home || api.widget_lookup(home, "nd_home_page", 1)) return ND_REPEAT;
  slides = api.widget_lookup(home, "home_sview", 1);
  indicator = api.widget_lookup(home, "home_sindicator", 1);
  if (!slides || !indicator) return ND_REPEAT;
  page = api.view_create(slides, 0, 0, 320, 364);
  if (!page) return ND_REPEAT;
  button = make_button(page, 30, 130, 260, 60, "nd_open", "Navidrome", 0);
  if (!button || !api.widget_on(button, ND_EVT_CLICK, open_window, NULL)) {
    api.widget_destroy(page);
    return ND_REPEAT;
  }
  api.widget_set_name(page, "nd_home_page");
  api.slide_indicator_set_max(indicator, (uint32_t)api.widget_count_children(slides));
  make_label(page, 30, 196, 260, 44, "nd_home_hint", "Your music server");
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
