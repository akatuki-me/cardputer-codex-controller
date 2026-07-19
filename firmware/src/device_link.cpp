#include "device_link.h"

#include <ArduinoJson.h>

#include <algorithm>
#include <cstring>
#include <limits>

namespace cardputer_codex {
namespace {

template <std::size_t Size>
void copy_text(char (&destination)[Size], const char* source) {
    destination[0] = '\0';
    if (source == nullptr) {
        return;
    }
    std::strncpy(destination, source, Size - 1);
    destination[Size - 1] = '\0';
}

ServiceState parse_service_state(const char* value) {
    if (value != nullptr && std::strcmp(value, "ready") == 0) {
        return ServiceState::Ready;
    }
    if (value != nullptr && std::strcmp(value, "auth_required") == 0) {
        return ServiceState::AuthRequired;
    }
    if (value != nullptr && std::strcmp(value, "initializing") == 0) {
        return ServiceState::Initializing;
    }
    return ServiceState::Down;
}

AttentionKind parse_attention(const char* value) {
    if (value != nullptr && std::strcmp(value, "approval") == 0) {
        return AttentionKind::Approval;
    }
    if (value != nullptr && std::strcmp(value, "question") == 0) {
        return AttentionKind::Question;
    }
    if (value != nullptr && std::strcmp(value, "error") == 0) {
        return AttentionKind::Error;
    }
    if (value != nullptr && std::strcmp(value, "done") == 0) {
        return AttentionKind::Done;
    }
    return AttentionKind::None;
}

bool is_known_type(const char* type) {
    constexpr const char* known[] = {
        "hello", "state", "detail", "approval", "approval_resolved", "toast", "ping",
    };
    for (const char* candidate : known) {
        if (std::strcmp(type, candidate) == 0) {
            return true;
        }
    }
    return false;
}

bool read_uint32(JsonVariantConst value, std::uint32_t& output) {
    if (value.is<ArduinoJson::JsonUInt>()) {
        const ArduinoJson::JsonUInt parsed = value.as<ArduinoJson::JsonUInt>();
        if (parsed > std::numeric_limits<std::uint32_t>::max()) {
            return false;
        }
        output = static_cast<std::uint32_t>(parsed);
        return true;
    }
    if (!value.is<ArduinoJson::JsonInteger>()) {
        return false;
    }
    const ArduinoJson::JsonInteger parsed = value.as<ArduinoJson::JsonInteger>();
    if (parsed < 0) {
        return false;
    }
    output = static_cast<std::uint32_t>(parsed);
    return true;
}

void apply_state(JsonDocument& document, ControllerState& state) {
    SlotState staged[kSlotCount] = {};
    for (std::size_t index = 0; index < kSlotCount; ++index) {
        staged[index].slot = static_cast<std::uint8_t>(index + 1);
        copy_text(staged[index].status, "idle");
    }

    JsonArrayConst slots = document["slots"].as<JsonArrayConst>();
    for (JsonObjectConst item : slots) {
        const int slot_number = item["slot"] | 0;
        if (slot_number < 1 || slot_number > static_cast<int>(kSlotCount)) {
            continue;
        }
        SlotState& target = staged[slot_number - 1];
        copy_text(target.label, item["label"] | "");
        copy_text(target.status, item["status"] | "idle");
        target.turn_active = item["turnActive"].is<bool>() && item["turnActive"].as<bool>();
        if (target.turn_active) {
            copy_text(target.turn_id, item["turnId"] | "");
        }
        target.attention = parse_attention(item["attentionKind"].as<const char*>());
    }

    const int requested_selected = document["selectedSlot"] | state.selected_slot();
    const std::uint8_t selected = requested_selected >= 1 &&
                                          requested_selected <= static_cast<int>(kSlotCount)
                                      ? static_cast<std::uint8_t>(requested_selected)
                                      : state.selected_slot();
    state.apply_snapshot(
        staged,
        selected,
        parse_service_state(document["serviceState"].as<const char*>())
    );
}

void apply_approval(JsonDocument& document, ControllerState& state, std::uint32_t now_ms) {
    const char* id = document["deviceApprovalId"].as<const char*>();
    if (id == nullptr || id[0] == '\0') {
        state.counters().invalid_json++;
        return;
    }

    ApprovalState next = {};
    next.active = true;
    const int requested_slot = document["slot"] | 0;
    next.slot = static_cast<std::uint8_t>(
        std::max(0, std::min(requested_slot, static_cast<int>(kSlotCount)))
    );
    next.content_complete = document["contentComplete"].is<bool>() &&
                            document["contentComplete"].as<bool>();
    const char* risk = document["riskClass"].as<const char*>();
    next.risk_normal = risk != nullptr && std::strcmp(risk, "normal") == 0;
    next.pending_count = document["pendingCount"] | 0;
    next.shown_at_ms = now_ms;
    copy_text(next.id, id);
    copy_text(next.kind, document["kind"] | "approval");
    copy_text(next.cwd, document["cwd"] | "");

    for (const char* decision : document["decisions"].as<JsonArrayConst>()) {
        if (decision == nullptr) {
            continue;
        }
        if (std::strcmp(decision, "accept") == 0) {
            next.accept_offered = true;
        } else if (std::strcmp(decision, "decline") == 0) {
            next.decline_offered = true;
        }
    }
    next.accept_offered = next.accept_offered && next.content_complete && next.risk_normal;

    for (const char* line : document["lines"].as<JsonArrayConst>()) {
        if (line == nullptr || next.line_count >= ApprovalState::kMaxLines) {
            continue;
        }
        copy_text(next.lines[next.line_count], line);
        ++next.line_count;
    }
    next.body_end_reached = next.line_count <= kApprovalVisibleLines;
    state.present_approval(next);
}

void apply_resolved(JsonDocument& document, ControllerState& state) {
    const char* id = document["deviceApprovalId"].as<const char*>();
    state.resolve_approval(id);
}

}  // namespace

bool is_valid_utf8(const std::uint8_t* bytes, std::size_t length) {
    std::size_t index = 0;
    while (index < length) {
        const std::uint8_t first = bytes[index++];
        if (first <= 0x7F) {
            continue;
        }

        std::uint32_t code_point = 0;
        std::size_t continuation_count = 0;
        if (first >= 0xC2 && first <= 0xDF) {
            code_point = first & 0x1F;
            continuation_count = 1;
        } else if (first >= 0xE0 && first <= 0xEF) {
            code_point = first & 0x0F;
            continuation_count = 2;
        } else if (first >= 0xF0 && first <= 0xF4) {
            code_point = first & 0x07;
            continuation_count = 3;
        } else {
            return false;
        }
        if (index + continuation_count > length) {
            return false;
        }
        for (std::size_t count = 0; count < continuation_count; ++count) {
            const std::uint8_t continuation = bytes[index++];
            if ((continuation & 0xC0) != 0x80) {
                return false;
            }
            code_point = (code_point << 6) | (continuation & 0x3F);
        }
        if ((continuation_count == 2 && code_point < 0x800) ||
            (continuation_count == 3 && code_point < 0x10000) ||
            (code_point >= 0xD800 && code_point <= 0xDFFF) || code_point > 0x10FFFF) {
            return false;
        }
    }
    return true;
}

void NdjsonReceiver::feed(
    const std::uint8_t* bytes,
    std::size_t length,
    LineHandler handler,
    void* context
) {
    if (bytes == nullptr || handler == nullptr) {
        return;
    }
    for (std::size_t index = 0; index < length; ++index) {
        const std::uint8_t byte = bytes[index];
        if (byte == '\n') {
            if (discarding_) {
                discarding_ = false;
                length_ = 0;
                continue;
            }
            finish_line(handler, context);
            continue;
        }
        if (discarding_) {
            continue;
        }
        if (length_ >= kHostToDeviceMaxBytes) {
            discarding_ = true;
            length_ = 0;
            counters_.oversize_lines++;
            continue;
        }
        buffer_[length_++] = static_cast<char>(byte);
    }
}

void NdjsonReceiver::finish_line(LineHandler handler, void* context) {
    if (length_ > 0 && buffer_[length_ - 1] == '\r') {
        --length_;
    }
    if (length_ == 0) {
        return;
    }
    if (!is_valid_utf8(reinterpret_cast<const std::uint8_t*>(buffer_), length_)) {
        counters_.invalid_utf8++;
        length_ = 0;
        return;
    }
    buffer_[length_] = '\0';
    handler(buffer_, length_, context);
    length_ = 0;
}

DispatchAction DeviceLinkDispatcher::dispatch(
    const char* line,
    std::size_t length,
    std::uint32_t now_ms
) {
    JsonDocument document;
    const DeserializationError error = deserializeJson(document, line, length);
    if (error) {
        state_.counters_.invalid_json++;
        return DispatchAction::None;
    }

    const char* type = document["t"].as<const char*>();
    std::uint32_t sequence = 0;
    if (type == nullptr || type[0] == '\0' || !read_uint32(document["seq"], sequence)) {
        state_.counters_.invalid_json++;
        return DispatchAction::None;
    }
    const bool is_hello = std::strcmp(type, "hello") == 0;
    if (is_hello) {
        std::uint32_t protocol_version = 0;
        const char* session = document["session"].as<const char*>();
        const bool valid_protocol = sequence > 0 &&
                                    read_uint32(document["proto"], protocol_version) &&
                                    protocol_version == kDeviceLinkProtocolVersion &&
                                    state_.begin_host_session(session);
        state_.protocol_ok_ = valid_protocol;
        if (!valid_protocol) {
            state_.counters_.invalid_json++;
            state_.link_state_ = LinkState::Stale;
            state_.service_state_ = ServiceState::Down;
            state_.dirty_ = true;
            return DispatchAction::None;
        }
    } else if (state_.host_session_[0] == '\0' || !state_.protocol_ok_) {
        return DispatchAction::None;
    }
    if (state_.sequence_seen_ && sequence <= state_.last_sequence_) {
        state_.counters_.old_sequence++;
        return DispatchAction::None;
    }
    state_.sequence_seen_ = true;
    state_.last_sequence_ = sequence;

    if (!is_known_type(type)) {
        state_.counters_.unknown_type++;
        return DispatchAction::None;
    }

    state_.counters_.valid_lines++;
    state_.note_receive(now_ms);
    if (std::strcmp(type, "state") == 0) {
        apply_state(document, state_);
    } else if (std::strcmp(type, "approval") == 0) {
        apply_approval(document, state_, now_ms);
    } else if (std::strcmp(type, "approval_resolved") == 0) {
        apply_resolved(document, state_);
    } else if (std::strcmp(type, "ping") == 0) {
        return DispatchAction::SendPong;
    }
    return DispatchAction::None;
}

}  // namespace cardputer_codex
