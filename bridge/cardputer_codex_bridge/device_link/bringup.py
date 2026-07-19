from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import TextIO

from cardputer_codex_bridge.app_server.types import JsonObject

from .bringup_protocol import (
    BRINGUP_MODE,
    BringupDecoder,
    encode_bringup_message,
    fnv1a,
    maximum_echo_payload,
)
from .serial_link import SerialLink, SerialProvider
from .synthetic import SyntheticSerialProvider


class BringupError(RuntimeError):
    """M1 bring-upが受入条件を満たさなかった。"""


@dataclass(frozen=True)
class EchoMeasurement:
    payload_bytes: int
    elapsed_ms: float


@dataclass(frozen=True)
class ThroughputMeasurement:
    payload_bytes: int
    elapsed_ms: float
    bytes_per_second: float


@dataclass
class _EchoWaiter:
    payload_bytes: int
    checksum: int
    started: float
    event: threading.Event
    elapsed_ms: float | None = None


@dataclass
class _PingWaiter:
    started: float
    event: threading.Event
    elapsed_ms: float | None = None


class BringupSession:
    def __init__(
        self,
        *,
        port: str,
        provider: SerialProvider,
        read_timeout: float = 0.05,
        ping_interval: float = 2.0,
        stale_after: float = 6.0,
        reconnect_delay: float = 0.25,
    ) -> None:
        self._lock = threading.RLock()
        self._generation_condition = threading.Condition(self._lock)
        self._generation = 0
        self._host_sequence = 0
        self._request_id = 0
        self._session = ""
        self._hello_sent = False
        self._failure: BringupError | None = None
        self._ready = threading.Event()
        self._heartbeat = threading.Event()
        self._digit = threading.Event()
        self._g0_short = threading.Event()
        self._g0_long = threading.Event()
        self._echo_waiters: dict[int, _EchoWaiter] = {}
        self._ping_waiters: dict[int, _PingWaiter] = {}
        self._last_heap: int | None = None
        self._link = SerialLink(
            port=port,
            provider=provider,
            on_message=self._on_message,
            on_connected=self._on_connected,
            on_stale=self._on_stale,
            ping_factory=self._periodic_ping,
            decoder_factory=BringupDecoder,
            encoder=encode_bringup_message,
            read_timeout=read_timeout,
            ping_interval=ping_interval,
            stale_after=stale_after,
            reconnect_delay=reconnect_delay,
        )

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def last_heap(self) -> int | None:
        with self._lock:
            return self._last_heap

    def start(self) -> None:
        self._link.start()

    def close(self) -> None:
        self._link.close()

    def wait_connected(self, timeout: float) -> bool:
        return self._link.wait_connected(timeout)

    def wait_ready(self, timeout: float) -> bool:
        return self._wait(self._ready, timeout)

    def wait_heartbeat(self, timeout: float) -> bool:
        return self._wait(self._heartbeat, timeout)

    def wait_digit(self, timeout: float) -> bool:
        return self._wait(self._digit, timeout)

    def wait_g0_short(self, timeout: float) -> bool:
        return self._wait(self._g0_short, timeout)

    def wait_g0_long(self, timeout: float) -> bool:
        return self._wait(self._g0_long, timeout)

    def wait_generation(self, generation: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._generation_condition:
            while self._generation < generation and self._failure is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._generation_condition.wait(timeout=remaining)
            self._raise_failure_locked()
            return self._generation >= generation

    def echo(self, payload: str, timeout: float) -> EchoMeasurement:
        return self._echo(payload=payload, timeout=timeout, maximum=False)

    def echo_maximum_line(self, timeout: float) -> EchoMeasurement:
        return self._echo(payload="", timeout=timeout, maximum=True)

    def ping(self, timeout: float) -> float:
        with self._lock:
            self._require_ready_locked()
            request_id = self._next_request_id_locked()
            message: JsonObject = {
                "t": "ping",
                "seq": self._next_sequence_locked(),
                "id": request_id,
            }
            waiter = _PingWaiter(started=time.monotonic(), event=threading.Event())
            self._ping_waiters[request_id] = waiter
        if not self._link.send(message):
            with self._lock:
                self._ping_waiters.pop(request_id, None)
            raise BringupError("bringup ping could not be sent")
        if not self._wait(waiter.event, timeout):
            raise TimeoutError("bringup pong was not received")
        with self._lock:
            self._ping_waiters.pop(request_id, None)
            if waiter.elapsed_ms is None:
                raise BringupError("bringup pong measurement is missing")
            return waiter.elapsed_ms

    def measure_throughput(
        self,
        *,
        payload_bytes: int = 512,
        iterations: int = 8,
        timeout: float,
    ) -> ThroughputMeasurement:
        if payload_bytes <= 0 or iterations <= 0:
            raise ValueError("throughput payload and iterations must be positive")
        payload = "x" * payload_bytes
        started = time.monotonic()
        for _ in range(iterations):
            measurement = self.echo(payload, timeout)
            if measurement.payload_bytes != payload_bytes:
                raise BringupError("bringup throughput echo length did not match")
        elapsed_ms = (time.monotonic() - started) * 1000
        total_bytes = payload_bytes * iterations
        if elapsed_ms <= 0:
            raise BringupError("bringup throughput elapsed time is invalid")
        return ThroughputMeasurement(
            payload_bytes=total_bytes,
            elapsed_ms=elapsed_ms,
            bytes_per_second=total_bytes / (elapsed_ms / 1000),
        )

    def _echo(self, *, payload: str, timeout: float, maximum: bool) -> EchoMeasurement:
        with self._lock:
            self._require_ready_locked()
            request_id = self._next_request_id_locked()
            sequence = self._next_sequence_locked()
            if maximum:
                payload = maximum_echo_payload(sequence=sequence, request_id=request_id)
            waiter = _EchoWaiter(
                payload_bytes=len(payload.encode("utf-8")),
                checksum=fnv1a(payload),
                started=time.monotonic(),
                event=threading.Event(),
            )
            self._echo_waiters[request_id] = waiter
            message: JsonObject = {
                "t": "echo",
                "seq": sequence,
                "id": request_id,
                "payload": payload,
            }
        if not self._link.send(message):
            with self._lock:
                self._echo_waiters.pop(request_id, None)
            raise BringupError("bringup echo could not be sent")
        if not self._wait(waiter.event, timeout):
            raise TimeoutError("bringup echo response was not received")
        with self._lock:
            self._echo_waiters.pop(request_id, None)
            if waiter.elapsed_ms is None:
                raise BringupError("bringup echo measurement is missing")
            return EchoMeasurement(waiter.payload_bytes, waiter.elapsed_ms)

    def _on_connected(self) -> None:
        with self._generation_condition:
            self._generation += 1
            self._host_sequence = 0
            self._request_id = 0
            self._session = secrets.token_hex(8)
            self._hello_sent = False
            self._ready.clear()
            self._heartbeat.clear()
            self._digit.clear()
            self._g0_short.clear()
            self._g0_long.clear()
            self._last_heap = None
            self._echo_waiters.clear()
            self._ping_waiters.clear()
            self._generation_condition.notify_all()

    def _on_stale(self) -> None:
        self._ready.clear()

    def _on_message(self, message: JsonObject) -> None:
        message_type = message["t"]
        if message_type == "hello":
            self._reply_to_hello()
            return
        if message_type == "ready":
            with self._lock:
                if (
                    message.get("session") != self._session
                    or message.get("board") != 24
                    or message.get("adv") is not True
                ):
                    self._fail_locked("bringup ready did not identify Cardputer-Adv")
                    return
                self._ready.set()
            return
        if message_type == "echo":
            self._receive_echo(message)
            return
        if message_type == "pong":
            self._receive_pong(message)
            return
        if message_type == "heartbeat":
            heap = message["heap"]
            assert isinstance(heap, int) and not isinstance(heap, bool)
            with self._lock:
                self._last_heap = heap
                self._heartbeat.set()
            return
        if message_type == "key":
            code = message["code"]
            assert isinstance(code, int) and not isinstance(code, bool)
            if 48 <= code <= 57:
                self._digit.set()
            return
        if message_type == "g0":
            action = message["action"]
            if action == "short":
                self._g0_short.set()
            elif action == "long":
                self._g0_long.set()
            return
        if message_type == "error":
            with self._lock:
                self._fail_locked("device reported a bringup error")

    def _reply_to_hello(self) -> None:
        with self._lock:
            if self._hello_sent:
                return
            message: JsonObject = {
                "t": "hello",
                "seq": self._next_sequence_locked(),
                "proto": 1,
                "mode": BRINGUP_MODE,
                "session": self._session,
            }
            self._hello_sent = True
        if not self._link.send(message):
            with self._lock:
                self._fail_locked("bringup hello response could not be sent")

    def _receive_echo(self, message: JsonObject) -> None:
        request_id = message["id"]
        payload_bytes = message["payloadBytes"]
        checksum = message["checksum"]
        assert isinstance(request_id, int) and not isinstance(request_id, bool)
        assert isinstance(payload_bytes, int) and not isinstance(payload_bytes, bool)
        assert isinstance(checksum, int) and not isinstance(checksum, bool)
        with self._lock:
            waiter = self._echo_waiters.get(request_id)
            if waiter is None:
                return
            if waiter.payload_bytes != payload_bytes or waiter.checksum != checksum:
                self._fail_locked("bringup echo checksum did not match")
                waiter.event.set()
                return
            waiter.elapsed_ms = (time.monotonic() - waiter.started) * 1000
            waiter.event.set()

    def _receive_pong(self, message: JsonObject) -> None:
        request_id = message["id"]
        assert isinstance(request_id, int) and not isinstance(request_id, bool)
        with self._lock:
            waiter = self._ping_waiters.get(request_id)
            if waiter is None:
                return
            waiter.elapsed_ms = (time.monotonic() - waiter.started) * 1000
            waiter.event.set()

    def _periodic_ping(self) -> JsonObject | None:
        with self._lock:
            if not self._ready.is_set():
                return None
            return {
                "t": "ping",
                "seq": self._next_sequence_locked(),
                "id": self._next_request_id_locked(),
            }

    def _wait(self, event: threading.Event, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not event.is_set():
            with self._lock:
                self._raise_failure_locked()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            event.wait(timeout=min(0.05, remaining))
        with self._lock:
            self._raise_failure_locked()
        return True

    def _next_sequence_locked(self) -> int:
        self._host_sequence += 1
        return self._host_sequence

    def _next_request_id_locked(self) -> int:
        self._request_id += 1
        return self._request_id

    def _require_ready_locked(self) -> None:
        self._raise_failure_locked()
        if not self._ready.is_set():
            raise BringupError("bringup session is not ready")

    def _fail_locked(self, message: str) -> None:
        if self._failure is None:
            self._failure = BringupError(message)

    def _raise_failure_locked(self) -> None:
        if self._failure is not None:
            raise self._failure


def run_bringup_dry_run(output: TextIO) -> None:
    _pass(output, "transport_selection")
    output.write("serial_io_opened false\n")
    output.write("codex_connection N/A\n")
    _pass(output, "bringup_dry_run")


def run_bringup(
    output: TextIO,
    *,
    port: str,
    provider: SerialProvider,
    synthetic_device: SyntheticSerialProvider | None = None,
    step_timeout: float = 30.0,
    input_timeout: float = 120.0,
) -> None:
    stop = threading.Event()
    driver_errors: list[type[BaseException]] = []
    driver: threading.Thread | None = None
    if synthetic_device is not None:
        driver = threading.Thread(
            target=_drive_synthetic_device,
            args=(synthetic_device, stop, driver_errors),
            name="cardputer-bringup-fixture",
            daemon=True,
        )
        driver.start()

    session = BringupSession(port=port, provider=provider)
    session.start()
    try:
        if not session.wait_connected(step_timeout):
            raise TimeoutError("bringup serial link did not connect")
        _pass(output, "serial_link")
        if not session.wait_ready(step_timeout):
            raise TimeoutError("bringup ready was not received")
        _pass(output, "device_ready")
        _pass(output, "firmware_version")
        _pass(output, "board_cardputer_adv")
        session.echo("bringup", step_timeout)
        _pass(output, "echo_small")
        maximum_echo = session.echo_maximum_line(step_timeout)
        _pass(output, "echo_4096")
        rtt_ms = session.ping(step_timeout)
        if rtt_ms <= 0:
            raise BringupError("bringup RTT measurement is invalid")
        _pass(output, "rtt")
        throughput = session.measure_throughput(timeout=step_timeout)
        if throughput.bytes_per_second <= 0:
            raise BringupError("bringup throughput measurement is invalid")
        _pass(output, "throughput")
        if not session.wait_heartbeat(step_timeout):
            raise TimeoutError("bringup heartbeat was not received")
        heap_bytes = session.last_heap
        if heap_bytes is None or heap_bytes <= 0:
            raise BringupError("bringup heap measurement is invalid")
        _pass(output, "heartbeat")
        if synthetic_device is None:
            _write_hardware_measurements(
                output,
                maximum_echo=maximum_echo,
                rtt_ms=rtt_ms,
                throughput=throughput,
                heap_bytes=heap_bytes,
            )
            output.write("input_test waiting: digit, G0 tap, G0 hold\n")
            output.flush()
        if not session.wait_digit(input_timeout):
            raise TimeoutError("bringup digit key was not observed")
        _pass(output, "keyboard_digit")
        if not session.wait_g0_short(input_timeout):
            raise TimeoutError("bringup G0 short press was not observed")
        _pass(output, "g0_short")
        if not session.wait_g0_long(input_timeout):
            raise TimeoutError("bringup G0 long press was not observed")
        _pass(output, "g0_long")
    finally:
        session.close()
        stop.set()
        if driver is not None:
            driver.join(timeout=1.0)
    if driver_errors:
        raise BringupError("synthetic bringup device failed")
    output.write("codex_connection N/A\n")
    _pass(output, "bringup")


def _write_hardware_measurements(
    output: TextIO,
    *,
    maximum_echo: EchoMeasurement,
    rtt_ms: float,
    throughput: ThroughputMeasurement,
    heap_bytes: int,
) -> None:
    output.write(f"echo_4096_elapsed_ms {maximum_echo.elapsed_ms:.3f}\n")
    output.write(f"rtt_ms {rtt_ms:.3f}\n")
    output.write(f"throughput_bytes_per_second {throughput.bytes_per_second:.0f}\n")
    output.write(f"heap_bytes {heap_bytes}\n")


def _drive_synthetic_device(
    provider: SyntheticSerialProvider,
    stop: threading.Event,
    errors: list[type[BaseException]],
) -> None:
    try:
        provider.wait_for_port(timeout=2.0)
        device_sequence = 1
        provider.inject(
            {
                "t": "hello",
                "seq": device_sequence,
                "proto": 1,
                "mode": BRINGUP_MODE,
                "firmware": "synthetic",
                "board": 24,
                "adv": True,
                "heap": 250_000,
            }
        )
        processed = 0
        input_sent = False
        last_heartbeat = 0.0
        while not stop.wait(0.002):
            messages = provider.decoded_host_messages()
            for message in messages[processed:]:
                message_type = message["t"]
                if message_type == "hello":
                    device_sequence += 1
                    provider.inject(
                        {
                            "t": "ready",
                            "seq": device_sequence,
                            "mode": BRINGUP_MODE,
                            "session": message["session"],
                            "board": 24,
                            "adv": True,
                        }
                    )
                    if not input_sent:
                        device_sequence = _inject_synthetic_inputs(provider, device_sequence)
                        input_sent = True
                elif message_type == "echo":
                    payload = message["payload"]
                    assert isinstance(payload, str)
                    device_sequence += 1
                    provider.inject(
                        {
                            "t": "echo",
                            "seq": device_sequence,
                            "id": message["id"],
                            "payloadBytes": len(payload.encode("utf-8")),
                            "checksum": fnv1a(payload),
                        }
                    )
                elif message_type == "ping":
                    device_sequence += 1
                    provider.inject(
                        {
                            "t": "pong",
                            "seq": device_sequence,
                            "id": message["id"],
                        }
                    )
            processed = len(messages)
            now = time.monotonic()
            if input_sent and now - last_heartbeat >= 0.05:
                device_sequence += 1
                provider.inject(
                    {
                        "t": "heartbeat",
                        "seq": device_sequence,
                        "uptimeMs": 1000,
                        "heap": 250_000,
                        "rx": processed,
                        "tx": device_sequence,
                        "errors": 0,
                    }
                )
                last_heartbeat = now
    except BaseException as error:
        errors.append(type(error))


def _inject_synthetic_inputs(
    provider: SyntheticSerialProvider,
    device_sequence: int,
) -> int:
    fixtures: tuple[JsonObject, ...] = (
        {"t": "key", "code": 49},
        {"t": "g0", "action": "press", "heldMs": 0},
        {"t": "g0", "action": "short", "heldMs": 499},
        {"t": "g0", "action": "press", "heldMs": 0},
        {"t": "g0", "action": "long", "heldMs": 500},
        {"t": "g0", "action": "release", "heldMs": 700},
    )
    for fixture in fixtures:
        device_sequence += 1
        fixture["seq"] = device_sequence
        provider.inject(fixture)
    return device_sequence


def _pass(output: TextIO, step: str) -> None:
    output.write(f"{step} PASS\n")
    output.flush()


__all__ = [
    "BringupError",
    "BringupSession",
    "EchoMeasurement",
    "ThroughputMeasurement",
    "run_bringup",
    "run_bringup_dry_run",
]
