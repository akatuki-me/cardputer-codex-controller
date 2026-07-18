#pragma once

#include <cstddef>
#include <cstdint>

namespace cardputer_codex {

constexpr std::size_t kSlotCount = 6;
constexpr std::uint32_t kLinkStaleAfterMs = 6000;
constexpr std::uint32_t kApprovalGuardMs = 300;
constexpr std::size_t kApprovalVisibleLines = 3;

enum class LinkState : std::uint8_t { Stale, Active };
enum class ServiceState : std::uint8_t { Initializing, Ready, Down, AuthRequired };
enum class AttentionKind : std::uint8_t { None, Approval, Question, Error, Done };
enum class ApprovalChoice : std::uint8_t { Hold, Accept, Decline };

struct SlotState {
    std::uint8_t slot = 0;
    char label[25] = {};
    char status[13] = {};
    bool turn_active = false;
    char turn_id[65] = {};
    AttentionKind attention = AttentionKind::None;
};

struct ApprovalState {
    static constexpr std::size_t kMaxLines = 8;
    static constexpr std::size_t kLineBytes = 65;

    bool active = false;
    bool sending = false;
    bool content_complete = false;
    bool risk_normal = false;
    bool accept_offered = false;
    bool decline_offered = false;
    bool body_end_reached = false;
    std::uint8_t slot = 0;
    std::uint8_t line_count = 0;
    std::uint8_t scroll_line = 0;
    std::uint16_t pending_count = 0;
    std::uint32_t shown_at_ms = 0;
    ApprovalChoice choice = ApprovalChoice::Hold;
    char id[65] = {};
    char kind[17] = {};
    char cwd[49] = {};
    char lines[kMaxLines][kLineBytes] = {};
};

struct ProtocolCounters {
    std::uint32_t valid_lines = 0;
    std::uint32_t invalid_utf8 = 0;
    std::uint32_t invalid_json = 0;
    std::uint32_t oversize_lines = 0;
    std::uint32_t old_sequence = 0;
    std::uint32_t unknown_type = 0;
};

class ControllerState {
public:
    ControllerState();

    [[nodiscard]] LinkState link_state() const { return link_state_; }
    [[nodiscard]] ServiceState service_state() const { return service_state_; }
    [[nodiscard]] bool protocol_ok() const { return protocol_ok_; }
    [[nodiscard]] std::uint8_t selected_slot() const { return selected_slot_; }
    [[nodiscard]] std::uint32_t last_sequence() const { return last_sequence_; }
    [[nodiscard]] const SlotState& slot(std::size_t index) const { return slots_[index]; }
    [[nodiscard]] const ApprovalState& approval() const { return approval_; }
    [[nodiscard]] ApprovalState& approval() { return approval_; }
    [[nodiscard]] const ProtocolCounters& counters() const { return counters_; }
    [[nodiscard]] ProtocolCounters& counters() { return counters_; }
    [[nodiscard]] bool dirty() const { return dirty_; }

    void clear_dirty() { dirty_ = false; }
    void mark_dirty() { dirty_ = true; }
    void note_receive(std::uint32_t now_ms);
    void update_link(std::uint32_t now_ms);
    void set_selected_slot(std::uint8_t slot);
    void apply_snapshot(
        const SlotState (&slots)[kSlotCount],
        std::uint8_t selected_slot,
        ServiceState service_state
    );
    void present_approval(const ApprovalState& approval);
    void resolve_approval(const char* id);
    void scroll_approval(int delta);
    void set_approval_choice(ApprovalChoice choice);
    void mark_approval_sending(const char* id);

    [[nodiscard]] bool can_send() const;
    [[nodiscard]] bool interrupt_allowed(std::size_t index) const;
    [[nodiscard]] bool accept_allowed(std::uint32_t now_ms, const char* id) const;
    [[nodiscard]] bool decline_allowed(const char* id) const;

private:
    friend class DeviceLinkDispatcher;

    LinkState link_state_ = LinkState::Stale;
    ServiceState service_state_ = ServiceState::Initializing;
    bool protocol_ok_ = true;
    bool receive_seen_ = false;
    bool sequence_seen_ = false;
    bool dirty_ = true;
    std::uint8_t selected_slot_ = 1;
    std::uint32_t last_receive_ms_ = 0;
    std::uint32_t last_sequence_ = 0;
    SlotState slots_[kSlotCount] = {};
    ApprovalState approval_ = {};
    ProtocolCounters counters_ = {};
};

}  // namespace cardputer_codex
