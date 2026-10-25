#include <jni.h>
#include <link.h>
#include <elf.h>
#include <dlfcn.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>
typedef int(*Hook)(void*,void*,void**);typedef int(*Unhook)(void*);
typedef void(*Callback)(uintptr_t,uintptr_t);typedef uint64_t(*Branch)(uint64_t);
static Hook hook;static Unhook unhook;static uint32_t version;
typedef struct {char name[768],build[65];uintptr_t bias;ElfW(Phdr) ph[32];size_t n;unsigned count;} Object;
static Object objects[2];static _Atomic uint64_t notifications,generation,notification_drops;
static _Atomic uintptr_t last_handle[2];static uint64_t seen,enumerations;
static int status,ready,installed,restored,mapping_verified;static uintptr_t rva,address;static void* held;
static void* owner_pin;static int borrowed_pin;static char pin_error[768];
static Callback original;static Branch branch_original;static void* branch_target;
static unsigned char before[32],patched[32],trampoline[128],branch_before[32],branch_patched[32],branch_trampoline[128];
static uint64_t hook_calls,unhook_calls,changed_bytes,branch_changed,branch_restored;
static int null_result,null_backup,duplicate_result,duplicate_backup,ambiguous_result;
static _Atomic uint64_t entries,forwards,inflight,peak,drops,errors,busy,branch_entries;
typedef struct {uint64_t id,entry,start,end,exit,queue,context,tid;} Event;
static Event ring[256];static _Atomic uint64_t wi,ri;static atomic_flag publishing=ATOMIC_FLAG_INIT;
static uint64_t now(void){struct timespec t;clock_gettime(CLOCK_BOOTTIME,&t);return (uint64_t)t.tv_sec*1000000000ULL+t.tv_nsec;}
static const char* basename_of(const char* n){const char* p=strrchr(n,'/');return p?p+1:n;}
void phase20_init(uint32_t v,void* h,void* u){version=v;hook=(Hook)h;unhook=(Unhook)u;}
void phase20_loaded(const char* name,void* handle){
    if(!name||!handle)return;const char* base=basename_of(name);int kind;
    if(!strcmp(base,"libsarae_native_audio.so"))kind=0;else if(!strcmp(base,"liblyrax.so"))kind=1;else return;
    // Registry calls are serialized by the pinned framework. Publish only handles
    // and counters; worker does all enumeration, ELF/file/map work and logging.
    if(atomic_exchange(&last_handle[kind],(uintptr_t)handle)!=(uintptr_t)handle){atomic_fetch_add(&notifications,1);atomic_fetch_add_explicit(&generation,1,memory_order_release);}
}
static int in_segment(const Object* o,uintptr_t p,size_t bytes,int flags){
    if(p+bytes<p)return 0;
    for(size_t i=0;i<o->n;i++){const ElfW(Phdr)* h=&o->ph[i];if(h->p_type==PT_LOAD&&(h->p_flags&flags)==(unsigned)flags&&p>=o->bias+h->p_vaddr&&p+bytes<=o->bias+h->p_vaddr+h->p_memsz)return 1;}return 0;
}
static int collect(struct dl_phdr_info* info,size_t size,void* unused){
    (void)size;(void)unused;const char* name=info->dlpi_name;if(!name||!*name)return 0;const char* base=basename_of(name);int kind;
    if(!strcmp(base,"libsarae_native_audio.so"))kind=0;else if(!strcmp(base,"liblyrax.so"))kind=1;else return 0;
    Object* o=&objects[kind];o->count++;if(o->count!=1)return 0;
    if(strlen(name)>=sizeof(o->name)||info->dlpi_phnum>32){o->count=99;return 0;}
    strcpy(o->name,name);o->bias=info->dlpi_addr;o->n=info->dlpi_phnum;memcpy(o->ph,info->dlpi_phdr,o->n*sizeof(ElfW(Phdr)));
    for(size_t i=0;i<o->n;i++)if(o->ph[i].p_type==PT_NOTE&&o->ph[i].p_memsz<=16384){
        uintptr_t p=o->bias+o->ph[i].p_vaddr,end=p+o->ph[i].p_memsz;if(!in_segment(o,p,end-p,PF_R))continue;
        while(p+sizeof(ElfW(Nhdr))<=end){ElfW(Nhdr) h;memcpy(&h,(void*)p,sizeof(h));p+=sizeof(h);
            size_t names=(h.n_namesz+3u)&~3u,desc=(h.n_descsz+3u)&~3u;if(names>16384||desc>16384||p+names+desc>end)break;
            if(h.n_type==NT_GNU_BUILD_ID&&h.n_namesz==4&&!memcmp((void*)p,"GNU",4)&&h.n_descsz<=32){
                const unsigned char* b=(void*)(p+names);for(unsigned j=0;j<h.n_descsz;j++)snprintf(o->build+2*j,3,"%02x",b[j]);
            }p+=names+desc;
        }
    }
    return 0;
}
static void snapshot(void){
    uint64_t g=atomic_load_explicit(&generation,memory_order_acquire);
    if(!enumerations||seen!=g){memset(objects,0,sizeof(objects));enumerations++;dl_iterate_phdr(collect,NULL);seen=g;ready=0;}
}
static int map_matches(Object* o,uintptr_t p,size_t length){
    char backing[768];strcpy(backing,o->name);char* zip=strstr(backing,"!/");if(zip)*zip=0;
    struct stat st;if(stat(backing,&st))return 0;
    FILE* f=fopen("/proc/self/maps","r");if(!f)return 0;char line[1400],perms[5];unsigned long a,b,offset,inode;unsigned maj,min;int match=0;
    while(fgets(line,sizeof(line),f))if(sscanf(line,"%lx-%lx %4s %lx %x:%x %lu",&a,&b,perms,&offset,&maj,&min,&inode)==7&&p>=a&&p+length<=b){
        match=perms[0]=='r'&&perms[2]=='x'&&inode==(unsigned long)st.st_ino&&maj==major(st.st_dev)&&min==minor(st.st_dev);break;
    }
    fclose(f);return match;
}
static int unique(unsigned count){return count==1?0:(count==0?-40:-34);}
static void observer(uintptr_t queue,uintptr_t context){
    uint64_t active=atomic_fetch_add(&inflight,1)+1,old=atomic_load(&peak);if(active>old)atomic_compare_exchange_strong(&peak,&old,active);
    Event e={0};e.id=atomic_fetch_add(&entries,1);e.entry=now();e.queue=queue;e.context=context;e.tid=(uint64_t)syscall(__NR_gettid);
    e.start=now();original(queue,context);e.end=now();atomic_fetch_add(&forwards,1);e.exit=now();
    if(!atomic_flag_test_and_set_explicit(&publishing,memory_order_acquire)){
        uint64_t w=atomic_load(&wi);if(w-atomic_load_explicit(&ri,memory_order_acquire)<256){ring[w%256]=e;atomic_store_explicit(&wi,w+1,memory_order_release);}else atomic_fetch_add(&drops,1);
        atomic_flag_clear_explicit(&publishing,memory_order_release);
    }else atomic_fetch_add(&drops,1);atomic_fetch_sub(&inflight,1);
}
static uint64_t branch_observer(uint64_t value){atomic_fetch_add(&branch_entries,1);return branch_original(value);}
static void copy_trampoline(void* p,unsigned char out[128]){
    FILE* f=fopen("/proc/self/maps","r");if(!f)return;char line[1400],perms[5];unsigned long a,b;
    while(fgets(line,sizeof(line),f))if(sscanf(line,"%lx-%lx %4s",&a,&b,perms)==3&&(uintptr_t)p>=a&&(uintptr_t)p+128<=b&&perms[0]=='r'){memcpy(out,p,128);break;}fclose(f);
}
static void execute(int command){
    status=0;if(command==20){snapshot();return;}if(command==24){atomic_store(&busy,1);return;}if(command==26){atomic_store(&busy,0);return;}
    if(command==29){ambiguous_result=unique(2);status=ambiguous_result;return;} // shared count policy, synthetic snapshot only
    if(command==30){if(installed||branch_original||atomic_load(&inflight)||atomic_load(&busy)){status=-16;return;}if(held&&!borrowed_pin)dlclose(held);held=NULL;owner_pin=NULL;borrowed_pin=0;ready=0;return;}
    if(atomic_load(&busy)||atomic_load(&inflight)){status=-16;return;}
    if(version!=2||!hook||!unhook){status=-20;return;}
    if(command==21){
        if(!ready||seen!=atomic_load(&generation)||installed){status=-45;return;}
        memcpy(before,(void*)address,32);hook_calls++;status=hook((void*)address,(void*)observer,(void**)&original);
        if(status||!original){atomic_fetch_add(&errors,1);return;}installed=1;memcpy(patched,(void*)address,32);copy_trampoline((void*)original,trampoline);
        for(size_t i=0;i<32;i++)if(before[i]!=patched[i])changed_bytes=i+1;
    }else if(command==22){
        // Source-reviewed real engine failure: Dobby rejects null before memory access.
        void* backup=(void*)1;null_result=hook(NULL,(void*)observer,&backup);null_backup=backup==(void*)1;status=null_result;
    }else if(command==23&&installed){
        unhook_calls++;status=unhook((void*)address);if(!status){installed=0;restored=!memcmp(before,(void*)address,32);}else atomic_fetch_add(&errors,1);
    }else if(command==27&&installed){
        unsigned char saved[32];memcpy(saved,(void*)address,32);void* backup=(void*)1;
        duplicate_result=hook((void*)address,(void*)observer,&backup);duplicate_backup=backup==(void*)1&&!memcmp(saved,(void*)address,32);status=duplicate_result;
    }else if(command==25){
        if(installed||!objects[0].count){status=-45;return;}
        branch_target=dlsym((void*)atomic_load(&last_handle[0]),"sarae_owned_branch_v20");
        if(!branch_target||!in_segment(&objects[0],(uintptr_t)branch_target,32,PF_R|PF_X)){status=-46;return;}
        memcpy(branch_before,branch_target,32);hook_calls++;status=hook(branch_target,(void*)branch_observer,(void**)&branch_original);
        if(!status&&branch_original){memcpy(branch_patched,branch_target,32);copy_trampoline((void*)branch_original,branch_trampoline);for(size_t i=0;i<32;i++)if(branch_before[i]!=branch_patched[i])branch_changed=i+1;}else atomic_fetch_add(&errors,1);
    }else if(command==28&&branch_original){
        unhook_calls++;status=unhook(branch_target);if(!status){branch_restored=!memcmp(branch_before,branch_target,32);branch_original=NULL;}else atomic_fetch_add(&errors,1);
    }
}
static jlongArray stats(JNIEnv* env){
    const jlong v[]={version,enumerations,objects[0].count,objects[1].count,atomic_load(&notifications),atomic_load(&notification_drops),status,ready,
        (jlong)objects[0].bias,(jlong)rva,(jlong)address,installed,atomic_load(&entries),atomic_load(&forwards),atomic_load(&inflight),atomic_load(&peak),atomic_load(&drops),atomic_load(&errors),
        null_result,null_backup,duplicate_result,duplicate_backup,restored,hook_calls,unhook_calls,changed_bytes,atomic_load(&generation),seen,atomic_load(&branch_entries),0,0,mapping_verified,held!=NULL,branch_restored,branch_changed,ambiguous_result};
    jlongArray out=(*env)->NewLongArray(env,36);if(out)(*env)->SetLongArrayRegion(env,out,0,36,v);return out;
}
jlongArray phase20_control(JNIEnv* env,int command){execute(command);return stats(env);}
JNIEXPORT jstring JNICALL Java_co_sarae_camera_observation_NativeHookValidation_path20(JNIEnv* env,jobject self,jint kind){
    (void)self;snapshot();return (*env)->NewStringUTF(env,(kind>=0&&kind<2&&objects[kind].count==1)?objects[kind].name:"");
}
JNIEXPORT jlongArray JNICALL Java_co_sarae_camera_observation_NativeHookValidation_resolve20(JNIEnv* env,jobject self,jint kind,jlong va,jstring build,jbyteArray fingerprint){
    (void)self;snapshot();ready=0;mapping_verified=0;status=-22;
    if(kind<0||kind>1||installed)return stats(env);Object* o=&objects[kind];status=unique(o->count);if(status)return stats(env);
    const char* id=(*env)->GetStringUTFChars(env,build,NULL);if(!id)return NULL;
    status=strcmp(id,o->build)?-32:0;(*env)->ReleaseStringUTFChars(env,build,id);if(status)return stats(env);
    if((*env)->GetArrayLength(env,fingerprint)!=32){status=-33;return stats(env);}unsigned char expected[32];(*env)->GetByteArrayRegion(env,fingerprint,0,32,(jbyte*)expected);
    uintptr_t p=o->bias+(uintptr_t)va;
    if(p<o->bias||!in_segment(o,p,32,PF_R|PF_X)||!map_matches(o,p,32)){status=-42;return stats(env);}
    if(memcmp((void*)p,expected,32)){status=-33;return stats(env);}mapping_verified=1;
    if(kind==1){
        // Resolver is observation preparation only; target hook install is absent.
        if(va!=0x6be020){status=-46;return stats(env);}status=0;return stats(env);
    }
    if(p!=(uintptr_t)dlsym((void*)atomic_load(&last_handle[0]),"sarae_owned_callback_v20")){status=-46;return stats(env);}
    if(!held){
        held=dlopen(o->name,RTLD_NOW|RTLD_NOLOAD);
        if(!held){const char* error=dlerror();snprintf(pin_error,sizeof(pin_error),"%s",error?error:"no dlerror");}
    }
    // Explicit cooperative lifetime handoff is allowed only for the owned ELF.
    // This does not grant a target-library pin or fix a foreign namespace.
    if(!held&&owner_pin&&owner_pin==(void*)atomic_load(&last_handle[0])){held=owner_pin;borrowed_pin=1;}
    if(!held){status=-43;return stats(env);} // namespace/lifetime failure: refuse binding
    rva=(uintptr_t)va;address=p;ready=1;status=0;return stats(env);
}
JNIEXPORT jstring JNICALL Java_co_sarae_camera_observation_NativeHookValidation_pinError20(JNIEnv* env,jobject self){(void)self;return (*env)->NewStringUTF(env,pin_error);}
JNIEXPORT void JNICALL Java_co_sarae_camera_observation_NativeHookValidation_ownerPin20(JNIEnv* env,jobject self,jlong handle){
    (void)env;(void)self;if(!installed)owner_pin=(void*)(uintptr_t)handle;
}
JNIEXPORT jlongArray JNICALL Java_co_sarae_camera_observation_NativeHookValidation_reject20(JNIEnv* env,jobject self,jint code){
    (void)self;if(!installed){ready=0;status=code;}else status=-16;return stats(env);
}
JNIEXPORT jlongArray JNICALL Java_co_sarae_camera_observation_NativeHookValidation_poll20(JNIEnv* env,jobject self){
    (void)self;uint64_t r=atomic_load(&ri),w=atomic_load_explicit(&wi,memory_order_acquire),count=w-r;if(count>256)count=256;
    jlongArray out=(*env)->NewLongArray(env,(jsize)count*8);if(!out)return NULL;
    for(uint64_t i=0;i<count;i++)(*env)->SetLongArrayRegion(env,out,(jsize)i*8,8,(const jlong*)&ring[(r+i)%256]);atomic_store_explicit(&ri,r+count,memory_order_release);return out;
}
JNIEXPORT jbyteArray JNICALL Java_co_sarae_camera_observation_NativeHookValidation_code20(JNIEnv* env,jobject self){
    (void)self;unsigned char all[384];memcpy(all,before,32);memcpy(all+32,patched,32);memcpy(all+64,trampoline,128);memcpy(all+192,branch_before,32);memcpy(all+224,branch_patched,32);memcpy(all+256,branch_trampoline,128);
    jbyteArray out=(*env)->NewByteArray(env,384);if(out)(*env)->SetByteArrayRegion(env,out,0,384,(jbyte*)all);return out;
}
