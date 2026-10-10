#define _POSIX_C_SOURCE 200809L
#include "../../native/nd_abi.h"
#include <assert.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef struct handler {uint32_t type;event_cb cb;void *ctx;} handler;
struct nd_widget {
 char *name,*text,*kind;int x,y,w,h,enabled,visible,dead;uint32_t indicator_max;
 struct nd_widget *parent,*children[256];size_t count;
 handler handlers[16];size_t handler_count;
};
typedef struct idle {idle_cb cb;void *ctx;struct idle *next;} idle;
typedef struct timer {timer_cb cb;void *ctx;unsigned ms;long next;int active;} timer;
typedef struct deque {nd_song songs[256];unsigned count;} deque;
static pthread_t gui;static widget_t *manager;
static idle *idle_head,*idle_tail;static pthread_mutex_t idle_lock=PTHREAD_MUTEX_INITIALIZER;
static timer timers[16];static unsigned timer_count,idle_worker_posts;
static deque stock;static int playlist_loads,play_starts,last_selected,last_seek;
static const char *statefile;static int checks;

static long millis(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000L+t.tv_nsec/1000000L;}
static void on_gui(void){assert(pthread_equal(pthread_self(),gui)&&"widget/player called outside GUI thread");}
static void alive(widget_t *w){on_gui();assert(w&&!w->dead&&"stale destroyed widget accessed");}
static void replace(char **p,const char *s){free(*p);*p=strdup(s?s:"");assert(*p);}
static widget_t *create(const char *kind,widget_t *parent,int x,int y,int w,int h){
 on_gui();widget_t *v=calloc(1,sizeof(*v));assert(v);v->kind=strdup(kind);v->name=strdup("");v->text=strdup("");v->x=x;v->y=y;v->w=w;v->h=h;v->enabled=v->visible=1;
 if(parent){alive(parent);assert(parent->count<256);parent->children[parent->count++]=v;v->parent=parent;}return v;
}
__attribute__((visibility("default"))) void *main_loop(void){return manager;}
__attribute__((visibility("default"))) ret_t idle_queue(idle_cb cb,void *ctx){
 idle *v=calloc(1,sizeof(*v));assert(v);v->cb=cb;v->ctx=ctx;
 pthread_mutex_lock(&idle_lock);if(!pthread_equal(pthread_self(),gui))idle_worker_posts++;
 if(idle_tail)idle_tail->next=v;else idle_head=v;idle_tail=v;pthread_mutex_unlock(&idle_lock);return ND_OK;
}
__attribute__((visibility("default"))) uint32_t timer_add(timer_cb cb,void *ctx,uint32_t ms){on_gui();assert(timer_count<16);timers[timer_count]=(timer){cb,ctx,ms,millis()+ms,1};return ++timer_count;}
__attribute__((visibility("default"))) widget_t *window_manager(void){on_gui();return manager;}
#define CREATOR(name,kind) __attribute__((visibility("default"))) widget_t *name(widget_t *p,int32_t x,int32_t y,int32_t w,int32_t h){if(!p)p=manager;if(!w)w=320;if(!h)h=450;return create(kind,p,x,y,w,h);}
CREATOR(window_create,"window") CREATOR(view_create,"view") CREATOR(button_create,"button") CREATOR(label_create,"label")
__attribute__((visibility("default"))) void *widget_factory(void){on_gui();return manager;}
__attribute__((visibility("default"))) widget_t *widget_factory_create_widget(void *f,const char *kind,widget_t *p,int32_t x,int32_t y,int32_t w,int32_t h){(void)f;return create(kind,p,x,y,w,h);}
__attribute__((visibility("default"))) widget_t *widget_lookup(widget_t *w,const char *name,int recursive){alive(w);if(!strcmp(w->name,name))return w;for(size_t i=0;i<w->count;i++){widget_t *c=w->children[i];if(c->dead)continue;if(!strcmp(c->name,name))return c;if(recursive){widget_t *r=widget_lookup(c,name,1);if(r)return r;}}return NULL;}
__attribute__((visibility("default"))) int32_t widget_count_children(widget_t *w){alive(w);int32_t n=0;for(size_t i=0;i<w->count;i++)if(!w->children[i]->dead)n++;return n;}
__attribute__((visibility("default"))) ret_t slide_indicator_set_max(widget_t *w,uint32_t n){alive(w);w->indicator_max=n;return ND_OK;}
__attribute__((visibility("default"))) uint32_t widget_on(widget_t *w,uint32_t type,event_cb cb,void *ctx){alive(w);assert(w->handler_count<16);w->handlers[w->handler_count++]=(handler){type,cb,ctx};return (uint32_t)w->handler_count;}
__attribute__((visibility("default"))) ret_t widget_set_name(widget_t *w,const char *s){alive(w);replace(&w->name,s);return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_set_text_utf8(widget_t *w,const char *s){alive(w);replace(&w->text,s);return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_get_text_utf8(widget_t *w,char *out,uint32_t n){alive(w);snprintf(out,n,"%s",w->text);return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_set_prop_str(widget_t *w,const char *k,const char *s){alive(w);(void)k;(void)s;return ND_OK;}
__attribute__((visibility("default"))) int32_t widget_get_prop_int(widget_t *w,const char *k,int32_t fallback){alive(w);if(!strcmp(k,"w"))return w->w;if(!strcmp(k,"h"))return w->h;return fallback;}
__attribute__((visibility("default"))) ret_t widget_set_enable(widget_t *w,int v){alive(w);w->enabled=v;return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_set_visible(widget_t *w,int v,...){alive(w);w->visible=v;return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_use_style(widget_t *w,const char *s){alive(w);(void)s;return ND_OK;}
__attribute__((visibility("default"))) int widget_is_style_exist(widget_t *w,const char *name,const char *state){alive(w);(void)name;(void)state;return 1;}
static void destroy(widget_t *w){alive(w);for(size_t i=0;i<w->handler_count;i++){handler h=w->handlers[i];if(h.type==ND_EVT_DESTROY)h.cb(h.ctx,NULL);}for(size_t i=0;i<w->count;i++)if(!w->children[i]->dead)destroy(w->children[i]);w->count=0;w->dead=1;}
__attribute__((visibility("default"))) ret_t widget_destroy(widget_t *w){destroy(w);return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_destroy_children(widget_t *w){alive(w);for(size_t i=0;i<w->count;i++)if(!w->children[i]->dead)destroy(w->children[i]);w->count=0;return ND_OK;}
__attribute__((visibility("default"))) ret_t window_close(widget_t *w){destroy(w);return ND_OK;}
__attribute__((visibility("default"))) ret_t edit_set_keyboard(widget_t *w,const char *s){alive(w);assert(!strcmp(s,"kb_default_t9"));return ND_OK;}
__attribute__((visibility("default"))) ret_t edit_set_tips(widget_t *w,const char *s){alive(w);(void)s;return ND_OK;}

static void song_copy(nd_song *dst,const nd_song *src){*dst=*src;
#define COPY(field) dst->field=src->field?strdup(src->field):NULL
 COPY(extra_id);COPY(filename);COPY(url);COPY(title);COPY(album);COPY(artist);COPY(genre);COPY(composer);COPY(albumartist);COPY(cover);COPY(extra);
#undef COPY
}
static void song_free(nd_song *s){
#define FREE(field) free(s->field)
 FREE(extra_id);FREE(filename);FREE(url);FREE(title);FREE(album);FREE(artist);FREE(genre);FREE(composer);FREE(albumartist);FREE(cover);FREE(extra);
#undef FREE
 memset(s,0,sizeof(*s));}
static void clear(deque *q){for(unsigned i=0;i<q->count;i++)song_free(&q->songs[i]);q->count=0;}
__attribute__((visibility("default"))) void *_create_deque(const char *kind){on_gui();assert(!strcmp(kind,"stSongInfo"));return calloc(1,sizeof(deque));}
__attribute__((visibility("default"))) void deque_init(void *q){on_gui();assert(q);}
__attribute__((visibility("default"))) void _deque_push_back(void *ptr,...){on_gui();deque *q=ptr;assert(q->count<256);va_list va;va_start(va,ptr);const nd_song *song=va_arg(va,const nd_song *);va_end(va);song_copy(&q->songs[q->count++],song);}
__attribute__((visibility("default"))) unsigned deque_size(const void *q){on_gui();return ((const deque *)q)->count;}
__attribute__((visibility("default"))) void deque_destroy(void *ptr){on_gui();deque *q=ptr;clear(q);free(q);}
__attribute__((visibility("default"))) void mclLoadPlayList(void *ptr,int selected,int mode){on_gui();deque *q=ptr;assert(mode==0);clear(&stock);for(unsigned i=0;i<q->count;i++)song_copy(&stock.songs[stock.count++],&q->songs[i]);last_selected=selected;playlist_loads++;}
__attribute__((visibility("default"))) void mclSetStartSeekTime(int seek){on_gui();last_seek=seek;}
__attribute__((visibility("default"))) int start_playqueue(int selected){on_gui();assert(selected==last_selected&&selected>=0&&(unsigned)selected<stock.count);play_starts++;return 1;}

static void pump(void){on_gui();for(;;){pthread_mutex_lock(&idle_lock);idle *v=idle_head;if(v){idle_head=v->next;if(!idle_head)idle_tail=NULL;}pthread_mutex_unlock(&idle_lock);if(!v)break;v->cb(NULL);free(v);}long now=millis();for(unsigned i=0;i<timer_count;i++)if(timers[i].active&&now>=timers[i].next){int r=timers[i].cb(NULL);timers[i].next=now+timers[i].ms;if(r==ND_REMOVE)timers[i].active=0;}}
static void delay_ms(long ms){long end=millis()+ms;while(millis()<end){pump();struct timespec t={0,10000000};nanosleep(&t,NULL);}}
static widget_t *lookup(const char *name){return widget_lookup(manager,name,1);}
static const char *text(const char *name){widget_t *w=lookup(name);assert(w);return w->text;}
static void click(const char *name){widget_t *w=lookup(name);assert(w&&w->enabled&&w->visible);size_t n=w->handler_count;handler hs[16];memcpy(hs,w->handlers,n*sizeof(handler));for(size_t i=0;i<n;i++)if(hs[i].type==ND_EVT_CLICK)hs[i].cb(hs[i].ctx,NULL);}
static void await_status(const char *part){long end=millis()+5000;while(millis()<end){pump();widget_t *w=lookup("nd_status");if(w&&strstr(w->text,part))return;delay_ms(10);}fprintf(stderr,"FAIL waiting for status '%s', actual '%s'\n",part,lookup("nd_status")?text("nd_status"):"closed");abort();}
static void mode(const char *value){char marker[1024];snprintf(marker,sizeof(marker),"%s.inflight",statefile);unlink(marker);FILE *f=fopen(statefile,"w");assert(f);fprintf(f,"%s",value);fclose(f);}
static void await_inflight(void){char marker[1024];snprintf(marker,sizeof(marker),"%s.inflight",statefile);long end=millis()+3000;while(millis()<end){if(!access(marker,F_OK))return;delay_ms(10);}fputs("fixture never observed in-flight request\n",stderr);abort();}
static void pass(const char *s){checks++;printf("PASS %s\n",s);fflush(stdout);}
static void xml(FILE *f,const char *s){for(;*s;s++){if(*s=='&')fputs("&amp;",f);else if(*s=='<')fputs("&lt;",f);else if(*s=='>')fputs("&gt;",f);else if(*s=='\"')fputs("&quot;",f);else fputc(*s,f);}}
static void svg_widget(FILE *f,widget_t *w,int px,int py){if(w->dead||!w->visible)return;int x=px+w->x,y=py+w->y;if(strcmp(w->kind,"view")&&strcmp(w->kind,"window")){fprintf(f,"<rect x='%d' y='%d' width='%d' height='%d' fill='%s' stroke='#666'/><text x='%d' y='%d' fill='%s' font-size='12'>",x,y,w->w,w->h,!strcmp(w->kind,"button")?"#202020":"#050505",x+4,y+w->h/2+4,w->enabled?"white":"#888");xml(f,w->text);fputs("</text>\n",f);}for(size_t i=0;i<w->count;i++)svg_widget(f,w->children[i],x,y);}
static void snapshot(const char *filename){FILE *f=fopen(filename,"w");assert(f);fputs("<svg xmlns='http://www.w3.org/2000/svg' width='320' height='480' viewBox='0 0 320 480'><rect width='320' height='480' fill='black'/><text x='5' y='467' fill='#ffcc77' font-size='11'>HOST MOCK GEOMETRY — NOT DEVICE RENDER</text>\n",f);svg_widget(f,lookup("nd_navidrome"),0,0);fputs("</svg>\n",f);fclose(f);}

int main(int argc,char **argv){
 assert(argc==4);gui=pthread_self();statefile=argv[2];mode("normal");manager=create("manager",NULL,0,0,320,450);
 widget_t *home=create("window",manager,0,0,320,450);widget_set_name(home,"home_page");widget_t *slides=create("view",home,0,0,320,364);widget_set_name(slides,"home_sview");create("view",slides,0,0,320,364);create("view",slides,0,0,320,364);widget_t *indicator=create("indicator",home,0,364,320,20);widget_set_name(indicator,"home_sindicator");
 void *plugin=dlopen(argv[1],RTLD_NOW|RTLD_GLOBAL);if(!plugin){fprintf(stderr,"dlopen: %s\n",dlerror());return 1;}
 int (*start)(void)=NULL;void *p=dlsym(plugin,"nd_ui_start");memcpy(&start,&p,sizeof(start));assert(start&&start()==0);assert(lookup("nd_open"));assert(widget_count_children(slides)==3&&indicator->indicator_max==3);delay_ms(400);assert(widget_count_children(slides)==3&&indicator->indicator_max==3);pass("actual plugin ABI resolution, single home injection and slide indicator count");
 click("nd_open");await_status("Select to browse");assert(!strcmp(text("nd_title"),"Artists"));assert(strstr(text("nd_row_0"),"宇多田"));pass("worker HTTP artists result delivered through GUI event loop");
 click("nd_next");assert(strstr(text("nd_row_0"),"Artist 4"));click("nd_previous");assert(strstr(text("nd_row_0"),"宇多田"));pass("local artist paging and Previous");
 click("nd_row_0");await_status("Select to browse");assert(!strcmp(text("nd_title"),"宇多田 Test"));click("nd_row_0");await_status("Select to browse");assert(!strcmp(text("nd_title"),"Requiem café"));assert(!strcmp(text("nd_row_1"),"Track 1"));
 click("nd_row_1");await_status("Queued tracks");assert(stock.count==6&&playlist_loads==1&&play_starts==1&&last_selected==1&&last_seek==0);assert(!strcmp(stock.songs[1].title,"Track 1")&&!strcmp(stock.songs[1].album,"Requiem café")&&!strcmp(stock.songs[1].artist,"宇多田 Test"));assert(stock.songs[1].track==2&&stock.songs[1].disc==2&&stock.songs[1].item_kind==8);for(unsigned i=0;i<stock.count;i++){assert(stock.songs[i].id< -1);for(unsigned j=0;j<i;j++)assert(stock.songs[i].id!=stock.songs[j].id);assert(strstr(stock.songs[i].url,"http://127.0.0.1:17831/stream/")==stock.songs[i].url);assert(strlen(stock.songs[i].url)==strlen("http://127.0.0.1:17831/stream/")+32);}pass("artist → album → selected track deep-copied stock playback queue and metadata");
 widget_set_text_utf8(lookup("nd_search_text"),"宇多田 & café");click("nd_search");await_status("Select to browse");assert(!strcmp(text("nd_title"),"Search"));click("nd_next");await_status("Select to browse");assert(strstr(text("nd_row_0"),"Track 4"));assert(!lookup("nd_next")->enabled);click("nd_previous");await_status("Select to browse");click("nd_search_artists");await_status("Select to browse");assert(strstr(text("nd_row_0"),"宇多田"));click("nd_back");await_status("Select to browse");assert(!strcmp(text("nd_title"),"Requiem café"));pass("Unicode search, remote offset paging, kind tabs and Back restore navigation");
 click("nd_lists");await_status("Select to browse");assert(!strcmp(text("nd_row_0"),"Fixture Mix"));click("nd_row_0");await_status("Select to browse");assert(!strcmp(text("nd_title"),"Fixture Mix"));click("nd_row_0");await_status("Queued tracks");assert(stock.count==3&&play_starts==2);pass("playlists → playlist tracks → stock queue playback");
 mode("empty");click("nd_artists");await_status("No results");assert(!lookup("nd_row_0")&&!lookup("nd_next")->enabled);pass("empty results and disabled paging");
 mode("malformed");click("nd_artists");await_status("Invalid bridge response");assert(lookup("nd_retry")->visible);mode("normal");click("nd_retry");await_status("Select to browse");mode("schema");click("nd_artists");await_status("Invalid bridge response");assert(lookup("nd_retry")->visible);mode("normal");click("nd_retry");await_status("Select to browse");pass("malformed JSON and schema shown with working Retry");
 mode("error");click("nd_albums");await_status("Bridge error 503");assert(lookup("nd_retry")->visible);mode("normal");click("nd_retry");await_status("Select to browse");click("nd_next");await_status("Select to browse");assert(strstr(text("nd_row_0"),"Album 4"));click("nd_previous");await_status("Select to browse");pass("upstream HTTP error, Retry and alphabetical album paging");
 mode("normal");click("nd_artists");await_status("Select to browse");snapshot(argv[3]);pass("host mock widget geometry SVG captured with explicit provenance");
 mode("delay");click("nd_artists");await_inflight();click("nd_close");assert(!lookup("nd_navidrome"));delay_ms(1400);assert(!lookup("nd_navidrome"));mode("normal");click("nd_open");await_status("Select to browse");assert(strstr(text("nd_row_0"),"宇多田"));pass("close during delayed HTTP, stale generation discard and clean reopen");
 click("nd_row_0");await_status("Select to browse");click("nd_row_0");await_status("Select to browse");int before=play_starts;mode("delay_prepare");click("nd_row_0");await_inflight();click("nd_close");delay_ms(1400);assert(play_starts==before&&!lookup("nd_navidrome"));pass("close during prepare does not start stale playback");
 mode("normal");click("nd_open");await_status("Select to browse");click("nd_row_0");await_status("Select to browse");click("nd_row_0");await_status("Select to browse");before=play_starts;mode("badurl");click("nd_row_0");await_status("Invalid stream address");assert(play_starts==before);mode("normal");click("nd_row_1");await_status("Queued tracks");assert(play_starts==before+1);pass("foreign credential-bearing stream URL rejected; failed prepare rows recover");click("nd_close");
 pthread_mutex_lock(&idle_lock);unsigned posts=idle_worker_posts;pthread_mutex_unlock(&idle_lock);assert(posts>0);printf("HOST INTEGRATION PASS: %d checks; %u worker idle posts; all widget/player operations GUI-thread only.\n",checks,posts);clear(&stock);return 0;
}
