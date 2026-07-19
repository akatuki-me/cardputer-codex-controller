#include "bringup_protocol.h"
#include "controller_state.h"
#include "device_link.h"

#include <unity.h>

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

using namespace cardputer_codex;
using namespace cardputer_bringup;

namespace {

void collect_line(const char* line, std::size_t length, void* context) {
    auto* lines = static_cast<std::vector<std::string>*>(context);
    lines->emplace_back(line, length);
}

DispatchAction dispatch(DeviceLinkDispatcher& dispatcher, const char* message, std::uint32_t now_ms) {
    return dispatcher.dispatch(message, std::strlen(message), now_ms);
}

void make_ready(ControllerState& state, DeviceLinkDispatcher& dispatcher) {
    dispatch(dispatcher, R"({"t":"hello","seq":0,"proto":1})", 100);
    dispatch(
        dispatcher,
        R"({"t":"state","seq":1,"full":true,"selectedSlot":1,"serviceState":"ready","slots":[{"slot":1,"label":"slot-1","status":"run","turnActive":true,"turnId":"turn-1","attentionKind":null}]})",
        100
    );
    TEST_ASSERT_TRUE(state.can_send());
}

void test_partial_and_multiple_lines_are_framed() {
    ProtocolCounters counters;
    NdjsonReceiver receiver(counters);
    std::vector<std::string> lines;
    const char* first = "{\"t\":\"hello\",";
    const char* second = "\"seq\":1}\n{\"t\":\"ping\",\"seq\":2}\n";

    receiver.feed(
        reinterpret_cast<const std::uint8_t*>(first),
        std::strlen(first),
        collect_line,
        &lines
    );
    TEST_ASSERT_TRUE(lines.empty());
    receiver.feed(
        reinterpret_cast<const std::uint8_t*>(second),
        std::strlen(second),
        collect_line,
        &lines
    );

    TEST_ASSERT_EQUAL_UINT32(2, lines.size());
    TEST_ASSERT_EQUAL_STRING("{\"t\":\"hello\",\"seq\":1}", lines[0].c_str());
    TEST_ASSERT_EQUAL_STRING("{\"t\":\"ping\",\"seq\":2}", lines[1].c_str());
}

void test_invalid_utf8_and_oversize_are_dropped() {
    ProtocolCounters counters;
    NdjsonReceiver receiver(counters);
    std::vector<std::string> lines;
    const std::uint8_t invalid[] = {0xFF, '\n'};
    receiver.feed(invalid, sizeof(invalid), collect_line, &lines);

    std::array<std::uint8_t, kHostToDeviceMaxBytes + 2> oversize = {};
    oversize.fill('x');
    receiver.feed(oversize.data(), oversize.size(), collect_line, &lines);
    const std::uint8_t newline = '\n';
    receiver.feed(&newline, 1, collect_line, &lines);

    TEST_ASSERT_TRUE(lines.empty());
    TEST_ASSERT_EQUAL_UINT32(1, counters.invalid_utf8);
    TEST_ASSERT_EQUAL_UINT32(1, counters.oversize_lines);
    TEST_ASSERT_EQUAL_UINT32(0, receiver.buffered_bytes());
}

void test_unknown_type_advances_sequence_and_old_state_is_ignored() {
    ControllerState state;
    DeviceLinkDispatcher dispatcher(state);
    dispatch(dispatcher, R"({"t":"future","seq":5})", 10);
    dispatch(
        dispatcher,
        R"({"t":"state","seq":4,"serviceState":"ready","slots":[]})",
        20
    );

    TEST_ASSERT_EQUAL_UINT32(0, state.counters().invalid_json);
    TEST_ASSERT_EQUAL_UINT32(5, state.last_sequence());
    TEST_ASSERT_EQUAL_UINT32(1, state.counters().unknown_type);
    TEST_ASSERT_EQUAL_UINT32(1, state.counters().old_sequence);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ServiceState::Initializing),
        static_cast<std::uint8_t>(state.service_state())
    );
}

void test_state_keeps_four_safety_axes_and_stale_locks_send() {
    ControllerState state;
    DeviceLinkDispatcher dispatcher(state);
    make_ready(state, dispatcher);

    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(LinkState::Active),
        static_cast<std::uint8_t>(state.link_state())
    );
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(ServiceState::Ready),
        static_cast<std::uint8_t>(state.service_state())
    );
    TEST_ASSERT_TRUE(state.slot(0).turn_active);
    TEST_ASSERT_EQUAL_STRING("turn-1", state.slot(0).turn_id);
    TEST_ASSERT_TRUE(state.interrupt_allowed(0));

    state.update_link(6101);
    TEST_ASSERT_FALSE(state.can_send());
    TEST_ASSERT_FALSE(state.interrupt_allowed(0));
}

void test_service_not_ready_locks_interrupt() {
    ControllerState state;
    DeviceLinkDispatcher dispatcher(state);
    dispatch(dispatcher, R"({"t":"hello","seq":1,"proto":1})", 50);
    dispatch(
        dispatcher,
        R"({"t":"state","seq":2,"serviceState":"down","slots":[{"slot":1,"turnActive":true,"turnId":"turn-1"}]})",
        50
    );

    TEST_ASSERT_FALSE(state.can_send());
    TEST_ASSERT_FALSE(state.interrupt_allowed(0));
}

void test_ping_requests_pong() {
    ControllerState state;
    DeviceLinkDispatcher dispatcher(state);
    const DispatchAction action = dispatch(dispatcher, R"({"t":"ping","seq":0})", 10);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(DispatchAction::SendPong),
        static_cast<std::uint8_t>(action)
    );
}

void test_accept_requires_guard_end_and_matching_id() {
    ControllerState state;
    DeviceLinkDispatcher dispatcher(state);
    make_ready(state, dispatcher);
    dispatch(
        dispatcher,
        R"({"t":"approval","seq":2,"deviceApprovalId":"approval-1","slot":1,"kind":"command","lines":["one","two","three","four"],"decisions":["accept","decline"],"contentComplete":true,"riskClass":"normal","pendingCount":0})",
        1000
    );

    TEST_ASSERT_FALSE(state.accept_allowed(1299, "approval-1"));
    state.scroll_approval(1);
    TEST_ASSERT_FALSE(state.accept_allowed(1299, "approval-1"));
    TEST_ASSERT_FALSE(state.accept_allowed(1300, "approval-other"));
    TEST_ASSERT_TRUE(state.accept_allowed(1300, "approval-1"));
}

void test_high_risk_and_incomplete_never_offer_accept() {
    ControllerState high_risk;
    DeviceLinkDispatcher high_dispatcher(high_risk);
    make_ready(high_risk, high_dispatcher);
    dispatch(
        high_dispatcher,
        R"({"t":"approval","seq":2,"deviceApprovalId":"high","lines":["full"],"decisions":["accept","decline"],"contentComplete":true,"riskClass":"high"})",
        100
    );
    TEST_ASSERT_FALSE(high_risk.approval().accept_offered);
    TEST_ASSERT_FALSE(high_risk.accept_allowed(1000, "high"));

    ControllerState incomplete;
    DeviceLinkDispatcher incomplete_dispatcher(incomplete);
    make_ready(incomplete, incomplete_dispatcher);
    dispatch(
        incomplete_dispatcher,
        R"({"t":"approval","seq":2,"deviceApprovalId":"cut","lines":["partial"],"decisions":["accept","decline"],"contentComplete":false,"riskClass":"normal"})",
        100
    );
    TEST_ASSERT_FALSE(incomplete.approval().accept_offered);
    TEST_ASSERT_FALSE(incomplete.accept_allowed(1000, "cut"));
}

void test_resolved_requires_matching_id() {
    ControllerState state;
    DeviceLinkDispatcher dispatcher(state);
    make_ready(state, dispatcher);
    dispatch(
        dispatcher,
        R"({"t":"approval","seq":2,"deviceApprovalId":"approval-1","lines":[],"decisions":["decline"],"contentComplete":true,"riskClass":"normal"})",
        100
    );
    dispatch(
        dispatcher,
        R"({"t":"approval_resolved","seq":3,"deviceApprovalId":"other"})",
        200
    );
    TEST_ASSERT_TRUE(state.approval().active);
    dispatch(
        dispatcher,
        R"({"t":"approval_resolved","seq":4,"deviceApprovalId":"approval-1"})",
        300
    );
    TEST_ASSERT_FALSE(state.approval().active);
}

void test_bringup_line_receiver_accepts_4096_and_rejects_4097_bytes() {
    BringupLineReceiver receiver;
    std::vector<std::string> lines;
    std::string exact(kBringupHostLineMaxBytes, 'x');
    exact.push_back('\n');
    receiver.feed(
        reinterpret_cast<const std::uint8_t*>(exact.data()),
        exact.size(),
        collect_line,
        &lines
    );

    TEST_ASSERT_EQUAL_UINT32(1, lines.size());
    TEST_ASSERT_EQUAL_UINT32(kBringupHostLineMaxBytes, lines[0].size());
    TEST_ASSERT_EQUAL_UINT32(0, receiver.oversize_lines());

    std::string oversize(kBringupHostLineMaxBytes + 1, 'y');
    oversize.push_back('\n');
    receiver.feed(
        reinterpret_cast<const std::uint8_t*>(oversize.data()),
        oversize.size(),
        collect_line,
        &lines
    );

    TEST_ASSERT_EQUAL_UINT32(1, lines.size());
    TEST_ASSERT_EQUAL_UINT32(1, receiver.oversize_lines());
    TEST_ASSERT_EQUAL_UINT32(0, receiver.buffered_bytes());
}

void test_usb_rx_buffers_cover_two_maximum_host_lines() {
    TEST_ASSERT_EQUAL_UINT32(
        kHostToDeviceMaxBytes * 2,
        kDeviceLinkSerialRxBufferBytes
    );
    TEST_ASSERT_EQUAL_UINT32(
        kBringupHostLineMaxBytes * 2,
        kBringupSerialRxBufferBytes
    );
}

void test_bringup_session_resets_sequence_only_for_a_new_session() {
    HostSessionGate gate;

    TEST_ASSERT_TRUE(gate.begin("session-a", 1));
    TEST_ASSERT_TRUE(gate.accept(2));
    TEST_ASSERT_FALSE(gate.accept(2));
    TEST_ASSERT_FALSE(gate.begin("session-a", 1));
    TEST_ASSERT_FALSE(gate.begin("session-b", 0));
    TEST_ASSERT_EQUAL_STRING("session-a", gate.session());
    TEST_ASSERT_TRUE(gate.begin("session-b", 1));
    TEST_ASSERT_EQUAL_STRING("session-b", gate.session());
    TEST_ASSERT_FALSE(gate.accept(1));
    TEST_ASSERT_TRUE(gate.accept(2));
}

void test_bringup_checksum_is_fnv1a_32() {
    TEST_ASSERT_EQUAL_HEX32(0x4F9F2CAB, fnv1a("hello", 5));
}

void test_bringup_stale_boundary_has_margin_below_six_seconds() {
    TEST_ASSERT_TRUE(kBringupHostStaleAfterMs < 6000U);
    TEST_ASSERT_FALSE(bringup_host_is_stale(kBringupHostStaleAfterMs - 1U, 0U));
    TEST_ASSERT_TRUE(bringup_host_is_stale(kBringupHostStaleAfterMs, 0U));
}

void test_g0_tracker_distinguishes_499ms_short_and_500ms_long_once() {
    G0Tracker tracker;

    G0Event event = tracker.update(true, 100);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(G0Action::Press),
        static_cast<std::uint8_t>(event.action)
    );
    event = tracker.update(true, 599);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(G0Action::None),
        static_cast<std::uint8_t>(event.action)
    );
    event = tracker.update(false, 599);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(G0Action::Short),
        static_cast<std::uint8_t>(event.action)
    );
    TEST_ASSERT_EQUAL_UINT32(499, event.held_ms);

    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(G0Action::Press),
        static_cast<std::uint8_t>(tracker.update(true, 1000).action)
    );
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(G0Action::None),
        static_cast<std::uint8_t>(tracker.update(true, 1499).action)
    );
    event = tracker.update(true, 1500);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(G0Action::Long),
        static_cast<std::uint8_t>(event.action)
    );
    TEST_ASSERT_EQUAL_UINT32(500, event.held_ms);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(G0Action::None),
        static_cast<std::uint8_t>(tracker.update(true, 1600).action)
    );
    event = tracker.update(false, 1700);
    TEST_ASSERT_EQUAL_UINT8(
        static_cast<std::uint8_t>(G0Action::Release),
        static_cast<std::uint8_t>(event.action)
    );
    TEST_ASSERT_EQUAL_UINT32(700, event.held_ms);
}

}  // namespace

void setUp() {}
void tearDown() {}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_partial_and_multiple_lines_are_framed);
    RUN_TEST(test_invalid_utf8_and_oversize_are_dropped);
    RUN_TEST(test_unknown_type_advances_sequence_and_old_state_is_ignored);
    RUN_TEST(test_state_keeps_four_safety_axes_and_stale_locks_send);
    RUN_TEST(test_service_not_ready_locks_interrupt);
    RUN_TEST(test_ping_requests_pong);
    RUN_TEST(test_accept_requires_guard_end_and_matching_id);
    RUN_TEST(test_high_risk_and_incomplete_never_offer_accept);
    RUN_TEST(test_resolved_requires_matching_id);
    RUN_TEST(test_bringup_line_receiver_accepts_4096_and_rejects_4097_bytes);
    RUN_TEST(test_usb_rx_buffers_cover_two_maximum_host_lines);
    RUN_TEST(test_bringup_session_resets_sequence_only_for_a_new_session);
    RUN_TEST(test_bringup_checksum_is_fnv1a_32);
    RUN_TEST(test_bringup_stale_boundary_has_margin_below_six_seconds);
    RUN_TEST(test_g0_tracker_distinguishes_499ms_short_and_500ms_long_once);
    return UNITY_END();
}
