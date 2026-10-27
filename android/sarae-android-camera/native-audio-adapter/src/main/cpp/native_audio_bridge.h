#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace sarae::audio {

// A native backend is usable only when its owner supplies the completed buffer.
// This is an explicit in-process contract, not cross-application microphone routing.
enum class Backend : uint8_t { OwnedOpenSl=1, ForeignOpenSl=2, NativeAudioRecord=3, AAudio=4, SystemInput=5 };
enum class Attachment : uint8_t { None=0, OwnerCompletedBuffer=1 };
enum class Capability : int32_t { Unsupported=0, Ready=1, InvalidContract=-1 };
enum class Encoding : uint8_t { U8=1, S16Le=2, Float32Le=4 };
enum class Mode : uint8_t { Microphone=0, Video=1, Muted=2 };
enum class Delivery : uint8_t { Microphone=0, Video=1, Muted=2, Underrun=3, ContractError=4 };

struct Contract {
    uint32_t sampleRate;
    uint32_t channels;
    Encoding encoding;
    uint32_t maximumFramesPerBuffer;
};

constexpr size_t kMaximumBlockBytes=32768;
constexpr size_t kQueueBlocks=32;
uint32_t frameBytes(const Contract& contract) noexcept;
Capability capability(Backend backend,Attachment attachment,const Contract& contract) noexcept;

struct Snapshot {
    uint64_t completed;
    uint64_t video;
    uint64_t microphone;
    uint64_t muted;
    uint64_t missingFrames;
    uint64_t staleBlocks;
    uint64_t futureWaits;
    uint64_t contractErrors;
    uint64_t fullDrops;
    uint64_t generation;
    uint64_t queuedBlocks;
};

// One worker writes decoded, pre-converted PCM; one recorder callback reads it.
// The owner invokes applyCompleted only after OpenSL returns the buffer, and
// remains responsible for re-enqueueing that same buffer after this call.
class NativeAudioBridge final {
public:
    NativeAudioBridge(Contract contract,Mode mode);
    bool push(uint64_t generation,const void* pcm,size_t bytes) noexcept;
    bool commitGeneration(uint64_t generation) noexcept;
    void setMode(Mode mode) noexcept;
    Delivery applyCompleted(void* completedBuffer,size_t bytes) noexcept;
    uint64_t depth() const noexcept;
    uint64_t generation() const noexcept;
    Snapshot snapshot() const noexcept;
    uint32_t framesFor(size_t bytes) const noexcept;

private:
    struct Slot {
        uint64_t generation=0;
        uint32_t bytes=0;
        std::array<uint8_t,kMaximumBlockBytes> pcm{};
    };
    void fillSilence(void* target,size_t bytes) const noexcept;
    const Contract contract_;
    const uint32_t frameBytes_;
    std::array<Slot,kQueueBlocks> slots_{};
    std::atomic<uint64_t> write_{0},read_{0},generation_{1};
    std::atomic<Mode> mode_;
    std::atomic<uint64_t> completed_{0},video_{0},microphone_{0},muted_{0};
    std::atomic<uint64_t> missingFrames_{0},staleBlocks_{0},futureWaits_{0};
    std::atomic<uint64_t> contractErrors_{0},fullDrops_{0};
};

} // namespace sarae::audio
