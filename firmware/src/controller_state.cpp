#include "controller_state.h"

#include <algorithm>
#include <cstring>
#include <iterator>

namespace cardputer_codex {

ControllerState::ControllerState() {
    for (std::size_t index = 0; index < kSlotCount; ++index) {
        slots_[index].slot = static_cast<std::uint8_t>(index + 1);
        std::strncpy(slots_[index].status, "idle", sizeof(slots_[index].status) - 1);
    }
}

bool ControllerState::begin_host_session(const char* session) {
    if (session == nullptr || session[0] == '\0') {
        return false;
    }
    const std::size_t length = std::strlen(session);
    if (length >= sizeof(host_session_)) {
        return false;
    }
    if (std::strcmp(host_session_, session) == 0) {
        return true;
    }

    std::memcpy(host_session_, session, length + 1);
    sequence_seen_ = false;
    last_sequence_ = 0;
    receive_seen_ = false;
    link_state_ = LinkState::Stale;
    service_state_ = ServiceState::Initializing;
    approval_ = {};
    dirty_ = true;
    return true;
}

void ControllerState::note_receive(std::uint32_t now_ms) {
    last_receive_ms_ = now_ms;
    receive_seen_ = true;
    if (protocol_ok_ && link_state_ != LinkState::Active) {
        link_state_ = LinkState::Active;
        dirty_ = true;
    }
}

void ControllerState::update_link(std::uint32_t now_ms) {
    const bool stale = !receive_seen_ || (now_ms - last_receive_ms_) > kLinkStaleAfterMs;
    const LinkState next = stale ? LinkState::Stale : LinkState::Active;
    if (next != link_state_) {
        link_state_ = next;
        dirty_ = true;
    }
}

void ControllerState::set_selected_slot(std::uint8_t slot) {
    if (slot < 1 || slot > kSlotCount || slot == selected_slot_) {
        return;
    }
    selected_slot_ = slot;
    dirty_ = true;
}

void ControllerState::apply_snapshot(
    const SlotState (&slots)[kSlotCount],
    std::uint8_t selected_slot,
    ServiceState service_state
) {
    std::copy(std::begin(slots), std::end(slots), std::begin(slots_));
    selected_slot_ = selected_slot;
    service_state_ = service_state;
    dirty_ = true;
}

void ControllerState::present_approval(const ApprovalState& approval) {
    approval_ = approval;
    dirty_ = true;
}

void ControllerState::resolve_approval(const char* id) {
    if (approval_.active && id != nullptr && std::strcmp(approval_.id, id) == 0) {
        approval_ = {};
        dirty_ = true;
    }
}

void ControllerState::scroll_approval(int delta) {
    if (!approval_.active || approval_.line_count == 0) {
        return;
    }
    const int max_scroll = std::max<int>(0, approval_.line_count - kApprovalVisibleLines);
    const int requested = approval_.scroll_line + delta;
    const int next = std::max(0, std::min(requested, max_scroll));
    if (next != approval_.scroll_line) {
        approval_.scroll_line = static_cast<std::uint8_t>(next);
        dirty_ = true;
    }
    if (approval_.scroll_line + kApprovalVisibleLines >= approval_.line_count) {
        approval_.body_end_reached = true;
    }
}

void ControllerState::set_approval_choice(ApprovalChoice choice) {
    if (!approval_.active) {
        return;
    }
    if (choice == ApprovalChoice::Accept && !approval_.accept_offered) {
        return;
    }
    if (choice == ApprovalChoice::Decline && !approval_.decline_offered) {
        return;
    }
    if (choice != approval_.choice) {
        approval_.choice = choice;
        dirty_ = true;
    }
}

void ControllerState::mark_approval_sending(const char* id) {
    if (approval_.active && id != nullptr && std::strcmp(approval_.id, id) == 0) {
        approval_.sending = true;
        dirty_ = true;
    }
}

bool ControllerState::can_send() const {
    return protocol_ok_ && link_state_ == LinkState::Active &&
           service_state_ == ServiceState::Ready;
}

bool ControllerState::interrupt_allowed(std::size_t index) const {
    if (index >= kSlotCount || !can_send()) {
        return false;
    }
    const SlotState& target = slots_[index];
    return target.turn_active && target.turn_id[0] != '\0';
}

bool ControllerState::accept_allowed(std::uint32_t now_ms, const char* id) const {
    return can_send() && approval_.active && !approval_.sending && approval_.content_complete &&
           approval_.risk_normal && approval_.accept_offered && approval_.body_end_reached &&
           id != nullptr && std::strcmp(approval_.id, id) == 0 &&
           (now_ms - approval_.shown_at_ms) >= kApprovalGuardMs;
}

bool ControllerState::decline_allowed(const char* id) const {
    return can_send() && approval_.active && !approval_.sending && approval_.decline_offered &&
           id != nullptr && std::strcmp(approval_.id, id) == 0;
}

}  // namespace cardputer_codex
