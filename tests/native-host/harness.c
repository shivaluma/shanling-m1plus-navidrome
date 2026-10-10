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
 char *keys[96],*values[96];size_t prop_count;
 struct nd_widget *parent,*children[256];size_t count;
 handler handlers[16];size_t handler_count;
};
typedef struct idle {idle_cb cb;void *ctx;struct idle *next;} idle;
typedef struct timer {timer_cb cb;void *ctx;unsigned ms;long next;int active;} timer;
typedef struct deque {nd_song songs[256];unsigned count;} deque;
static pthread_t gui;static widget_t *manager;
static idle *idle_head,*idle_tail;static pthread_mutex_t idle_lock=PTHREAD_MUTEX_INITIALIZER;
static timer timers[16];static unsigned timer_count,idle_worker_posts;
static deque stock;static int playlist_loads,play_starts,last_selected,last_seek,layouts,player_opens;
static nd_player_context last_player;
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
__attribute__((visibility("default"))) uint32_t timer_add(timer_cb cb,void *ctx,uint32_t ms){on_gui();for(unsigned i=0;i<timer_count;i++)if(!timers[i].active){timers[i]=(timer){cb,ctx,ms,millis()+ms,1};return i+1;}assert(timer_count<16&&"timer leak");timers[timer_count]=(timer){cb,ctx,ms,millis()+ms,1};return ++timer_count;}
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
__attribute__((visibility("default"))) ret_t widget_set_prop_str(widget_t *w,const char *k,const char *s){alive(w);assert(k&&s);for(size_t i=0;i<w->prop_count;i++)if(!strcmp(w->keys[i],k)){replace(&w->values[i],s);return ND_OK;}assert(w->prop_count<96);w->keys[w->prop_count]=strdup(k);w->values[w->prop_count]=strdup(s);w->prop_count++;return ND_OK;}
static const char *prop(widget_t *w,const char *k){for(size_t i=0;i<w->prop_count;i++)if(!strcmp(w->keys[i],k))return w->values[i];return "";}
static int library_opens;static char last_nav[64];
__attribute__((visibility("default"))) ret_t navigator_to(const char *target){on_gui();snprintf(last_nav,sizeof(last_nav),"%s",target);library_opens++;return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_layout(widget_t *w){alive(w);layouts++;return ND_OK;}
__attribute__((visibility("default"))) ret_t navigator_switch_to_with_context(const char *target,void *ctx,int close){on_gui();assert(!strcmp(target,"playing_page")&&ctx&&close==0);memcpy(&last_player,ctx,sizeof(last_player));player_opens++;return ND_OK;}
__attribute__((visibility("default"))) int32_t widget_get_prop_int(widget_t *w,const char *k,int32_t fallback){alive(w);if(!strcmp(k,"w"))return w->w;if(!strcmp(k,"h"))return w->h;for(size_t i=0;i<w->prop_count;i++)if(!strcmp(w->keys[i],k))return atoi(w->values[i]);return fallback;}
__attribute__((visibility("default"))) ret_t widget_set_enable(widget_t *w,int v){alive(w);w->enabled=v;return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_set_visible(widget_t *w,int v,...){alive(w);w->visible=v;return ND_OK;}
__attribute__((visibility("default"))) ret_t widget_use_style(widget_t *w,const char *s){alive(w);return widget_set_prop_str(w,"style",s);}
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
static int shown(widget_t *w){for(;w;w=w->parent)if(w->dead||!w->visible)return 0;return 1;}
static const char *text(const char *name){widget_t *w=lookup(name);assert(w);return w->text;}
/* Row title is the first text child of a list_item. */
static const char *row(const char *name){widget_t *w=lookup(name);assert(w);for(size_t i=0;i<w->count;i++)if(!strcmp(w->children[i]->kind,"hscroll_label")||!strcmp(w->children[i]->kind,"label"))return w->children[i]->text;return "";}
static void click(const char *name){widget_t *w=lookup(name);if(!w||!w->enabled||!shown(w)){fprintf(stderr,"cannot click %s\n",name);abort();}size_t n=w->handler_count;handler hs[16];memcpy(hs,w->handlers,n*sizeof(handler));for(size_t i=0;i<n;i++)if(hs[i].type==ND_EVT_CLICK)hs[i].cb(hs[i].ctx,NULL);}
static void await_text(const char *name,const char *part){long end=millis()+8000;while(millis()<end){pump();widget_t *w=lookup(name);if(w&&strstr(w->text,part))return;delay_ms(10);}fprintf(stderr,"FAIL waiting for %s '%s', actual '%s'\n",name,part,lookup(name)?text(name):"closed");abort();}
static void await_rows(void){long end=millis()+5000;while(millis()<end){pump();widget_t *s=lookup("nd_status");if(lookup("nd_rows")&&!shown(lookup("nd_spinner")))return;if(s&&shown(lookup("nd_retry"))){fprintf(stderr,"FAIL unexpected error '%s'\n",s->text);abort();}delay_ms(10);}fputs("FAIL waiting for rows\n",stderr);abort();}
static void mode(const char *value){char marker[1024];snprintf(marker,sizeof(marker),"%s.inflight",statefile);unlink(marker);FILE *f=fopen(statefile,"w");assert(f);fprintf(f,"%s",value);fclose(f);}
static void await_inflight(void){char marker[1024];snprintf(marker,sizeof(marker),"%s.inflight",statefile);long end=millis()+3000;while(millis()<end){if(!access(marker,F_OK))return;delay_ms(10);}fputs("fixture never observed in-flight request\n",stderr);abort();}
static void pass(const char *s){checks++;printf("PASS %s\n",s);fflush(stdout);}
static const char *icon(const char *name){widget_t *w=lookup(name);assert(w);return prop(w,"style:normal:icon");}
static int saved_mark(const char *row_name){widget_t *w=lookup(row_name);return w&&widget_lookup(w,"nd_saved",0)!=NULL;}
static void await_icon(const char *name,const char *want){long end=millis()+8000;while(millis()<end){pump();if(lookup(name)&&!strcmp(icon(name),want))return;delay_ms(10);}fprintf(stderr,"FAIL waiting for %s icon %s, actual %s\n",name,want,lookup(name)?icon(name):"missing");abort();}
static void close_all(void){while(lookup("nd_navidrome"))click("nd_back");}
static void xml(FILE *f,const char *s){for(;*s;s++){if(*s=='&')fputs("&amp;",f);else if(*s=='<')fputs("&lt;",f);else if(*s=='>')fputs("&gt;",f);else if(*s=='\"')fputs("&quot;",f);else fputc(*s,f);}}
/* list_view stacks scroll_view children; everything else is absolute. */
static void svg_widget(FILE *f,widget_t *w,int x,int y){
 if(w->dead||!w->visible)return;x+=w->x;y+=w->y;const char *image=prop(w,"image"),*style=prop(w,"style");
 if(!strcmp(w->kind,"list_item")||!strcmp(w->kind,"button"))fprintf(f,"<rect x='%d' y='%d' width='%d' height='%d' fill='%s' stroke='#333'/>\n",x,y,w->w,w->h,!strcmp(style,"btn_nottablerow_style")||strstr(style,"navbar")||!strcmp(style,"search_style")?"none":"#131313");
 if(*image)fprintf(f,"<rect x='%d' y='%d' width='%d' height='%d' fill='#2a4d7a'/><text x='%d' y='%d' fill='#9cf' font-size='8'>%s</text>\n",x,y,w->w,w->h,x+1,y+w->h/2+3,image);
 if(*style&&!strcmp(w->kind,"button")&&!*w->text)fprintf(f,"<text x='%d' y='%d' fill='#fa7' font-size='8'>%s</text>\n",x+2,y+w->h/2+3,style);
 if(*w->text){fprintf(f,"<text x='%d' y='%d' fill='%s' font-size='%d'>",x+4,y+w->h/2+5,strstr(style,"gray")?"#aaa":"white",strstr(style,"gray")||strstr(style,"gray18")?13:15);xml(f,w->text);fputs("</text>\n",f);}
 int cy=0;for(size_t i=0;i<w->count;i++){widget_t *c=w->children[i];if(!strcmp(w->kind,"scroll_view")){svg_widget(f,c,x-c->x,y+cy-c->y);if(!c->dead)cy+=c->h;}else svg_widget(f,c,x,y);}
}
/* Optional widget-tree export for the offline device-style preview renderer. */
static void json_str(FILE *f,const char *s){fputc('"',f);for(;*s;s++){unsigned char c=(unsigned char)*s;if(c=='"'||c=='\\')fprintf(f,"\\%c",c);else if(c<32)fprintf(f,"\\u%04x",c);else fputc(c,f);}fputc('"',f);}
static void json_widget(FILE *f,widget_t *w){fputs("{\"kind\":",f);json_str(f,w->kind);fputs(",\"name\":",f);json_str(f,w->name);fprintf(f,",\"rect\":[%d,%d,%d,%d],\"visible\":%d,\"text\":",w->x,w->y,w->w,w->h,w->visible);json_str(f,w->text);fputs(",\"props\":{",f);for(size_t i=0;i<w->prop_count;i++){if(i)fputc(',',f);json_str(f,w->keys[i]);fputc(':',f);json_str(f,w->values[i]);}fputs("},\"children\":[",f);int first=1;for(size_t i=0;i<w->count;i++){if(w->children[i]->dead)continue;if(!first)fputc(',',f);first=0;json_widget(f,w->children[i]);}fputs("]}",f);}
static void preview(const char *root,const char *name){const char *dir=getenv("ND_PREVIEW_DIR");if(!dir)return;char path[1024];snprintf(path,sizeof(path),"%s/%s.json",dir,name);FILE *f=fopen(path,"w");assert(f);json_widget(f,lookup(root));fclose(f);}
static void snapshot(const char *filename){FILE *f=fopen(filename,"w");assert(f);fputs("<svg xmlns='http://www.w3.org/2000/svg' width='320' height='480' viewBox='0 -30 320 480'><rect y='-30' width='320' height='480' fill='black'/><text x='5' y='-12' fill='#ffcc77' font-size='11'>HOST MOCK GEOMETRY — NOT DEVICE RENDER</text>\n",f);svg_widget(f,lookup("nd_navidrome"),0,0);fputs("</svg>\n",f);fclose(f);}

int main(int argc,char **argv){
 assert(argc==4);gui=pthread_self();statefile=argv[2];mode("normal");manager=create("manager",NULL,0,0,320,450);
 widget_t *home=create("window",manager,0,0,320,450);widget_set_name(home,"home_page");widget_t *slides=create("view",home,0,0,320,364);widget_set_name(slides,"home_sview");create("view",slides,0,0,320,364);create("view",slides,0,0,320,364);widget_t *indicator=create("indicator",home,0,364,320,20);widget_set_name(indicator,"home_sindicator");
 void *plugin=dlopen(argv[1],RTLD_NOW|RTLD_GLOBAL);if(!plugin){fprintf(stderr,"dlopen: %s\n",dlerror());return 1;}
 int (*start)(void)=NULL;void *p=dlsym(plugin,"nd_ui_start");memcpy(&start,&p,sizeof(start));assert(start&&start()==0);
 assert(lookup("nd_home_page")&&lookup("nd_open_artists")&&lookup("nd_open_albums")&&lookup("nd_open_playlists")&&lookup("nd_open_search")&&lookup("nd_open_downloads")&&lookup("nd_open_library"));assert(!strcmp(text("nd_home_hint"),"NAVIDROME"));click("nd_open_library");assert(library_opens==1&&!strcmp(last_nav,"systemset/updatemusic_page")&&!lookup("nd_navidrome"));
 assert(!strcmp(prop(lookup("nd_open_artists"),"style"),"btn_nottablerow_style"));assert(widget_count_children(slides)==3&&indicator->indicator_max==3);delay_ms(400);assert(widget_count_children(slides)==3&&indicator->indicator_max==3);preview("nd_home_page","home");pass("actual plugin ABI resolution, single stock-style tile page (incl. Downloads and Update library) and slide indicator count");
 click("nd_open_artists");await_rows();assert(!strcmp(text("nd_title"),"Artists")&&shown(lookup("nd_title"))&&!shown(lookup("nd_search_box")));assert(strstr(row("nd_row_0"),"宇多田"));assert(!strcmp(prop(lookup("nd_row_0"),"style"),"btn_tablerow_style")&&!strcmp(prop(lookup("nd_back"),"style"),"navbar_btnreturn_style"));assert(layouts>0);preview("nd_navidrome","artists");pass("worker HTTP artists delivered through GUI loop into stock-styled list rows");
 click("nd_more_next");assert(strstr(row("nd_row_0"),"Artist 4")&&lookup("nd_more_prev")&&!lookup("nd_more_next"));click("nd_more_prev");assert(strstr(row("nd_row_0"),"宇多田")&&!lookup("nd_more_prev"));pass("local artist windows with Show more and Previous rows");
 click("nd_row_0");await_rows();assert(!strcmp(text("nd_title"),"宇多田 Test"));click("nd_row_0");await_rows();assert(!strcmp(text("nd_title"),"Requiem café"));assert(lookup("nd_play_all")&&strstr(text("nd_summary"),"6 songs"));assert(!strcmp(row("nd_row_1"),"Track 1"));preview("nd_navidrome","album");
 click("nd_row_1");await_text("nd_toast","Queued tracks");assert(shown(lookup("nd_toast")));assert(stock.count==6&&playlist_loads==1&&play_starts==1&&last_selected==1&&last_seek==0);assert(!strcmp(stock.songs[1].title,"Track 1")&&!strcmp(stock.songs[1].album,"Requiem café")&&!strcmp(stock.songs[1].artist,"宇多田 Test"));assert(stock.songs[1].track==2&&stock.songs[1].disc==2&&stock.songs[1].item_kind==8);for(unsigned i=0;i<stock.count;i++){assert(stock.songs[i].id< -1);for(unsigned j=0;j<i;j++)assert(stock.songs[i].id!=stock.songs[j].id);assert(strstr(stock.songs[i].url,"http://127.0.0.1:17831/stream/")==stock.songs[i].url);assert(strlen(stock.songs[i].url)==strlen("http://127.0.0.1:17831/stream/")+32);}
 assert(player_opens==1&&!last_player.queue&&last_player.index==0&&last_player.source==255&&last_player.mode==2);assert(lookup("nd_row_1"));pass("artist → album → selected track: deep-copied stock queue, stock back-to-player request, list kept");
 click("nd_play_all");await_text("nd_toast","Queued tracks 1–6");assert(play_starts==2&&last_selected==0&&player_opens==2);click("nd_player");assert(player_opens==3);pass("album Play all and navigation-bar back-to-player");
 click("nd_search_open");assert(shown(lookup("nd_search_box"))&&!shown(lookup("nd_title"))&&shown(lookup("nd_search_tracks")));assert(strstr(text("nd_status"),"Search")&&!lookup("nd_rows"));widget_set_text_utf8(lookup("nd_search_text"),"宇多田 & café");click("nd_search");await_rows();assert(!strcmp(row("nd_row_0"),"Track 0")&&!strcmp(prop(lookup("nd_search_tracks"),"style:normal:bg_color"),"#FE7E40"));
 click("nd_more_next");await_rows();assert(!strcmp(row("nd_row_0"),"Track 4")&&!lookup("nd_more_next"));click("nd_more_prev");await_rows();click("nd_search_artists");await_rows();preview("nd_navidrome","search");assert(strstr(row("nd_row_0"),"宇多田")&&!strcmp(prop(lookup("nd_search_artists"),"style:normal:bg_color"),"#FE7E40")&&!strcmp(prop(lookup("nd_search_tracks"),"style:normal:bg_color"),"#131313"));
 click("nd_back");await_rows();assert(!strcmp(text("nd_title"),"Requiem café")&&shown(lookup("nd_title"))&&!shown(lookup("nd_search_box")));pass("Unicode search, remote offset paging, accent tabs and Back restore navigation");
 close_all();click("nd_open_playlists");await_rows();assert(!strcmp(row("nd_row_0"),"Fixture Mix"));click("nd_row_0");await_rows();assert(!strcmp(text("nd_title"),"Fixture Mix"));click("nd_row_0");await_text("nd_toast","Queued tracks");assert(stock.count==3&&play_starts==3);pass("playlists tile → playlist tracks → stock queue playback");
 close_all();assert(!lookup("nd_navidrome"));mode("empty");click("nd_open_artists");await_text("nd_status","Nothing here");assert(!lookup("nd_row_0")&&shown(lookup("nd_status")));pass("Back at root closes; empty result message");
 mode("malformed");click("nd_back");click("nd_open_artists");await_text("nd_status","Invalid bridge response");assert(shown(lookup("nd_retry")));mode("normal");click("nd_retry");await_rows();mode("schema");click("nd_back");click("nd_open_artists");await_text("nd_status","Invalid bridge response");assert(shown(lookup("nd_retry")));mode("normal");click("nd_retry");await_rows();pass("malformed JSON and schema shown with working Retry");
 mode("error");close_all();click("nd_open_albums");await_text("nd_status","fixture failure");assert(shown(lookup("nd_retry")));mode("normal");click("nd_retry");await_rows();assert(!strcmp(row("nd_row_0"),"Requiem café"));click("nd_more_next");await_rows();assert(!strcmp(row("nd_row_0"),"Album 4"));click("nd_more_prev");await_rows();pass("upstream HTTP error, Retry and alphabetical album remote paging");
 close_all();click("nd_open_artists");await_rows();snapshot(argv[3]);pass("host mock widget geometry SVG captured with explicit provenance");
 mode("delay");click("nd_row_0");await_inflight();click("nd_back");click("nd_back");assert(!lookup("nd_navidrome"));delay_ms(1400);assert(!lookup("nd_navidrome"));mode("normal");click("nd_open_artists");await_rows();assert(strstr(row("nd_row_0"),"宇多田"));pass("close during delayed HTTP, stale generation discard and clean reopen");
 click("nd_row_0");await_rows();click("nd_row_0");await_rows();int before=play_starts;mode("delay_prepare");click("nd_row_0");await_inflight();close_all();delay_ms(1400);assert(play_starts==before&&!lookup("nd_navidrome"));pass("close during prepare does not start stale playback");
 mode("normal");click("nd_open_search");assert(shown(lookup("nd_search_box"))&&strstr(text("nd_status"),"Search"));click("nd_search");await_text("nd_toast","Enter");click("nd_back");assert(!lookup("nd_navidrome"));pass("search tile opens empty search; empty query rejected; Back closes");
 click("nd_open_artists");await_rows();click("nd_row_0");await_rows();click("nd_row_0");await_rows();before=play_starts;mode("badurl");click("nd_row_0");await_text("nd_toast","Invalid stream address");assert(play_starts==before&&lookup("nd_row_0"));mode("normal");click("nd_row_1");await_text("nd_toast","Queued tracks");assert(play_starts==before+1);pass("foreign credential-bearing stream URL rejected; rows stay usable");close_all();
 click("nd_open_artists");await_rows();assert(!strcmp(lookup("nd_row_0")->kind,"button")&&!strcmp(prop(lookup("nd_row_0"),"style:pressed:bg_color"),"#4A2C22"));widget_set_prop_str(lookup("nd_rows"),"yoffset","120");click("nd_row_0");await_rows();click("nd_back");assert(lookup("nd_row_0")&&!shown(lookup("nd_spinner"))&&!strcmp(text("nd_title"),"Artists"));assert(!strcmp(prop(lookup("nd_rows"),"yoffset"),"120"));pass("rows are instant-press buttons; Back restores the cached list and scroll position without a request");
 click("nd_row_0");await_rows();click("nd_row_0");await_rows();before=play_starts;mode("delay_prepare");click("nd_row_0");await_inflight();mode("normal");click("nd_row_2");await_text("nd_toast","Queued tracks");delay_ms(1300);assert(play_starts==before+1&&last_selected==2);pass("a tap during playback preparation supersedes the earlier one");close_all();
 click("nd_open_artists");await_rows();click("nd_row_0");await_rows();click("nd_row_0");await_rows();assert(!strcmp(icon("nd_download"),"download_more")&&!saved_mark("nd_row_0"));
 click("nd_download");await_text("nd_toast","Downloading to the SD card");await_text("nd_summary","Downloading 3/6");assert(!strcmp(icon("nd_download"),"cancel"));
 await_text("nd_toast","Downloaded");await_rows();assert(saved_mark("nd_row_0")&&saved_mark("nd_row_3")&&strstr(text("nd_summary"),"On SD card · 6 songs")&&!strcmp(icon("nd_download"),"mulselected"));preview("nd_navidrome","album-offline");pass("album download: queued → progress in header → done reloads per-song SD-card marks");
 click("nd_download");await_text("nd_toast","Tap the bin again");assert(!strcmp(icon("nd_download"),"delete_more")&&saved_mark("nd_row_0"));click("nd_download");await_text("nd_toast","Removed from the SD card");await_rows();assert(!saved_mark("nd_row_0")&&!strcmp(icon("nd_download"),"download_more"));pass("two-tap removal from the SD card");
 click("nd_download");await_text("nd_toast","Downloading to the SD card");click("nd_download");await_text("nd_toast","Download cancelled");assert(!strcmp(icon("nd_download"),"download_more"));pass("cancel a running album download");
 close_all();click("nd_open_downloads");await_rows();assert(!strcmp(text("nd_title"),"Downloads")&&strstr(text("nd_jobs_summary"),"saved · 12.0 GB free"));assert(!strcmp(row("nd_job_0"),"Requiem café")&&strstr(text("nd_job_detail"),"Cancelled"));
 click("nd_job_0");await_text("nd_job_detail","On SD card · 6 songs");preview("nd_navidrome","downloads");assert(!strcmp(prop(lookup("nd_job_icon"),"image"),"mulselected"));click("nd_library");assert(library_opens==2);click("nd_job_0");await_rows();assert(!strcmp(text("nd_title"),"Requiem café")&&saved_mark("nd_row_0"));pass("Downloads page: resume a cancelled job in place, Update library, open finished album");
 close_all();mode("dlfail");click("nd_open_playlists");await_rows();click("nd_row_0");await_rows();assert(saved_mark("nd_row_0")&&strstr(text("nd_summary"),"On SD card · 3 songs"));click("nd_download");await_text("nd_toast","Tap the bin again");click("nd_download");await_text("nd_toast","Removed");await_rows();assert(!saved_mark("nd_row_0"));click("nd_download");await_text("nd_toast","Download failed: not enough space");await_rows();assert(strstr(text("nd_summary"),"Download failed")&&!strcmp(icon("nd_download"),"download_more"));mode("normal");close_all();pass("playlist removal, then download failure reported with retry state");
 pthread_mutex_lock(&idle_lock);unsigned posts=idle_worker_posts;pthread_mutex_unlock(&idle_lock);assert(posts>0);printf("HOST INTEGRATION PASS: %d checks; %u worker idle posts; all widget/player operations GUI-thread only.\n",checks,posts);clear(&stock);return 0;
}
