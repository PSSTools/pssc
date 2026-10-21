#include <stdio.h>
#include <stdlib.h>
#include "tb_mem.h"

/* A word not yet written reads as PRESET, so a read-modify-write that
 * dropped the bits it does not own would show in the trace. */
#define PRESET 0xA5A5A5A4u
#define WORDS 64

static uintptr_t addrs[WORDS];
static uint32_t words[WORDS];
static int n_words;
static uintptr_t busy_addr;
static int busy_reads;

static uint32_t *word(uintptr_t addr) {
    for (int i = 0; i < n_words; i++)
        if (addrs[i] == addr)
            return &words[i];
    if (n_words == WORDS) {
        fprintf(stderr, "tb_mem: out of words\n");
        exit(2);
    }
    addrs[n_words] = addr;
    words[n_words] = PRESET;
    return &words[n_words++];
}

uint32_t tb_read32(uintptr_t addr) {
    uint32_t v = *word(addr);
    if (addr == busy_addr)
        v = busy_reads-- > 0 ? (v | 1u) : (v & ~1u);
    printf("R 0x%lx 0x%08x\n", (unsigned long)addr, (unsigned)v);
    return v;
}

void tb_write32(uintptr_t addr, uint32_t data) {
    *word(addr) = data;
    printf("W 0x%lx 0x%08x\n", (unsigned long)addr, (unsigned)data);
}

void tb_busy(uintptr_t addr, int reads) {
    busy_addr = addr;
    busy_reads = reads;
}
