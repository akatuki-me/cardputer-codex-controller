#include "bringup_protocol.h"

#include <cstring>

namespace cardputer_bringup {

void BringupLineReceiver::feed(
    const std::uint8_t* bytes,
    std::size_t length,
    BringupLineHandler handler,
    void* context
) {
    if (bytes == nullptr || handler == nullptr) {
        return;
    }
    for (std::size_t index = 0; index < length; ++index) {
        const char byte = static_cast<char>(bytes[index]);
        if (byte == '\n') {
            if (discarding_) {
                discarding_ = false;
                length_ = 0;
                continue;
            }
            if (length_ > 0 && buffer_[length_ - 1] == '\r') {
                --length_;
            }
            if (length_ > 0) {
                buffer_[length_] = '\0';
                handler(buffer_, length_, context);
            }
            length_ = 0;
            continue;
        }
        if (discarding_) {
            continue;
        }
        if (length_ >= kBringupHostLineMaxBytes) {
            discarding_ = true;
            length_ = 0;
            ++oversize_lines_;
            continue;
        }
        buffer_[length_++] = byte;
    }
}

bool HostSessionGate::begin(const char* session, std::uint32_t sequence) {
    if (session == nullptr || session[0] == '\0' || std::strlen(session) >= sizeof(session_) ||
        sequence == 0) {
        return false;
    }
    if (std::strcmp(session_, session) != 0) {
        std::strncpy(session_, session, sizeof(session_) - 1);
        session_[sizeof(session_) - 1] = '\0';
        last_sequence_ = 0;
    }
    return accept(sequence);
}

bool HostSessionGate::accept(std::uint32_t sequence) {
    if (session_[0] == '\0' || sequence == 0 || sequence <= last_sequence_) {
        return false;
    }
    last_sequence_ = sequence;
    return true;
}

G0Event G0Tracker::update(bool pressed, std::uint32_t now_ms) {
    if (pressed && !pressed_) {
        pressed_ = true;
        long_reported_ = false;
        pressed_at_ms_ = now_ms;
        return {G0Action::Press, 0};
    }
    if (pressed && !long_reported_ && (now_ms - pressed_at_ms_) >= kBringupLongPressMs) {
        long_reported_ = true;
        return {G0Action::Long, now_ms - pressed_at_ms_};
    }
    if (!pressed && pressed_) {
        pressed_ = false;
        const std::uint32_t held_ms = now_ms - pressed_at_ms_;
        return {long_reported_ ? G0Action::Release : G0Action::Short, held_ms};
    }
    return {};
}

std::uint32_t fnv1a(const char* value, std::size_t length) {
    std::uint32_t checksum = 2166136261u;
    for (std::size_t index = 0; index < length; ++index) {
        checksum ^= static_cast<std::uint8_t>(value[index]);
        checksum *= 16777619u;
    }
    return checksum;
}

}  // namespace cardputer_bringup
