#include "native_audio_bridge.h"

#include <cstring>
#include <stdexcept>

namespace sarae::audio {

static_assert(std::atomic<uint64_t>::is_always_lock_free);
static_assert(std::atomic<Mode>::is_always_lock_free);

uint32_t frameBytes(const Contract& contract) noexcept {
    return contract.channels*static_cast<uint32_t>(contract.encoding);
}

Capability capability(Backend backend,Attachment attachment,const Contract& contract) noexcept {
    if(contract.sampleRate<8000 || contract.sampleRate>192000 || contract.channels<1 || contract.channels>2 ||
       (contract.encoding!=Encoding::U8 && contract.encoding!=Encoding::S16Le && contract.encoding!=Encoding::Float32Le) ||
       contract.maximumFramesPerBuffer==0 || contract.maximumFramesPerBuffer>4096)
        return Capability::InvalidContract;
    if(size_t(contract.maximumFramesPerBuffer)*frameBytes(contract)>kMaximumBlockBytes)
        return Capability::InvalidContract;
    if(backend==Backend::OwnedOpenSl && attachment==Attachment::OwnerCompletedBuffer)
        return Capability::Ready;
    return Capability::Unsupported;
}

NativeAudioBridge::NativeAudioBridge(Contract contract,Mode mode):
    contract_(contract),frameBytes_(frameBytes(contract)),mode_(mode) {
    if(capability(Backend::OwnedOpenSl,Attachment::OwnerCompletedBuffer,contract)!=Capability::Ready ||
       (mode!=Mode::Microphone && mode!=Mode::Video && mode!=Mode::Muted))
        throw std::invalid_argument("invalid owned OpenSL PCM contract");
}

bool NativeAudioBridge::push(uint64_t generation,const void* pcm,size_t bytes) noexcept {
    if(!pcm || generation<generation_.load(std::memory_order_acquire) || bytes==0 ||
       bytes>size_t(contract_.maximumFramesPerBuffer)*frameBytes_ || bytes%frameBytes_!=0) return false;
    const auto w=write_.load(std::memory_order_relaxed);
    if(w-read_.load(std::memory_order_acquire)>=kQueueBlocks) {
        fullDrops_.fetch_add(1,std::memory_order_relaxed);return false;
    }
    auto& slot=slots_[w%kQueueBlocks];
    slot.generation=generation;slot.bytes=static_cast<uint32_t>(bytes);
    std::memcpy(slot.pcm.data(),pcm,bytes);
    write_.store(w+1,std::memory_order_release);
    return true;
}

bool NativeAudioBridge::commitGeneration(uint64_t generation) noexcept {
    const auto old=generation_.load(std::memory_order_acquire);
    if(generation<old || generation==0) return false;
    generation_.store(generation,std::memory_order_release);
    return true;
}

void NativeAudioBridge::setMode(Mode mode) noexcept {
    if(mode==Mode::Microphone || mode==Mode::Video || mode==Mode::Muted)
        mode_.store(mode,std::memory_order_release);
}

uint32_t NativeAudioBridge::framesFor(size_t bytes) const noexcept {
    return frameBytes_ ? static_cast<uint32_t>(bytes/frameBytes_) : 0;
}

void NativeAudioBridge::fillSilence(void* target,size_t bytes) const noexcept {
    std::memset(target,contract_.encoding==Encoding::U8 ? 0x80 : 0,bytes);
}

Delivery NativeAudioBridge::applyCompleted(void* completedBuffer,size_t bytes) noexcept {
    completed_.fetch_add(1,std::memory_order_relaxed);
    if(!completedBuffer || bytes==0 || bytes>size_t(contract_.maximumFramesPerBuffer)*frameBytes_ ||
       bytes%frameBytes_!=0) {
        contractErrors_.fetch_add(1,std::memory_order_relaxed);
        // The owner must stop this recorder on a contract error. Never inspect
        // or write beyond a supplied buffer whose extent cannot be verified.
        return Delivery::ContractError;
    }
    const auto mode=mode_.load(std::memory_order_acquire);
    if(mode==Mode::Microphone) {
        microphone_.fetch_add(1,std::memory_order_relaxed);return Delivery::Microphone;
    }
    if(mode==Mode::Muted) {
        fillSilence(completedBuffer,bytes);muted_.fetch_add(1,std::memory_order_relaxed);return Delivery::Muted;
    }
    const auto active=generation_.load(std::memory_order_acquire);
    for(size_t scanned=0;scanned<kQueueBlocks;scanned++) {
        const auto r=read_.load(std::memory_order_relaxed);
        if(r==write_.load(std::memory_order_acquire)) break;
        const auto& slot=slots_[r%kQueueBlocks];
        if(slot.generation>active) {
            futureWaits_.fetch_add(1,std::memory_order_relaxed);break;
        }
        if(slot.generation<active) {
            read_.store(r+1,std::memory_order_release);
            staleBlocks_.fetch_add(1,std::memory_order_relaxed);continue;
        }
        if(slot.bytes!=bytes) {
            read_.store(r+1,std::memory_order_release);
            contractErrors_.fetch_add(1,std::memory_order_relaxed);
            fillSilence(completedBuffer,bytes);
            missingFrames_.fetch_add(framesFor(bytes),std::memory_order_relaxed);
            return Delivery::ContractError;
        }
        std::memcpy(completedBuffer,slot.pcm.data(),bytes);
        read_.store(r+1,std::memory_order_release);
        video_.fetch_add(1,std::memory_order_relaxed);
        return Delivery::Video;
    }
    fillSilence(completedBuffer,bytes);
    missingFrames_.fetch_add(framesFor(bytes),std::memory_order_relaxed);
    return Delivery::Underrun;
}

uint64_t NativeAudioBridge::depth() const noexcept {
    return write_.load(std::memory_order_acquire)-read_.load(std::memory_order_acquire);
}

uint64_t NativeAudioBridge::generation() const noexcept {
    return generation_.load(std::memory_order_acquire);
}

Snapshot NativeAudioBridge::snapshot() const noexcept {
    return {completed_.load(),video_.load(),microphone_.load(),muted_.load(),missingFrames_.load(),
            staleBlocks_.load(),futureWaits_.load(),contractErrors_.load(),fullDrops_.load(),
            generation_.load(),depth()};
}

} // namespace sarae::audio
