#include "observer_api.h"
#include <jni.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define CAPACITY 256
_Static_assert(ATOMIC_LLONG_LOCK_FREE==2,"64-bit telemetry must be lock-free");
_Static_assert(sizeof(SaraeObservation)==80,"stable packet ABI");
static uint64_t clock_ns(void) {struct timespec t;clock_gettime(CLOCK_BOOTTIME,&t);return (uint64_t)t.tv_sec*1000000000ULL+t.tv_nsec;}
static uint64_t loaded_pid,loaded_tid,loaded_ns;
static _Atomic uint64_t allocated,active,retirements,failures,shutdowns;
typedef struct {
    slAndroidSimpleBufferQueueCallback original;
    void* context;
    SLAndroidSimpleBufferQueueItf queue;
    uint64_t created_ns;
    _Atomic uint64_t entries,forwarded,drops,mismatches,reentrant,in_flight,late,retired,retired_ns,first_tid,tid_changes;
    _Atomic uint64_t read,write;
    atomic_flag producer;
    SaraeObservation packets[CAPACITY];
} Relay;

static void* create(slAndroidSimpleBufferQueueCallback original,void* context,SLAndroidSimpleBufferQueueItf queue) {
    if(!original||!context||!queue) {atomic_fetch_add(&failures,1);return NULL;}
    Relay* r=calloc(1,sizeof(*r));if(!r){atomic_fetch_add(&failures,1);return NULL;}
    r->original=original;r->context=context;r->queue=queue;r->created_ns=clock_ns();
    atomic_init(&r->entries,0);atomic_init(&r->forwarded,0);atomic_init(&r->drops,0);atomic_init(&r->mismatches,0);
    atomic_init(&r->reentrant,0);atomic_init(&r->in_flight,0);atomic_init(&r->late,0);atomic_init(&r->retired,0);
    atomic_init(&r->retired_ns,0);atomic_init(&r->first_tid,0);atomic_init(&r->tid_changes,0);atomic_init(&r->read,0);atomic_init(&r->write,0);
    atomic_flag_clear(&r->producer);atomic_fetch_add(&allocated,1);atomic_fetch_add(&active,1);return r;
}
static void forward(SLAndroidSimpleBufferQueueItf queue,void* context) {
    Relay* r=context;
    atomic_fetch_add_explicit(&r->in_flight,1,memory_order_acq_rel);
    if(atomic_load_explicit(&r->retired,memory_order_acquire)) {
        atomic_fetch_add_explicit(&r->late,1,memory_order_relaxed);
        atomic_fetch_sub_explicit(&r->in_flight,1,memory_order_release);return;
    }
    SaraeObservation e={0};e.entry_ns=clock_ns();
    e.sequence=atomic_fetch_add_explicit(&r->entries,1,memory_order_relaxed);
    e.queue=(uintptr_t)queue;e.forwarded_context=(uintptr_t)r->context;e.original_function=(uintptr_t)r->original;e.tid=(uint64_t)gettid();
    uint64_t first=atomic_load_explicit(&r->first_tid,memory_order_relaxed);
    if(!first)atomic_compare_exchange_strong_explicit(&r->first_tid,&first,e.tid,memory_order_relaxed,memory_order_relaxed);
    else if(first!=e.tid)atomic_fetch_add_explicit(&r->tid_changes,1,memory_order_relaxed);
    if(queue!=r->queue){e.flags|=1;atomic_fetch_add_explicit(&r->mismatches,1,memory_order_relaxed);}
    // Never wait for another producer. Concurrent callbacks still forward once,
    // but skip ring publication and explicitly count lost telemetry.
    int exclusive=!atomic_flag_test_and_set_explicit(&r->producer,memory_order_acquire);
    if(!exclusive){e.flags|=2;atomic_fetch_add_explicit(&r->reentrant,1,memory_order_relaxed);}
    e.original_start_ns=clock_ns();
    r->original(queue,r->context);
    e.original_end_ns=clock_ns();
    atomic_fetch_add_explicit(&r->forwarded,1,memory_order_relaxed);
    if(exclusive) {
        uint64_t w=atomic_load_explicit(&r->write,memory_order_relaxed);
        if(w-atomic_load_explicit(&r->read,memory_order_acquire)<CAPACITY) {
            r->packets[w%CAPACITY]=e;
            r->packets[w%CAPACITY].exit_ns=clock_ns();
            atomic_store_explicit(&r->write,w+1,memory_order_release);
        } else atomic_fetch_add_explicit(&r->drops,1,memory_order_relaxed);
        atomic_flag_clear_explicit(&r->producer,memory_order_release);
    } else atomic_fetch_add_explicit(&r->drops,1,memory_order_relaxed);
    atomic_fetch_sub_explicit(&r->in_flight,1,memory_order_release);
}
static void stats(void* relay,uint64_t out[16]) {
    Relay* r=relay;
    const uint64_t values[]={atomic_load(&r->entries),atomic_load(&r->forwarded),atomic_load(&r->drops),atomic_load(&r->mismatches),
        atomic_load(&r->reentrant),atomic_load(&r->in_flight),atomic_load(&r->late),atomic_load(&r->retired),
        (uintptr_t)r->original,(uintptr_t)r->context,(uintptr_t)r->queue,(uintptr_t)r,r->created_ns,atomic_load(&r->retired_ns),atomic_load(&r->first_tid),atomic_load(&r->tid_changes)};
    memcpy(out,values,sizeof(values));
}
static int poll(void* relay,SaraeObservation* out) {
    Relay* r=relay;uint64_t index=atomic_load_explicit(&r->read,memory_order_relaxed);
    if(index==atomic_load_explicit(&r->write,memory_order_acquire))return 0;
    *out=r->packets[index%CAPACITY];atomic_store_explicit(&r->read,index+1,memory_order_release);return 1;
}
// Owner must first destroy its OpenSL object, which joins callback dispatch.
// Relay stays alive for a post-DESTROY observation window before free().
static int retire(void* relay) {
    Relay* r=relay;
    if(atomic_load(&r->in_flight)){atomic_fetch_add(&failures,1);return -1;}
    if(atomic_exchange(&r->retired,1))return 0;
    atomic_store(&r->retired_ns,clock_ns());atomic_fetch_sub(&active,1);atomic_fetch_add(&retirements,1);return 0;
}
static int destroy(void* relay) {
    Relay* r=relay;
    if(!atomic_load(&r->retired)||atomic_load(&r->in_flight)){atomic_fetch_add(&failures,1);return -1;}
    free(r);atomic_fetch_sub(&allocated,1);return 0;
}
static void global_stats(uint64_t out[12]) {
    const uint64_t values[]={SARAE_OBSERVER_MAGIC,SARAE_OBSERVER_VERSION,loaded_pid,loaded_tid,(uint64_t)getpid(),(uint64_t)gettid(),
        loaded_ns,atomic_load(&allocated),atomic_load(&active),atomic_load(&retirements),atomic_load(&failures),atomic_load(&shutdowns)};
    memcpy(out,values,sizeof(values));
}
static int shutdown_observer(void) {
    if(atomic_load(&allocated)||atomic_load(&active)){atomic_fetch_add(&failures,1);return -1;}
    atomic_fetch_add(&shutdowns,1);return 0;
}
static const SaraeObserverApi api={SARAE_OBSERVER_MAGIC,SARAE_OBSERVER_VERSION,sizeof(SaraeObserverApi),sizeof(SaraeObservation),
    create,forward,stats,poll,retire,destroy,global_stats,shutdown_observer};
JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm,void* reserved) {
    (void)vm;(void)reserved;loaded_pid=getpid();loaded_tid=gettid();loaded_ns=clock_ns();return JNI_VERSION_1_6;
}
JNIEXPORT jlong JNICALL Java_co_sarae_camera_observation_NativeObservation_apiAddress(JNIEnv* env,jobject self) {
    (void)env;(void)self;return (jlong)(uintptr_t)&api;
}
// Control-thread diagnostics only. Reuses the Phase 17 API; never a callback.
JNIEXPORT jlongArray JNICALL Java_co_sarae_camera_observation_NativeObservation_diagnostics(JNIEnv* env,jobject self) {
    (void)self;uint64_t values[12];global_stats(values);
    jlongArray result=(*env)->NewLongArray(env,12);
    if(result)(*env)->SetLongArrayRegion(env,result,0,12,(const jlong*)values);
    return result;
}
