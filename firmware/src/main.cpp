#include "controller_state.h"
#include "device_link.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <M5Cardputer.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace cardputer_codex {
namespace {

constexpr std::size_t kTxQueueDepth = 4;
constexpr std::uint32_t kHelloRetryMs = 2000;
constexpr std::uint32_t kRenderRefreshMs = 500;

struct TxFrame {
    std::array<std::uint8_t, kDeviceToHostMaxBytes + 1> bytes = {};
    std::size_t length = 0;
    std::size_t offset = 0;
};

class TxQueue {
public:
    bool send_hello() {
        JsonDocument message;
        common(message, "hello");
        message["proto"] = kDeviceLinkProtocolVersion;
        message["device"] = "cardputer-adv";
        message["firmware"] = DEVICE_LINK_FIRMWARE_VERSION;
        return enqueue(message);
    }

    bool send_pong() {
        JsonDocument message;
        common(message, "pong");
        return enqueue(message);
    }

    bool send_interrupt(std::uint8_t slot, const char* turn_id) {
        JsonDocument message;
        common(message, "interrupt");
        message["slot"] = slot;
        message["turnId"] = turn_id;
        return enqueue(message);
    }

    bool send_decision(const char* approval_id, const char* decision) {
        JsonDocument message;
        common(message, "decision");
        message["deviceApprovalId"] = approval_id;
        message["decision"] = decision;
        return enqueue(message);
    }

    void drain() {
        if (count_ == 0) {
            return;
        }
        TxFrame& frame = frames_[head_];
        const std::size_t remaining = frame.length - frame.offset;
        const std::size_t written = Serial.write(frame.bytes.data() + frame.offset, remaining);
        frame.offset += written;
        if (frame.offset == frame.length) {
            frame = {};
            head_ = (head_ + 1) % kTxQueueDepth;
            --count_;
        }
    }

private:
    void common(JsonDocument& message, const char* type) {
        message["t"] = type;
        message["seq"] = ++sequence_;
    }

    bool enqueue(JsonDocument& message) {
        const std::size_t payload_length = measureJson(message);
        if (payload_length > kDeviceToHostMaxBytes || count_ == kTxQueueDepth) {
            return false;
        }
        TxFrame& frame = frames_[tail_];
        const std::size_t written = serializeJson(
            message,
            reinterpret_cast<char*>(frame.bytes.data()),
            frame.bytes.size()
        );
        if (written != payload_length || written >= frame.bytes.size()) {
            frame = {};
            return false;
        }
        frame.bytes[written] = '\n';
        frame.length = written + 1;
        frame.offset = 0;
        tail_ = (tail_ + 1) % kTxQueueDepth;
        ++count_;
        return true;
    }

    std::array<TxFrame, kTxQueueDepth> frames_ = {};
    std::size_t head_ = 0;
    std::size_t tail_ = 0;
    std::size_t count_ = 0;
    std::uint32_t sequence_ = 0;
};

ControllerState controller;
NdjsonReceiver receiver(controller.counters());
DeviceLinkDispatcher dispatcher(controller);
TxQueue tx_queue;
std::uint32_t dispatch_now_ms = 0;
std::uint32_t last_hello_ms = 0;
std::uint32_t last_render_ms = 0;
std::uint32_t button_down_ms = 0;
bool interrupt_latched = false;

const char* service_label(ServiceState service) {
    switch (service) {
        case ServiceState::Ready:
            return "READY";
        case ServiceState::Initializing:
            return "STARTING";
        case ServiceState::AuthRequired:
            return "AUTH REQUIRED";
        case ServiceState::Down:
            return "SERVICE DOWN";
    }
    return "SERVICE DOWN";
}

std::uint16_t status_color(const SlotState& slot) {
    if (slot.attention == AttentionKind::Approval || slot.attention == AttentionKind::Question) {
        return TFT_ORANGE;
    }
    if (slot.attention == AttentionKind::Error) {
        return TFT_RED;
    }
    if (slot.attention == AttentionKind::Done || std::strcmp(slot.status, "done") == 0) {
        return TFT_GREEN;
    }
    if (slot.turn_active) {
        return TFT_CYAN;
    }
    return TFT_LIGHTGREY;
}

void draw_header(const char* screen) {
    auto& display = M5Cardputer.Display;
    display.fillRect(0, 0, 240, 16, TFT_NAVY);
    display.setTextColor(TFT_WHITE, TFT_NAVY);
    display.setCursor(3, 4);
    display.printf("CODEX  %s", screen);
    display.setTextColor(
        controller.link_state() == LinkState::Active ? TFT_GREEN : TFT_RED,
        TFT_NAVY
    );
    display.setCursor(222, 4);
    display.print(controller.link_state() == LinkState::Active ? "UP" : "--");
}

void render_home() {
    auto& display = M5Cardputer.Display;
    display.fillScreen(TFT_BLACK);
    draw_header("HOME");
    for (std::size_t index = 0; index < kSlotCount; ++index) {
        const SlotState& slot = controller.slot(index);
        const int y = 18 + static_cast<int>(index) * 19;
        const bool selected = controller.selected_slot() == index + 1;
        if (selected) {
            display.fillRect(0, y - 1, 240, 18, 0x2104);
        }
        display.setTextColor(selected ? TFT_WHITE : TFT_LIGHTGREY, selected ? 0x2104 : TFT_BLACK);
        display.setCursor(3, y + 3);
        display.printf("%c%d %-16.16s", selected ? '>' : ' ', slot.slot, slot.label);
        display.setTextColor(status_color(slot), selected ? 0x2104 : TFT_BLACK);
        display.setCursor(187, y + 3);
        display.printf("%-7.7s", slot.status);
    }
}

void render_run(const SlotState& slot) {
    auto& display = M5Cardputer.Display;
    display.fillScreen(TFT_BLACK);
    char header[16] = {};
    std::snprintf(header, sizeof(header), "RUN #%u", slot.slot);
    draw_header(header);
    display.setTextColor(TFT_CYAN, TFT_BLACK);
    display.setTextSize(2);
    display.setCursor(8, 27);
    display.printf("%.16s", slot.label);
    display.setTextSize(1);
    display.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    display.setCursor(8, 58);
    display.printf("turn %.28s", slot.turn_id);
    display.setCursor(8, 78);
    display.printf("status: %.12s", slot.status);
    display.setTextColor(controller.interrupt_allowed(slot.slot - 1) ? TFT_YELLOW : TFT_DARKGREY, TFT_BLACK);
    display.setCursor(8, 106);
    display.print("HOLD BtnA 0.5s TO INTERRUPT");
}

void render_approval() {
    const ApprovalState& approval = controller.approval();
    auto& display = M5Cardputer.Display;
    display.fillScreen(TFT_BLACK);
    draw_header("APPROVE");
    display.setTextColor(TFT_YELLOW, TFT_BLACK);
    display.setCursor(4, 20);
    display.printf("#%u %.12s +%u", approval.slot, approval.kind, approval.pending_count);
    display.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    for (std::size_t row = 0; row < kApprovalVisibleLines; ++row) {
        const std::size_t line = approval.scroll_line + row;
        display.setCursor(4, 38 + static_cast<int>(row) * 16);
        if (line < approval.line_count) {
            display.printf("%.38s", approval.lines[line]);
        }
    }
    display.setCursor(4, 87);
    display.printf("id %.22s", approval.id);
    display.setCursor(4, 104);
    if (approval.sending) {
        display.setTextColor(TFT_CYAN, TFT_BLACK);
        display.print("SENDING - WAIT FOR RESOLVED");
        return;
    }
    display.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    display.print("0 HOLD  ");
    if (approval.accept_offered) {
        display.setTextColor(
            approval.choice == ApprovalChoice::Accept ? TFT_BLACK : TFT_GREEN,
            approval.choice == ApprovalChoice::Accept ? TFT_GREEN : TFT_BLACK
        );
        display.print("A ACCEPT  ");
    }
    if (approval.decline_offered) {
        display.setTextColor(
            approval.choice == ApprovalChoice::Decline ? TFT_WHITE : TFT_RED,
            approval.choice == ApprovalChoice::Decline ? TFT_RED : TFT_BLACK
        );
        display.print("D DECLINE");
    }
    display.setTextColor(TFT_DARKGREY, TFT_BLACK);
    display.setCursor(4, 121);
    display.print("J/K SCROLL, ENTER CONFIRM");
}

void render_overlay() {
    auto& display = M5Cardputer.Display;
    if (controller.link_state() == LinkState::Stale || !controller.protocol_ok()) {
        display.fillRect(18, 43, 204, 49, TFT_MAROON);
        display.drawRect(18, 43, 204, 49, TFT_RED);
        display.setTextColor(TFT_WHITE, TFT_MAROON);
        display.setTextSize(2);
        display.setCursor(47, 53);
        display.print(controller.protocol_ok() ? "LINK STALE" : "PROTO MISMATCH");
        display.setTextSize(1);
        display.setCursor(45, 77);
        display.print("SEND OPERATIONS LOCKED");
    } else if (controller.service_state() != ServiceState::Ready) {
        display.fillRect(18, 43, 204, 49, TFT_DARKGREY);
        display.drawRect(18, 43, 204, 49, TFT_ORANGE);
        display.setTextColor(TFT_WHITE, TFT_DARKGREY);
        display.setTextSize(2);
        display.setCursor(36, 53);
        display.print(service_label(controller.service_state()));
        display.setTextSize(1);
        display.setCursor(45, 77);
        display.print("SEND OPERATIONS LOCKED");
    }
}

void render() {
    if (controller.approval().active) {
        render_approval();
    } else {
        const SlotState& focused = controller.slot(controller.selected_slot() - 1);
        if (focused.turn_active) {
            render_run(focused);
        } else {
            render_home();
        }
    }
    render_overlay();
    controller.clear_dirty();
}

void line_handler(const char* line, std::size_t length, void*) {
    if (dispatcher.dispatch(line, length, dispatch_now_ms) == DispatchAction::SendPong) {
        tx_queue.send_pong();
    }
}

void read_serial(std::uint32_t now_ms) {
    std::uint8_t chunk[256] = {};
    std::size_t count = 0;
    while (Serial.available() > 0 && count < sizeof(chunk)) {
        const int value = Serial.read();
        if (value < 0) {
            break;
        }
        chunk[count++] = static_cast<std::uint8_t>(value);
    }
    if (count > 0) {
        dispatch_now_ms = now_ms;
        receiver.feed(chunk, count, line_handler, nullptr);
    }
}

void handle_keyboard(std::uint32_t now_ms) {
    if (!M5Cardputer.Keyboard.isChange() || !M5Cardputer.Keyboard.isPressed()) {
        return;
    }
    for (char key = '1'; key <= '6'; ++key) {
        if (M5Cardputer.Keyboard.isKeyPressed(key)) {
            controller.set_selected_slot(static_cast<std::uint8_t>(key - '0'));
        }
    }
    if (!controller.approval().active) {
        return;
    }
    if (M5Cardputer.Keyboard.isKeyPressed('j')) {
        controller.scroll_approval(1);
    }
    if (M5Cardputer.Keyboard.isKeyPressed('k')) {
        controller.scroll_approval(-1);
    }
    if (M5Cardputer.Keyboard.isKeyPressed('0')) {
        controller.set_approval_choice(ApprovalChoice::Hold);
    }
    if (M5Cardputer.Keyboard.isKeyPressed('a')) {
        controller.set_approval_choice(ApprovalChoice::Accept);
    }
    if (M5Cardputer.Keyboard.isKeyPressed('d')) {
        controller.set_approval_choice(ApprovalChoice::Decline);
    }
    if (!M5Cardputer.Keyboard.isKeyPressed('\n')) {
        return;
    }

    const ApprovalState& approval = controller.approval();
    if (approval.choice == ApprovalChoice::Accept &&
        controller.accept_allowed(now_ms, approval.id) &&
        tx_queue.send_decision(approval.id, "accept")) {
        controller.mark_approval_sending(approval.id);
    } else if (approval.choice == ApprovalChoice::Decline &&
               controller.decline_allowed(approval.id) &&
               tx_queue.send_decision(approval.id, "decline")) {
        controller.mark_approval_sending(approval.id);
    }
}

void handle_interrupt(std::uint32_t now_ms) {
    if (M5Cardputer.BtnA.wasPressed()) {
        button_down_ms = now_ms;
        interrupt_latched = false;
    }
    if (M5Cardputer.BtnA.wasReleased()) {
        interrupt_latched = false;
    }
    if (!M5Cardputer.BtnA.isPressed() || interrupt_latched ||
        (now_ms - button_down_ms) < 500) {
        return;
    }
    interrupt_latched = true;
    const std::size_t index = controller.selected_slot() - 1;
    const SlotState& focused = controller.slot(index);
    if (controller.interrupt_allowed(index)) {
        tx_queue.send_interrupt(focused.slot, focused.turn_id);
    }
}

}  // namespace
}  // namespace cardputer_codex

void setup() {
    using namespace cardputer_codex;

    auto config = M5.config();
    M5Cardputer.begin(config, true);
    M5Cardputer.Display.setRotation(1);
    M5Cardputer.Display.setTextFont(1);
    M5Cardputer.Display.setTextSize(1);
    M5Cardputer.Display.setBrightness(128);
    Serial.setRxBufferSize(kDeviceLinkSerialRxBufferBytes);
    Serial.begin(115200);

    tx_queue.send_hello();
    last_hello_ms = millis();
    render();
}

void loop() {
    using namespace cardputer_codex;

    const std::uint32_t now_ms = millis();
    M5Cardputer.update();
    read_serial(now_ms);
    controller.update_link(now_ms);
    handle_keyboard(now_ms);
    handle_interrupt(now_ms);

    if (controller.link_state() == LinkState::Stale && (now_ms - last_hello_ms) >= kHelloRetryMs) {
        if (tx_queue.send_hello()) {
            last_hello_ms = now_ms;
        }
    }
    tx_queue.drain();

    if (controller.dirty() || (now_ms - last_render_ms) >= kRenderRefreshMs) {
        render();
        last_render_ms = now_ms;
    }
    delay(2);
}
