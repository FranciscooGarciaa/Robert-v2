"""WitMotion D6 (Guoke COIN-D6) 360° LiDAR over a USB-serial adapter.

The sensor starts spinning and streaming as soon as it has power; nothing has
to be sent to it. Wire format (230400 8N1, little-endian), verified on the
robot 2026-10-22:

    AA 55 | CT | LSN | FSA(u16) | LSA(u16) | CS(u16) | LSN x 3-byte samples

    CT bit0 = 1   start-of-revolution packet (LSN=1, its sample is not a
                  measurement); CT>>1 = scan frequency in 0.1 Hz
    angle         (raw >> 1) / 64 degrees, clockwise seen from above,
                  interpolated linearly from FSA to LSA
    sample        intensity = b0, distance_mm = (b2 << 6) | (b1 >> 2), 0 = no return
    CS            XOR of the four header words, every intensity byte and
                  every (b1 | b2 << 8) word

A reader thread turns packets into complete revolutions and publishes the
latest one as 360 one-degree bins in the robot frame (0° = front, clockwise),
each holding the nearest return in mm (0 = nothing seen).
"""

import logging
import struct
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

from .state import LidarState

log = logging.getLogger("robert.lidar")

HEADER = b"\xaa\x55"
HEADER_LEN = 10
SAMPLE_LEN = 3
BINS = 360


@dataclass(frozen=True)
class Packet:
    scan_start: bool
    scan_hz: float | None          # only reported in start packets
    points: list[tuple[float, int, int]]  # (angle_deg, distance_mm, intensity)


def checksum(frame: bytes) -> int:
    lsn = frame[3]
    x = 0
    for off in (0, 2, 4, 6):
        x ^= frame[off] | frame[off + 1] << 8
    for k in range(HEADER_LEN, HEADER_LEN + SAMPLE_LEN * lsn, SAMPLE_LEN):
        x ^= frame[k]
        x ^= frame[k + 1] | frame[k + 2] << 8
    return x


def decode(frame: bytes) -> Packet:
    ct, lsn = frame[2], frame[3]
    fsa, lsa = struct.unpack_from("<HH", frame, 4)
    a0 = (fsa >> 1) / 64
    a1 = (lsa >> 1) / 64
    span = (a1 - a0) % 360
    step = span / (lsn - 1) if lsn > 1 else 0.0
    start = bool(ct & 1)
    points = []
    if not start:
        for k in range(lsn):
            b0, b1, b2 = frame[HEADER_LEN + 3 * k: HEADER_LEN + 3 * k + 3]
            points.append(((a0 + step * k) % 360, (b2 << 6) | (b1 >> 2), b0))
    return Packet(start, (ct >> 1) / 10 if start else None, points)


class Parser:
    """Incremental byte-stream parser; resynchronises on bad checksums."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.checksum_errors = 0

    def feed(self, data: bytes) -> Iterator[Packet]:
        buf = self._buf
        buf += data
        while True:
            i = buf.find(HEADER)
            if i < 0:
                del buf[:-1]  # keep a trailing 0xAA that may start a header
                return
            if i:
                del buf[:i]
            if len(buf) < HEADER_LEN:
                return
            n = HEADER_LEN + SAMPLE_LEN * buf[3]
            if len(buf) < n:
                return
            frame = bytes(buf[:n])
            if checksum(frame) != struct.unpack_from("<H", frame, 8)[0]:
                self.checksum_errors += 1
                del buf[:2]  # false header: search again after it
                continue
            del buf[:n]
            yield decode(frame)


class ScanAssembler:
    """Collects packets into revolutions and bins them in the robot frame."""

    def __init__(self, offset_deg: float = 0.0, clockwise: bool = True,
                 min_mm: int = 50, max_mm: int = 12000) -> None:
        self.offset_deg = offset_deg
        self.clockwise = clockwise
        self.min_mm = min_mm
        self.max_mm = max_mm
        self._bins = [0] * BINS
        self._count = 0

    def to_robot_deg(self, sensor_deg: float) -> float:
        a = sensor_deg if self.clockwise else -sensor_deg
        return (a + self.offset_deg) % 360

    def add(self, pkt: Packet) -> list[int] | None:
        """Returns the finished revolution when `pkt` starts a new one."""
        done = None
        if pkt.scan_start:
            if self._count:
                done = self._bins
            self._bins = [0] * BINS
            self._count = 0
        for angle, dist, _ in pkt.points:
            if not self.min_mm <= dist <= self.max_mm:
                continue
            b = int(self.to_robot_deg(angle)) % BINS
            if self._bins[b] == 0 or dist < self._bins[b]:
                self._bins[b] = dist
            self._count += 1
        return done


def nearest(bins: list[int]) -> tuple[int | None, int | None]:
    best = min(((d, a) for a, d in enumerate(bins) if d), default=None)
    return (best[0], best[1]) if best else (None, None)


class LidarReader:
    """Owns the serial port in a background thread; reopens it if unplugged.

    The thread only swaps whole objects into `state` (GIL-atomic), so the
    asyncio side can read them without locks.
    """

    def __init__(self, port: str, baud: int, state: LidarState, assembler: ScanAssembler):
        self._port = port
        self._baud = baud
        self._state = state
        self._asm = assembler
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lidar", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        import serial  # pyserial; imported here so tests don't need a port

        st = self._state
        while not self._stop.is_set():
            try:
                with serial.Serial(self._port, self._baud, timeout=0.2) as ser:
                    log.info("LiDAR port open: %s", self._port)
                    st.connected = True
                    self._read_loop(ser)
            except Exception as exc:  # noqa: BLE001 - unplugged/permission: retry
                if st.connected:
                    log.warning("LiDAR lost: %r", exc)
                else:
                    log.debug("LiDAR open failed: %r", exc)
            st.connected = False
            st.bins = None
            self._stop.wait(2.0)

    def _read_loop(self, ser) -> None:
        parser = Parser()
        st = self._state
        last_scan = None
        last_data = time.monotonic()
        while not self._stop.is_set():
            # Read what has arrived (not a fixed 4 kB, which takes ~0.2 s at
            # 230400 baud) so scan timing and latency stay per revolution.
            data = ser.read(ser.in_waiting or 1)
            now = time.monotonic()
            if not data:
                if now - last_data > 2.0:
                    raise TimeoutError("no data from LiDAR for 2 s")
                continue
            last_data = now
            for pkt in parser.feed(data):
                if pkt.scan_hz:
                    st.reported_hz = pkt.scan_hz
                bins = self._asm.add(pkt)
                if bins is None:
                    continue
                if last_scan is not None and now > last_scan:
                    hz = 1 / (now - last_scan)
                    st.scan_hz = round(hz if st.scan_hz is None else 0.8 * st.scan_hz + 0.2 * hz, 1)
                last_scan = now
                st.points_per_scan = sum(1 for d in bins if d)
                st.nearest_mm, st.nearest_deg = nearest(bins)
                st.bins = bins
                st.last_scan = now
                st.scans += 1
            st.checksum_errors = parser.checksum_errors

