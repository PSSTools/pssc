/*
 * zsp_bridge.h -- pssc C scenario <-> SystemVerilog DPI bridge runtime.
 *
 * Model-independent runtime that owns one zsp_timebase and drives a generated
 * scenario by integer action id. The host (a SV trampoline over DPI, or a C
 * harness) calls create -> spawn(action_id, seed) -> run -> done.
 *
 * `pssc_bridge_dispatch` is generated per model (the ACTION_* spawn switch);
 * it instantiates the selected action's root coroutine and posts it onto the
 * bridge timebase. This header stays free of any model specifics.
 *
 * Companion design: docs/design/pssc-c-bridge-runtime-design.md (Phase C1).
 */
#ifndef INCLUDED_ZSP_BRIDGE_H
#define INCLUDED_ZSP_BRIDGE_H

#include "zsp_alloc.h"
#include "zsp_init_ctxt.h"
#include "zsp_timebase.h"

#ifdef __cplusplus
extern "C" {
#endif

/* A pending blocking-import request: a coroutine called a `target` import and
 * suspended; the SV trampoline runs the import task and completes it. */
#define ZSP_BRIDGE_MAX_ARGS 8
typedef struct zsp_bridge_req_s {
    int                       req_id;
    int                       fn_id;
    int                       argc;
    long long                 argv[ZSP_BRIDGE_MAX_ARGS];
    struct zsp_thread_s      *thread;   /* the suspended coroutine             */
    struct zsp_bridge_req_s  *next;
} zsp_bridge_req_t;

typedef struct zsp_bridge_s {
    zsp_alloc_t       alloc;   /* malloc-backed allocator                      */
    zsp_timebase_t    tb;      /* ready-queue executor (untimed scenario)      */
    zsp_init_ctxt_t   ctxt;    /* {alloc, timebase} handed to <Action>_init    */
    long long         status;  /* last spawned action's status/result (reserved) */
    /* blocking-import mailbox */
    zsp_bridge_req_t *pending_head, *pending_tail;  /* posted, not yet drained  */
    zsp_bridge_req_t *active_head;                   /* drained, awaiting complete */
    int               next_req_id;
} zsp_bridge_t;

/* C -> bridge: a generated import sub-task fills `req` (fn_id/argc/argv) and
 * posts it, which BLOCKs `thread` until zsp_bridge_complete re-wakes it. The
 * request lives in the sub-task's frame (alive while suspended). */
void zsp_bridge_post_request(struct zsp_thread_s *thread, zsp_bridge_req_t *req);

/* SV -> bridge: drain one pending request (args is a handle for zsp_bridge_arg_i);
 * returns 1 if one was dequeued, else 0. */
int  zsp_bridge_next_request(zsp_bridge_t *b, int *req_id, int *fn_id, void **args);
void zsp_bridge_complete(zsp_bridge_t *b, int req_id, long long ret);
long long zsp_bridge_arg_i(void *args, int idx);

/* Generated per model: spawn the root coroutine selected by `action_id`,
 * after seeding any runtime solve. Defined in the generated dispatcher TU. */
void pssc_bridge_dispatch(zsp_bridge_t *b, int action_id, long long seed);

/* Lifecycle */
zsp_bridge_t *zsp_bridge_create(void);
void          zsp_bridge_destroy(zsp_bridge_t *b);

/* Capture the calling SV scope (a `context` DPI import) so the scenario can
 * re-enter SV (exported imports) while it runs. Call once before zsp_bridge_run. */
void zsp_bridge_capture_scope(void);

/* SV -> C: drive the scenario */
void zsp_bridge_spawn(zsp_bridge_t *b, int action_id, long long seed);
void zsp_bridge_run(zsp_bridge_t *b);   /* run the ready queue to quiescence  */
int  zsp_bridge_done(zsp_bridge_t *b);  /* 1 when no pending work remains       */

#ifdef __cplusplus
}
#endif

#endif /* INCLUDED_ZSP_BRIDGE_H */
