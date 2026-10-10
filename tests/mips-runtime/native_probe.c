#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
void probe_init(void) {}
void probe_fini(void) {}
int main(int argc, char **argv) {
  void *handle, *symbol;
  int (*start)(void) = NULL;
  int result;
  if (argc != 2) return 2;
  if (getenv("ND_EXPECT_PRELOAD_UNSET")) {
    if (getenv("LD_PRELOAD")) { fputs("Startup wrapper did not clear LD_PRELOAD\n", stderr); return 6; }
    puts("PASS startup interception reaches real main and clears LD_PRELOAD");
  }
  handle = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!handle) { fprintf(stderr, "dlopen failed: %s\n", dlerror()); return 3; }
  symbol = dlsym(handle, "nd_ui_start");
  if (!symbol) return 4;
  memcpy(&start, &symbol, sizeof(start));
  result = start();
  if (result != -1) { fprintf(stderr, "Expected missing GUI ABI to fail closed, got %d\n", result); return 5; }
  puts("PASS original MIPS libc/loader dlopen plugin; unavailable GUI ABI fails closed");
  /* The plugin stays loaded for the lifetime of the stock app. This minimal
     crt harness deliberately avoids testing libc/loader process teardown. */
  fflush(stdout);
  _exit(0);
}
