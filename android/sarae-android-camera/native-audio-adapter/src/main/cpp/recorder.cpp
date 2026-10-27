#include <jni.h>
#include <SLES/OpenSLES.h>
#include <SLES/OpenSLES_Android.h>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <memory>
#include <stdexcept>
#include <time.h>
#include "observer_api.h"
#include "native_audio_bridge.h"
#include <dlfcn.h>

// Owned, metadata-only probe. Optional Phase19 test path; never touches PCM.
static std::atomic<bool> probeEnabled{false};
static std::atomic<uint64_t> probeCalls{0},probeMismatches{0};
static std::atomic<bool> enabled20{false};
static std::atomic<uint64_t> calls20{0},queue20{0},context20{0},depth20{0};
static void* ownerPin20=nullptr;
extern "C" void sarae_owned_callback_v20(uintptr_t,uintptr_t);
extern "C" uint64_t sarae_owned_checked_v20(uintptr_t,uintptr_t);
extern "C" uint64_t sarae_owned_branch_v20(uint64_t);
extern "C" void sarae_owned_body_v20(uintptr_t queue,uintptr_t context) {
    calls20.fetch_add(1);queue20=queue;context20=context;
    auto depth=depth20.load();if(depth){depth20=depth-1;sarae_owned_callback_v20(queue,context);}
}
static uint64_t probeValue(uintptr_t queue,uintptr_t context,uint64_t sequence,uint64_t depth) {
    const uint64_t rotated=(uint64_t(context)<<7)|(uint64_t(context)>>57);
    return (uint64_t(queue)^rotated)+(sequence+depth)*17+0x19+depth*(depth+1)/2;
}
extern "C" __attribute__((noinline,visibility("default"))) uint64_t sarae_owned_probe_v19(uintptr_t queue,uintptr_t context,uint64_t sequence,uint64_t depth) {
    probeCalls.fetch_add(1,std::memory_order_relaxed);
    if(depth) {
        // Volatile indirect recursion deliberately exercises the patched entry again.
        auto volatile recursive=&sarae_owned_probe_v19;
        return recursive(queue,context,sequence+1,depth-1)+depth;
    }
    return probeValue(queue,context,sequence,0);
}

namespace {
constexpr size_t Frames=480, Channels=2, Samples=Frames*Channels, Bytes=Samples*2;
using Block=std::array<int16_t,Samples>;
static_assert(std::atomic<uint64_t>::is_always_lock_free);
static_assert(std::atomic<bool>::is_always_lock_free);
// Same domain as Android SystemClock.elapsedRealtimeNanos, including suspend.
uint64_t nowNs() { timespec t{}; clock_gettime(CLOCK_BOOTTIME,&t); return uint64_t(t.tv_sec)*1000000000ULL+t.tv_nsec; }

// Exactly one writer and reader per ring. Slot publication and retirement use
// acquire/release; callback never waits and never overwrites a reader-owned slot.
template<typename T,size_t N> struct Ring {
    std::array<T,N> slots{};
    std::atomic<uint64_t> write{0},read{0};
    uint64_t depth() const { const auto r=read.load(std::memory_order_acquire); return write.load(std::memory_order_acquire)-r; }
    bool push(const T& value) {
        auto w=write.load(std::memory_order_relaxed);
        if(w-read.load(std::memory_order_acquire)>=N) return false;
        slots[w%N]=value; write.store(w+1,std::memory_order_release); return true;
    }
    bool pop(T& value) {
        auto r=read.load(std::memory_order_relaxed);
        if(r==write.load(std::memory_order_acquire)) return false;
        value=slots[r%N]; read.store(r+1,std::memory_order_release); return true;
    }
};
struct Header {
    uint64_t sequence,callbackNs,durationNs,address;
    uint32_t index,missing,enqueueResult,depth;
};
static_assert(sizeof(Header)==48);
struct Packet { Header header{}; Block mic{},delivered{}; };
static_assert(sizeof(Packet)==48+Bytes*2);
void check(SLresult result,const char* operation) {
    if(result!=SL_RESULT_SUCCESS) {
        char message[160]; std::snprintf(message,sizeof(message),"%s: SLresult=%u",operation,unsigned(result));
        throw std::runtime_error(message);
    }
}
struct Recorder {
    SLObjectItf engineObject=nullptr,recorderObject=nullptr;
    SLEngineItf engine=nullptr;
    SLRecordItf record=nullptr;
    SLAndroidSimpleBufferQueueItf queue=nullptr;
    std::array<Block,2> buffers{};
    sarae::audio::NativeAudioBridge replacement;
    Ring<Packet,128> completed;
    std::atomic<bool> running{false};
    std::atomic<uint64_t> callbacks{0},drops{0},missing{0},enqueueFailures{0},inFlight{0};
    uint64_t startNs=0,stopNs=0;
    unsigned next=0;
    const SaraeObserverApi* observerApi=nullptr;
    void* observer=nullptr;
    bool observerExported=false;
    explicit Recorder(int m,const SaraeObserverApi* api=nullptr):
        replacement({48000,Channels,sarae::audio::Encoding::S16Le,Frames},static_cast<sarae::audio::Mode>(m)),observerApi(api) {
        if(m<0||m>2) throw std::runtime_error("invalid mode");
    }
    void open() {
        check(slCreateEngine(&engineObject,0,nullptr,0,nullptr,nullptr),"slCreateEngine");
        check((*engineObject)->Realize(engineObject,SL_BOOLEAN_FALSE),"engine Realize");
        check((*engineObject)->GetInterface(engineObject,SL_IID_ENGINE,&engine),"ENGINE GetInterface");
        SLDataLocator_IODevice device{SL_DATALOCATOR_IODEVICE,SL_IODEVICE_AUDIOINPUT,SL_DEFAULTDEVICEID_AUDIOINPUT,nullptr};
        SLDataSource source{&device,nullptr};
        SLDataLocator_AndroidSimpleBufferQueue locator{SL_DATALOCATOR_ANDROIDSIMPLEBUFFERQUEUE,2};
        SLDataFormat_PCM pcm{SL_DATAFORMAT_PCM,Channels,SL_SAMPLINGRATE_48,SL_PCMSAMPLEFORMAT_FIXED_16,
                            SL_PCMSAMPLEFORMAT_FIXED_16,SL_SPEAKER_FRONT_LEFT|SL_SPEAKER_FRONT_RIGHT,SL_BYTEORDER_LITTLEENDIAN};
        SLDataSink sink{&locator,&pcm};
        const SLInterfaceID ids[]{SL_IID_ANDROIDSIMPLEBUFFERQUEUE};
        const SLboolean required[]{SL_BOOLEAN_TRUE};
        check((*engine)->CreateAudioRecorder(engine,&recorderObject,&source,&sink,1,ids,required),"CreateAudioRecorder");
        check((*recorderObject)->Realize(recorderObject,SL_BOOLEAN_FALSE),"recorder Realize");
        check((*recorderObject)->GetInterface(recorderObject,SL_IID_RECORD,&record),"RECORD GetInterface");
        check((*recorderObject)->GetInterface(recorderObject,SL_IID_ANDROIDSIMPLEBUFFERQUEUE,&queue),"QUEUE GetInterface");
        if(observerApi) {
            observer=observerApi->create(callback,this,queue);
            if(!observer)throw std::runtime_error("observer create failed");
            check((*queue)->RegisterCallback(queue,observerApi->forwarder,observer),"RegisterCallback observed");
        } else check((*queue)->RegisterCallback(queue,callback,this),"RegisterCallback");
        for(auto& buffer:buffers) check((*queue)->Enqueue(queue,buffer.data(),Bytes),"initial Enqueue");
    }
    static void callback(SLAndroidSimpleBufferQueueItf caller,void* context) {
        auto& r=*static_cast<Recorder*>(context);
        r.inFlight.fetch_add(1,std::memory_order_acq_rel);
        if(!r.running.load(std::memory_order_acquire)) { r.inFlight.fetch_sub(1,std::memory_order_release); return; }
        const auto begin=nowNs();
        // OpenSL returns ownership of the oldest queued buffer on completion.
        // Snapshot both variants before Enqueue transfers that same buffer back.
        auto index=r.next; r.next=(r.next+1)%2;
        auto& buffer=r.buffers[index];
        Packet packet{};
        packet.header.sequence=r.callbacks.fetch_add(1,std::memory_order_relaxed);
        if(enabled20.load())sarae_owned_callback_v20(reinterpret_cast<uintptr_t>(caller),reinterpret_cast<uintptr_t>(context));
        if(probeEnabled.load(std::memory_order_relaxed)) {
            auto q=reinterpret_cast<uintptr_t>(caller),c=reinterpret_cast<uintptr_t>(context);
            if(sarae_owned_probe_v19(q,c,packet.header.sequence,0)!=probeValue(q,c,packet.header.sequence,0))probeMismatches.fetch_add(1,std::memory_order_relaxed);
        }
        packet.header.callbackNs=begin;
        packet.header.address=reinterpret_cast<uint64_t>(buffer.data());
        packet.header.index=index;
        packet.mic=buffer;
        const auto delivery=r.replacement.applyCompleted(buffer.data(),Bytes);
        if(delivery==sarae::audio::Delivery::Underrun || delivery==sarae::audio::Delivery::ContractError) {
            packet.header.missing=Frames;r.missing.fetch_add(Frames,std::memory_order_relaxed);
        }
        packet.delivered=buffer;
        packet.header.depth=uint32_t(r.replacement.depth());
        packet.header.enqueueResult=(*caller)->Enqueue(caller,buffer.data(),Bytes);
        if(packet.header.enqueueResult!=SL_RESULT_SUCCESS) r.enqueueFailures.fetch_add(1,std::memory_order_relaxed);
        packet.header.durationNs=nowNs()-begin;
        if(!r.completed.push(packet)) r.drops.fetch_add(1,std::memory_order_relaxed);
        r.inFlight.fetch_sub(1,std::memory_order_release);
    }
    void start() {
        if(running.exchange(true)) throw std::runtime_error("already recording");
        startNs=nowNs();
        try {check((*record)->SetRecordState(record,SL_RECORDSTATE_RECORDING),"SetRecordState RECORDING");}
        catch(...) { running=false; throw; }
    }
    void stop() {
        if(!record||!running.exchange(false)) return;
        auto result=(*record)->SetRecordState(record,SL_RECORDSTATE_STOPPED);
        // Waiting is confined to the control/worker thread, never the callback.
        // Destroy below also synchronizes object teardown with its callbacks.
        while(inFlight.load(std::memory_order_acquire)!=0) { timespec t{0,100000}; nanosleep(&t,nullptr); }
        stopNs=nowNs();
        check(result,"SetRecordState STOPPED");
    }
    ~Recorder() {
        try {stop();} catch(...) {}
        if(recorderObject) {(*recorderObject)->Destroy(recorderObject);recorderObject=nullptr;}
        if(observer) {
            // OpenSL Destroy has synchronized dispatch. Keep the relay for the
            // worker's post-DESTROY window, without retaining the recorder.
            observerApi->retire(observer);
            if(!observerExported)observerApi->destroy(observer);
        }
        if(engineObject) {(*engineObject)->Destroy(engineObject);engineObject=nullptr;}
    }
};
Recorder& get(jlong h) {return *reinterpret_cast<Recorder*>(h);}
void fail(JNIEnv* env,const std::exception& e) {env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),e.what());}
const SaraeObserverApi& api(jlong pointer) {
    if(!pointer)throw std::runtime_error("observer API unavailable: module not loaded");
    const auto& value=*reinterpret_cast<const SaraeObserverApi*>(pointer);
    if(value.magic!=SARAE_OBSERVER_MAGIC||value.version!=SARAE_OBSERVER_VERSION||value.structure_bytes!=sizeof(SaraeObserverApi)||value.packet_bytes!=sizeof(SaraeObservation))
        throw std::runtime_error("incompatible owned-observer ABI");
    return value;
}
}
#define JNI_METHOD(name) Java_co_sarae_camera_nativeaudio_NativeRecorder_##name
extern "C" JNIEXPORT jlong JNICALL JNI_METHOD(pin20)(JNIEnv* env,jobject,jboolean acquire){
    if(acquire&&!ownerPin20){Dl_info info{};if(dladdr(reinterpret_cast<void*>(&sarae_owned_callback_v20),&info))ownerPin20=dlopen(info.dli_fname,RTLD_NOW|RTLD_NOLOAD);
        if(!ownerPin20)env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),dlerror());}
    if(!acquire&&ownerPin20){dlclose(ownerPin20);ownerPin20=nullptr;}return reinterpret_cast<jlong>(ownerPin20);
}
extern "C" JNIEXPORT jlongArray JNICALL JNI_METHOD(probe20)(JNIEnv* env,jobject,jint command){
    uint64_t errors=0;auto before=calls20.load();
    if(command==1)enabled20=true;
    if(command==2)enabled20=false;
    if(command==3)for(int i=0;i<32;i++){depth20=3;errors+=sarae_owned_checked_v20(0x1234,0x5678);}
    if(command==4)for(uint64_t i=0;i<32;i++)if(sarae_owned_branch_v20(i)!=i+7)errors++;
    const jlong values[]={jlong(before),jlong(calls20.load()),jlong(queue20.load()),jlong(context20.load()),jlong(depth20.load()),jlong(errors)};
    auto out=env->NewLongArray(6);env->SetLongArrayRegion(out,0,6,values);return out;
}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(probeEnable)(JNIEnv*,jobject,jboolean enabled){probeEnabled=enabled;}
extern "C" JNIEXPORT jlongArray JNICALL JNI_METHOD(probeSelfTest)(JNIEnv* env,jobject){
    const auto before=probeCalls.load();uint64_t failures=0;
    for(uint64_t i=0;i<32;i++)if(sarae_owned_probe_v19(0x1234,0x5678,i,3)!=probeValue(0x1234,0x5678,i,3))failures++;
    const jlong values[]={jlong(before),jlong(probeCalls.load()),jlong(failures),jlong(probeMismatches.load())};
    auto out=env->NewLongArray(4);env->SetLongArrayRegion(out,0,4,values);return out;
}
extern "C" JNIEXPORT jlongArray JNICALL JNI_METHOD(probeStats)(JNIEnv* env,jobject){
    const jlong values[]={jlong(probeCalls.load()),jlong(probeMismatches.load()),jlong(probeEnabled.load())};
    auto out=env->NewLongArray(3);env->SetLongArrayRegion(out,0,3,values);return out;
}
extern "C" JNIEXPORT jlong JNICALL JNI_METHOD(create)(JNIEnv* env,jobject,jint mode) {
    try {auto p=std::make_unique<Recorder>(mode);p->open();return reinterpret_cast<jlong>(p.release());}
    catch(const std::exception& e) {fail(env,e);return 0;}
}
extern "C" JNIEXPORT jlong JNICALL JNI_METHOD(createObserved)(JNIEnv* env,jobject,jint mode,jlong pointer) {
    try {
        if(mode!=0)throw std::runtime_error("observation only permits MICROPHONE pass-through");
        auto p=std::make_unique<Recorder>(mode,&api(pointer));p->open();p->observerExported=true;return reinterpret_cast<jlong>(p.release());
    } catch(const std::exception& e) {fail(env,e);return 0;}
}
extern "C" JNIEXPORT jlong JNICALL JNI_METHOD(observerToken)(JNIEnv*,jobject,jlong h) {return reinterpret_cast<jlong>(get(h).observer);}
extern "C" JNIEXPORT jlongArray JNICALL JNI_METHOD(observerStats)(JNIEnv* env,jobject,jlong pointer,jlong token) {
    try {uint64_t values[16];api(pointer).stats(reinterpret_cast<void*>(token),values);auto out=env->NewLongArray(16);env->SetLongArrayRegion(out,0,16,reinterpret_cast<const jlong*>(values));return out;}
    catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT jboolean JNICALL JNI_METHOD(pollObservation)(JNIEnv* env,jobject,jlong pointer,jlong token,jobject target) {
    try {
        void* data=env->GetDirectBufferAddress(target);
        if(!data||env->GetDirectBufferCapacity(target)<jlong(sizeof(SaraeObservation)))throw std::runtime_error("observation packet capacity");
        return api(pointer).poll(reinterpret_cast<void*>(token),static_cast<SaraeObservation*>(data));
    }catch(const std::exception& e){fail(env,e);return false;}
}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(releaseObservation)(JNIEnv* env,jobject,jlong pointer,jlong token) {
    try {if(api(pointer).destroy(reinterpret_cast<void*>(token)))throw std::runtime_error("observer release rejected: not retired or callback active");}
    catch(const std::exception& e){fail(env,e);}
}
extern "C" JNIEXPORT jlongArray JNICALL JNI_METHOD(observerLibraryStats)(JNIEnv* env,jobject,jlong pointer) {
    try {uint64_t values[12];api(pointer).global_stats(values);auto out=env->NewLongArray(12);env->SetLongArrayRegion(out,0,12,reinterpret_cast<const jlong*>(values));return out;}
    catch(const std::exception& e){fail(env,e);return nullptr;}
}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(observerShutdown)(JNIEnv* env,jobject,jlong pointer) {
    try {if(api(pointer).shutdown())throw std::runtime_error("observer shutdown rejected: recorder relay still allocated");}
    catch(const std::exception& e){fail(env,e);}
}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(start)(JNIEnv* env,jobject,jlong h) {try{get(h).start();}catch(const std::exception& e){fail(env,e);}}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(stop)(JNIEnv* env,jobject,jlong h) {try{get(h).stop();}catch(const std::exception& e){fail(env,e);}}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(destroy)(JNIEnv*,jobject,jlong h) {delete reinterpret_cast<Recorder*>(h);}
extern "C" JNIEXPORT jint JNICALL JNI_METHOD(inputDepth)(JNIEnv*,jobject,jlong h) {return jint(get(h).replacement.depth());}
extern "C" JNIEXPORT jboolean JNICALL JNI_METHOD(push)(JNIEnv* env,jobject,jlong h,jshortArray source) {
    if(env->GetArrayLength(source)!=Samples) {env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"),"expected 960 S16 samples");return false;}
    Block b{};env->GetShortArrayRegion(source,0,Samples,b.data());auto& bridge=get(h).replacement;
    return bridge.push(bridge.generation(),b.data(),Bytes);
}
extern "C" JNIEXPORT jboolean JNICALL JNI_METHOD(pushGeneration)(JNIEnv* env,jobject,jlong h,jshortArray source,jlong generation) {
    if(env->GetArrayLength(source)!=Samples || generation<=0) {env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"),"expected 960 S16 samples and positive generation");return false;}
    Block b{};env->GetShortArrayRegion(source,0,Samples,b.data());
    return get(h).replacement.push(uint64_t(generation),b.data(),Bytes);
}
extern "C" JNIEXPORT jboolean JNICALL JNI_METHOD(commitGeneration)(JNIEnv*,jobject,jlong h,jlong generation) {
    return generation>0 && get(h).replacement.commitGeneration(uint64_t(generation));
}
extern "C" JNIEXPORT void JNICALL JNI_METHOD(setMode)(JNIEnv* env,jobject,jlong h,jint mode) {
    if(mode<0||mode>2) {env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"),"invalid native audio mode");return;}
    get(h).replacement.setMode(static_cast<sarae::audio::Mode>(mode));
}
extern "C" JNIEXPORT jlongArray JNICALL JNI_METHOD(bridgeStats)(JNIEnv* env,jobject,jlong h) {
    const auto s=get(h).replacement.snapshot();
    const jlong values[]={jlong(s.completed),jlong(s.video),jlong(s.microphone),jlong(s.muted),jlong(s.missingFrames),
        jlong(s.staleBlocks),jlong(s.futureWaits),jlong(s.contractErrors),jlong(s.fullDrops),jlong(s.generation),jlong(s.queuedBlocks)};
    auto out=env->NewLongArray(11);env->SetLongArrayRegion(out,0,11,values);return out;
}
extern "C" JNIEXPORT jintArray JNICALL JNI_METHOD(bridgeCapabilities)(JNIEnv* env,jobject) {
    using namespace sarae::audio;
    const Contract known{48000,Channels,Encoding::S16Le,Frames};
    const jint values[]={jint(capability(Backend::OwnedOpenSl,Attachment::OwnerCompletedBuffer,known)),
        jint(capability(Backend::ForeignOpenSl,Attachment::None,known)),
        jint(capability(Backend::NativeAudioRecord,Attachment::None,known)),
        jint(capability(Backend::AAudio,Attachment::None,known)),
        jint(capability(Backend::SystemInput,Attachment::None,known))};
    auto out=env->NewIntArray(5);env->SetIntArrayRegion(out,0,5,values);return out;
}
extern "C" JNIEXPORT jboolean JNICALL JNI_METHOD(poll)(JNIEnv* env,jobject,jlong h,jobject target) {
    auto* p=env->GetDirectBufferAddress(target);
    if(!p||env->GetDirectBufferCapacity(target)<jlong(sizeof(Packet))) {env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"),"packet capacity");return false;}
    Packet packet{};if(!get(h).completed.pop(packet))return false;std::memcpy(p,&packet,sizeof(packet));return true;
}
extern "C" JNIEXPORT jlongArray JNICALL JNI_METHOD(stats)(JNIEnv* env,jobject,jlong h) {
    auto& r=get(h);SLuint32 state=0;if(r.record)(*r.record)->GetRecordState(r.record,&state);
    const jlong values[]={jlong(r.callbacks.load()),jlong(r.drops.load()),jlong(r.missing.load()),jlong(r.enqueueFailures.load()),jlong(r.inFlight.load()),
        reinterpret_cast<jlong>(r.buffers[0].data()),reinterpret_cast<jlong>(r.buffers[1].data()),reinterpret_cast<jlong>(r.recorderObject),reinterpret_cast<jlong>(r.queue),state,jlong(r.startNs),jlong(r.stopNs)};
    auto out=env->NewLongArray(12);env->SetLongArrayRegion(out,0,12,values);return out;
}
