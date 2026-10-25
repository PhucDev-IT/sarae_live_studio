#ifndef SARAE_OBSERVER_API_H
#define SARAE_OBSERVER_API_H
#include <stdint.h>
#include <stddef.h>
#include <SLES/OpenSLES_Android.h>

#define SARAE_OBSERVER_MAGIC UINT64_C(0x53415241454f4253)
#define SARAE_OBSERVER_VERSION 1
// Cross-library ABI for explicitly registered, owned recorder callbacks only.
// Not a target process search API and not an interception/hook engine.
typedef struct {
    uint64_t sequence,entry_ns,original_start_ns,original_end_ns,exit_ns;
    uint64_t queue,forwarded_context,original_function,tid,flags;
} SaraeObservation;
typedef struct {
    uint64_t magic,version,structure_bytes,packet_bytes;
    void* (*create)(slAndroidSimpleBufferQueueCallback original,void* original_context,SLAndroidSimpleBufferQueueItf queue);
    slAndroidSimpleBufferQueueCallback forwarder;
    void (*stats)(void* relay,uint64_t output[16]);
    int (*poll)(void* relay,SaraeObservation* packet);
    int (*retire)(void* relay);
    int (*destroy)(void* relay);
    void (*global_stats)(uint64_t output[12]);
    int (*shutdown)(void);
} SaraeObserverApi;
#endif
