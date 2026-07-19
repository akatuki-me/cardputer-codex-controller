#pragma once

#include "controller_state.h"

#include <cstddef>
#include <cstdint>

namespace cardputer_codex {

constexpr std::size_t kHostToDeviceMaxBytes = 4096;
constexpr std::size_t kDeviceToHostMaxBytes = 1024;
constexpr std::size_t kDeviceLinkSerialRxBufferBytes = kHostToDeviceMaxBytes * 2;
constexpr std::uint32_t kDeviceLinkProtocolVersion = 1;

using LineHandler = void (*)(const char* line, std::size_t length, void* context);

class NdjsonReceiver {
public:
    explicit NdjsonReceiver(ProtocolCounters& counters) : counters_(counters) {}

    void feed(const std::uint8_t* bytes, std::size_t length, LineHandler handler, void* context);
    [[nodiscard]] std::size_t buffered_bytes() const { return length_; }

private:
    void finish_line(LineHandler handler, void* context);

    ProtocolCounters& counters_;
    char buffer_[kHostToDeviceMaxBytes + 1] = {};
    std::size_t length_ = 0;
    bool discarding_ = false;
};

enum class DispatchAction : std::uint8_t { None, SendPong };

class DeviceLinkDispatcher {
public:
    explicit DeviceLinkDispatcher(ControllerState& state) : state_(state) {}

    DispatchAction dispatch(const char* line, std::size_t length, std::uint32_t now_ms);

private:
    ControllerState& state_;
};

[[nodiscard]] bool is_valid_utf8(const std::uint8_t* bytes, std::size_t length);

}  // namespace cardputer_codex
