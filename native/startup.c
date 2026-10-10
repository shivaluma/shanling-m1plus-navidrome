#define _GNU_SOURCE
#include "nd_abi.h"
#include <pthread.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#if defined(__linux__) && !defined(ND_HOST_TEST)
#include <dlfcn.h>
#include <stdio.h>

typedef int (*main_fn)(int, char **, char **);
typedef int (*libc_start_fn)(main_fn, int, char **, void (*)(void),
                           void (*)(void), void (*)(void), void *);
static main_fn original_main;

static void *wait_for_gui(void *unused) {
  unsigned int i;
  struct timespec delay = {0, 50000000L};
  (void)unused;
  /* GUI toolkit initializes inside original_main; do not install a timer here. */
  for (i = 0; i < 300; ++i) {
    int r = nd_ui_schedule_start();
    if (r == 0 || r == -1) return NULL;
    nanosleep(&delay, NULL);
  }
  fputs("Navidrome: GUI startup timed out; extension inactive\n", stderr);
  return NULL;
}

static int wrapped_main(int argc, char **argv, char **envp) {
  pthread_t thread;
  /* hciplayer and helper programs must not inherit this application's hook. */
  unsetenv("LD_PRELOAD");
  if (pthread_create(&thread, NULL, wait_for_gui, NULL) == 0)
    pthread_detach(thread);
  else
    fputs("Navidrome: startup worker unavailable; extension inactive\n", stderr);
  return original_main(argc, argv, envp);
}

__attribute__((visibility("default")))
int __libc_start_main(main_fn main, int argc, char **argv,
                      void (*init)(void), void (*fini)(void),
                      void (*rtld_fini)(void), void *stack_end) {
  libc_start_fn next;
  void *sym = dlsym(RTLD_NEXT, "__libc_start_main");
  /* POSIX dlsym function pointers; memcpy avoids incompatible pointer casts. */
  __builtin_memcpy(&next, &sym, sizeof(next));
  if (!next) _exit(127);
  original_main = main;
  return next(wrapped_main, argc, argv, init, fini, rtld_fini, stack_end);
}
#endif
