// Public native ABI field layout only, derived from the pinned framework contract.
// Owned-function validation; never register this native module in TikTok.
#include <jni.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>
#include <dlfcn.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
#define EXPORT __attribute__((visibility("default")))
typedef int (*Hook)(void*,void*,void**);
typedef int (*Unhook)(void*);
typedef struct {uint32_t version;Hook hook;Unhook unhook;} NativeAPIEntries;
typedef void (*LibraryLoaded)(const char*,void*);
typedef uint64_t (*Probe)(uintptr_t,uintptr_t,uint64_t,uint64_t);
void phase20_init(uint32_t,void*,void*);
void phase20_loaded(const char*,void*);
jlongArray phase20_control(JNIEnv*,int);
static uint32_t version,initializations;static Hook hook;static Unhook unhook;
static void* owned_handle;static uint64_t owned_loads;
static Probe original;static void* target;static unsigned char bytes[16];
static int installed,last_status,restored;static uint64_t hook_calls,unhook_calls;
static _Atomic uint64_t entries,forwarded,inflight,peak,drops,guard_rejections,recording,errors;
typedef struct {uint64_t id,entry,start,end,exit,queue,context,sequence,depth,result,tid;} Event;
static Event events[256];static _Atomic uint64_t write_index,read_index;
static atomic_flag publishing=ATOMIC_FLAG_INIT;
_Static_assert(ATOMIC_LLONG_LOCK_FREE==2,"callback counters must be lock free");
static uint64_t now(void){struct timespec t;clock_gettime(CLOCK_BOOTTIME,&t);return (uint64_t)t.tv_sec*1000000000ULL+t.tv_nsec;}
static uint64_t replacement(uintptr_t queue,uintptr_t context,uint64_t sequence,uint64_t depth){
    uint64_t active=atomic_fetch_add(&inflight,1)+1,previous=atomic_load(&peak);
    if(active>previous)atomic_compare_exchange_strong(&peak,&previous,active);
    Event e={0};e.id=atomic_fetch_add(&entries,1);e.entry=now();e.queue=queue;e.context=context;e.sequence=sequence;e.depth=depth;e.tid=(uint64_t)syscall(__NR_gettid);
    e.start=now();uint64_t result=original(queue,context,sequence,depth);e.end=now();atomic_fetch_add(&forwarded,1);e.result=result;e.exit=now();
    // Bounded try-publish only; never block original callback if telemetry is full.
    if(!atomic_flag_test_and_set_explicit(&publishing,memory_order_acquire)){
        uint64_t w=atomic_load_explicit(&write_index,memory_order_relaxed);
        if(w-atomic_load_explicit(&read_index,memory_order_acquire)<256){events[w%256]=e;atomic_store_explicit(&write_index,w+1,memory_order_release);}
        else atomic_fetch_add(&drops,1);
        atomic_flag_clear_explicit(&publishing,memory_order_release);
    }else atomic_fetch_add(&drops,1);
    atomic_fetch_sub(&inflight,1);return result;
}
static void loaded(const char* name,void* handle){
    phase20_loaded(name,handle);
    // Exact basename only. No dlsym, IO, allocation, Java or nested dlopen here:
    // the pinned framework invokes this under its loader/registry lock.
    if(!name||!handle)return;
    const char* base=strrchr(name,'/');base=base?base+1:name;
    if(strcmp(base,"libsarae_native_audio.so")==0){owned_handle=handle;owned_loads++;}
}
EXPORT LibraryLoaded native_init(const NativeAPIEntries* api){
    initializations++;
    if(api){version=api->version;if(version==2){hook=api->hook;unhook=api->unhook;}}
    phase20_init(version,(void*)hook,(void*)unhook);
    return loaded;
}
static void control(int command){
    last_status=0;
    if(command==2){atomic_fetch_add(&guard_rejections,1);last_status=-22;return;} // invalid candidate rejected before framework
    if(command==4){atomic_store(&recording,1);return;}
    if(command==5){atomic_store(&recording,0);return;}
    if(command!=1&&command!=3)return;
    if(atomic_load(&recording)||atomic_load(&inflight)){atomic_fetch_add(&guard_rejections,1);last_status=-16;return;}
    if(version!=2||!hook||!unhook||!owned_handle){atomic_fetch_add(&guard_rejections,1);last_status=-20;return;}
    if(command==1){
        if(installed){last_status=-17;return;}
        void* candidate=dlsym(owned_handle,"sarae_owned_probe_v19");
        Dl_info info;
        if(!candidate||!dladdr(candidate,&info)||!info.dli_fname||!strstr(info.dli_fname,"co.sarae.camera.nativeaudiotest")){
            atomic_fetch_add(&guard_rejections,1);last_status=-22;return;
        }
        target=candidate;memcpy(bytes,target,sizeof(bytes));hook_calls++;
        last_status=hook(target,(void*)replacement,(void**)&original);
        if(last_status==0&&original){installed=1;}else{atomic_fetch_add(&errors,1);}
    }else if(installed){
        unhook_calls++;last_status=unhook(target);
        if(last_status==0){installed=0;restored=memcmp(bytes,target,sizeof(bytes))==0;}else atomic_fetch_add(&errors,1);
    }
}
JNIEXPORT jlongArray JNICALL Java_co_sarae_camera_observation_NativeHookValidation_control(JNIEnv* env,jobject self,jint command){
    if(command>=20)return phase20_control(env,command);
    (void)self;control(command);
    const jlong values[]={version,initializations,hook&&unhook,owned_loads,installed,last_status,
        atomic_load(&entries),atomic_load(&forwarded),atomic_load(&inflight),atomic_load(&peak),atomic_load(&drops),atomic_load(&errors),
        atomic_load(&recording),atomic_load(&guard_rejections),(jlong)(uintptr_t)original,(jlong)(uintptr_t)target,
        (jlong)(uintptr_t)hook,(jlong)(uintptr_t)unhook,hook_calls,unhook_calls,restored};
    jlongArray out=(*env)->NewLongArray(env,21);if(out)(*env)->SetLongArrayRegion(env,out,0,21,values);return out;
}
JNIEXPORT jlongArray JNICALL Java_co_sarae_camera_observation_NativeHookValidation_poll(JNIEnv* env,jobject self){
    (void)self;uint64_t r=atomic_load_explicit(&read_index,memory_order_relaxed),w=atomic_load_explicit(&write_index,memory_order_acquire);
    uint64_t count=w-r;if(count>256)count=256;
    jlongArray out=(*env)->NewLongArray(env,(jsize)(count*11));if(!out)return NULL;
    for(uint64_t i=0;i<count;i++)(*env)->SetLongArrayRegion(env,out,(jsize)(i*11),11,(const jlong*)&events[(r+i)%256]);
    atomic_store_explicit(&read_index,r+count,memory_order_release);return out;
}
