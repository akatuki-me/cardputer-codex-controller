#pragma once

#include <cstddef>
#include <cstdint>

namespace cardputer_bringup {

constexpr std::size_t kBringupHostLineMaxBytes = 4096;
constexpr std::size_t kBringupSerialRxBufferBytes = kBringupHostLineMaxBytes * 2;
constexpr std::size_t kBringupSessionMaxBytes = 32;
constexpr std::uint32_t kBringupLongPressMs = 500;

using BringupLineHandler = void (*)(const char* line, std::size_t length, void* context);

class BringupLineReceiver {
public:
    void feed(
        const std::uint8_t* bytes,
        std::size_t length,
        BringupLineHandler handler,
        void* context
    );

    [[nodiscard]] std::size_t buffered_bytes() const { return length_; }
    [[nodiscard]] std::uint32_t oversize_lines() const { return oversize_lines_; }

private:
    char buffer_[kBringupHostLineMaxBytes + 1] = {};
    std::size_t length_ = 0;
    std::uint32_t oversize_lines_ = 0;
    bool discarding_ = false;
};

class HostSessionGate {
public:
    [[nodiscard]] bool begin(const char* session, std::uint32_t sequence);
    [[nodiscard]] bool accept(std::uint32_t sequence);
    [[nodiscard]] const char* session() const { return session_; }

private:
    char session_[kBringupSessionMaxBytes + 1] = {};
    std::uint32_t last_sequence_ = 0;
};

enum class G0Action : std::uint8_t { None, Press, Short, Long, Release };

struct G0Event {
    G0Event(
        G0Action requested_action = G0Action::None,
        std::uint32_t requested_held_ms = 0
    )
        : action(requested_action), held_ms(requested_held_ms) {}

    G0Action action;
    std::uint32_t held_ms;
};

class G0Tracker {
public:
    [[nodiscard]] G0Event update(bool pressed, std::uint32_t now_ms);
    [[nodiscard]] bool pressed() const { return pressed_; }

private:
    bool pressed_ = false;
    bool long_reported_ = false;
    std::uint32_t pressed_at_ms_ = 0;
};

[[nodiscard]] std::uint32_t fnv1a(const char* value, std::size_t length);

}  // namespace cardputer_bringup
