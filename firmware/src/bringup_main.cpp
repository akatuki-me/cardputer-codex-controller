#include "bringup_protocol.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <M5Cardputer.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace cardputer_bringup {
namespace {

constexpr std::uint32_t kHelloIntervalMs = 2000;
constexpr std::uint32_t kHeartbeatIntervalMs = 2000;
constexpr std::uint32_t kHostStaleAfterMs = 6000;
constexpr const char* kMode = "m1-bringup";

BringupLineReceiver receiver;
HostSessionGate host_gate;
G0Tracker g0_tracker;
std::uint32_t tx_sequence = 0;
std::uint32_t last_host_receive_ms = 0;
std::uint32_t last_hello_ms = 0;
std::uint32_t last_heartbeat_ms = 0;
std::uint32_t dispatch_now_ms = 0;
std::uint32_t rx_messages = 0;
std::uint32_t tx_messages = 0;
std::uint32_t errors = 0;
char last_key = '-';
bool linked = false;
bool screen_dirty = true;

bool read_sequence(JsonVariantConst value, std::uint32_t& output) {
    if (!value.is<ArduinoJson::JsonUInt>()) {
        return false;
    }
    const ArduinoJson::JsonUInt parsed = value.as<ArduinoJson::JsonUInt>();
    if (parsed > UINT32_MAX) {
        return false;
    }
    output = static_cast<std::uint32_t>(parsed);
    return true;
}

void send_message(JsonDocument& message) {
    message["seq"] = ++tx_sequence;
    serializeJson(message, Serial);
    Serial.write('\n');
    ++tx_messages;
}

void send_hello() {
    JsonDocument message;
    message["t"] = "hello";
    message["proto"] = 1;
    message["mode"] = kMode;
    message["firmware"] = M1_BRINGUP_FIRMWARE_VERSION;
    message["board"] = static_cast<int>(M5.getBoard());
    message["adv"] = M5.getBoard() == m5::board_t::board_M5CardputerADV;
    message["heap"] = ESP.getFreeHeap();
    send_message(message);
}

void send_ready() {
    JsonDocument message;
    message["t"] = "ready";
    message["mode"] = kMode;
    message["session"] = host_gate.session();
    message["board"] = static_cast<int>(M5.getBoard());
    message["adv"] = M5.getBoard() == m5::board_t::board_M5CardputerADV;
    send_message(message);
}

void send_error(const char* kind) {
    JsonDocument message;
    message["t"] = "error";
    message["kind"] = kind;
    send_message(message);
}

void send_heartbeat(std::uint32_t now_ms) {
    JsonDocument message;
    message["t"] = "heartbeat";
    message["uptimeMs"] = now_ms;
    message["heap"] = ESP.getFreeHeap();
    message["rx"] = rx_messages;
    message["tx"] = tx_messages;
    message["errors"] = errors;
    send_message(message);
}

void send_key(char key) {
    JsonDocument message;
    message["t"] = "key";
    message["code"] = static_cast<std::uint8_t>(key);
    send_message(message);
}

void send_g0(const char* action, std::uint32_t held_ms) {
    JsonDocument message;
    message["t"] = "g0";
    message["action"] = action;
    message["heldMs"] = held_ms;
    send_message(message);
}

void handle_hello(JsonDocument& document, std::uint32_t sequence, std::uint32_t now_ms) {
    const char* mode = document["mode"].as<const char*>();
    const char* session = document["session"].as<const char*>();
    const bool valid = document["proto"].is<int>() && document["proto"].as<int>() == 1 &&
                       mode != nullptr && std::strcmp(mode, kMode) == 0 && session != nullptr &&
                       session[0] != '\0' && std::strlen(session) <= kBringupSessionMaxBytes;
    if (!valid) {
        ++errors;
        send_error("handshake");
        return;
    }

    if (!host_gate.begin(session, sequence)) {
        return;
    }
    last_host_receive_ms = now_ms;
    linked = true;
    screen_dirty = true;
    send_ready();
}

void handle_echo(JsonDocument& document) {
    const char* payload = document["payload"].as<const char*>();
    if (payload == nullptr || !document["id"].is<ArduinoJson::JsonUInt>()) {
        ++errors;
        send_error("echo");
        return;
    }
    const std::size_t payload_bytes = std::strlen(payload);
    JsonDocument response;
    response["t"] = "echo";
    response["id"] = document["id"].as<ArduinoJson::JsonUInt>();
    response["payloadBytes"] = payload_bytes;
    response["checksum"] = fnv1a(payload, payload_bytes);
    send_message(response);
}

void handle_ping(JsonDocument& document) {
    if (!document["id"].is<ArduinoJson::JsonUInt>()) {
        ++errors;
        send_error("ping");
        return;
    }
    JsonDocument response;
    response["t"] = "pong";
    response["id"] = document["id"].as<ArduinoJson::JsonUInt>();
    send_message(response);
}

void process_line(const char* line, std::size_t length, std::uint32_t now_ms) {
    JsonDocument document;
    const DeserializationError parse_error = deserializeJson(document, line, length);
    if (parse_error) {
        ++errors;
        send_error("json");
        return;
    }

    const char* type = document["t"].as<const char*>();
    std::uint32_t sequence = 0;
    if (type == nullptr || !read_sequence(document["seq"], sequence)) {
        ++errors;
        send_error("envelope");
        return;
    }
    ++rx_messages;
    if (std::strcmp(type, "hello") == 0) {
        handle_hello(document, sequence, now_ms);
        return;
    }
    if (!linked || !host_gate.accept(sequence)) {
        return;
    }

    last_host_receive_ms = now_ms;
    if (std::strcmp(type, "echo") == 0) {
        handle_echo(document);
    } else if (std::strcmp(type, "ping") == 0) {
        handle_ping(document);
    } else {
        ++errors;
        send_error("type");
    }
}

void line_handler(const char* line, std::size_t length, void*) {
    process_line(line, length, dispatch_now_ms);
}

void read_serial(std::uint32_t now_ms) {
    std::uint8_t chunk[256] = {};
    const std::uint32_t oversize_before = receiver.oversize_lines();
    dispatch_now_ms = now_ms;
    while (Serial.available() > 0) {
        std::size_t count = 0;
        while (Serial.available() > 0 && count < sizeof(chunk)) {
            const int value = Serial.read();
            if (value < 0) {
                break;
            }
            chunk[count++] = static_cast<std::uint8_t>(value);
        }
        receiver.feed(chunk, count, line_handler, nullptr);
    }
    if (receiver.oversize_lines() != oversize_before) {
        errors += receiver.oversize_lines() - oversize_before;
        send_error("oversize");
    }
}

void handle_keyboard() {
    if (!M5Cardputer.Keyboard.isChange() || !M5Cardputer.Keyboard.isPressed()) {
        return;
    }
    for (char key : M5Cardputer.Keyboard.keysState().word) {
        last_key = key;
        send_key(key);
        screen_dirty = true;
    }
}

void handle_g0(std::uint32_t now_ms) {
    const G0Event event = g0_tracker.update(M5Cardputer.BtnA.isPressed(), now_ms);
    const char* action = nullptr;
    switch (event.action) {
        case G0Action::Press:
            action = "press";
            break;
        case G0Action::Short:
            action = "short";
            break;
        case G0Action::Long:
            action = "long";
            break;
        case G0Action::Release:
            action = "release";
            break;
        case G0Action::None:
            break;
    }
    if (action != nullptr) {
        send_g0(action, event.held_ms);
        screen_dirty = true;
    }
}

void render() {
    auto& display = M5Cardputer.Display;
    display.fillScreen(TFT_BLACK);
    display.setTextColor(TFT_WHITE, TFT_BLACK);
    display.setTextSize(1);
    display.setCursor(4, 5);
    display.printf("M1 BRINGUP %s", M1_BRINGUP_FIRMWARE_VERSION);
    display.setCursor(4, 16);
    display.print("NO CODEX COMMANDS");

    const int board = static_cast<int>(M5.getBoard());
    const bool board_ok = M5.getBoard() == m5::board_t::board_M5CardputerADV;
    display.setCursor(4, 32);
    display.setTextColor(board_ok ? TFT_GREEN : TFT_RED, TFT_BLACK);
    display.printf("BOARD %d  ADV %s", board, board_ok ? "PASS" : "FAIL");

    display.setCursor(4, 49);
    display.setTextColor(linked ? TFT_GREEN : TFT_ORANGE, TFT_BLACK);
    display.printf("LINK %s", linked ? "ACTIVE" : "WAIT/STALE");

    display.setCursor(4, 66);
    display.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
    display.printf("KEY %c  G0 %s", last_key, g0_tracker.pressed() ? "DOWN" : "UP");
    display.setCursor(4, 83);
    display.printf("RX %lu  TX %lu  ERR %lu", static_cast<unsigned long>(rx_messages),
                   static_cast<unsigned long>(tx_messages), static_cast<unsigned long>(errors));
    display.setCursor(4, 100);
    display.printf("HEAP %lu", static_cast<unsigned long>(ESP.getFreeHeap()));
    display.setCursor(4, 118);
    display.print("Type digits; tap/hold G0");
    screen_dirty = false;
}

}  // namespace
}  // namespace cardputer_bringup

void setup() {
    using namespace cardputer_bringup;

    auto config = M5.config();
    M5Cardputer.begin(config, true);
    M5Cardputer.Display.setRotation(1);
    M5Cardputer.Display.setBrightness(128);
    Serial.setRxBufferSize(kBringupSerialRxBufferBytes);
    Serial.begin(115200);
    send_hello();
    last_hello_ms = millis();
    render();
}

void loop() {
    using namespace cardputer_bringup;

    const std::uint32_t now_ms = millis();
    M5Cardputer.update();
    read_serial(now_ms);
    handle_keyboard();
    handle_g0(now_ms);

    if (linked && (now_ms - last_host_receive_ms) > kHostStaleAfterMs) {
        linked = false;
        screen_dirty = true;
    }
    if (!linked && (now_ms - last_hello_ms) >= kHelloIntervalMs) {
        send_hello();
        last_hello_ms = now_ms;
    }
    if (linked && (now_ms - last_heartbeat_ms) >= kHeartbeatIntervalMs) {
        send_heartbeat(now_ms);
        last_heartbeat_ms = now_ms;
    }
    if (screen_dirty) {
        render();
    }
    delay(2);
}
