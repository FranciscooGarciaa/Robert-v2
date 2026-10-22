import struct

from robert.robot.lidar import Parser, ScanAssembler, checksum, nearest


def frame(ct: int, angles: tuple[float, float], samples: list[tuple[int, int]]) -> bytes:
    """Build a D6 packet; samples are (distance_mm, intensity)."""
    fsa, lsa = (int(a * 64) << 1 | 1 for a in angles)
    body = b"".join(
        bytes([i & 0xFF, (d & 0x3F) << 2, d >> 6]) for d, i in samples
    )
    head = b"\xaa\x55" + bytes([ct, len(samples)]) + struct.pack("<HH", fsa, lsa)
    f = head + b"\0\0" + body
    return head + struct.pack("<H", checksum(f)) + body


def start() -> bytes:
    return frame(201, (0, 0), [(0, 0)])


def test_parser_decodes_and_interpolates():
    pkt = next(Parser().feed(frame(0, (10, 20), [(500, 9), (1000, 9), (1500, 9)])))
    assert not pkt.scan_start
    assert [(round(a, 2), d) for a, d, _ in pkt.points] == [(10, 500), (15, 1000), (20, 1500)]


def test_parser_wraps_angles_and_reports_start_hz():
    pkts = list(Parser().feed(start() + frame(0, (350, 10), [(700, 1)] * 5)))
    assert pkts[0].scan_start and pkts[0].scan_hz == 10.0 and pkts[0].points == []
    assert [round(a) for a, _, _ in pkts[1].points] == [350, 355, 0, 5, 10]


def test_parser_resyncs_after_garbage_and_bad_checksum():
    good = frame(0, (0, 2), [(800, 1)] * 3)
    bad = bytearray(good)
    bad[12] ^= 0xFF
    p = Parser()
    data = b"\x01\xaa\x02" + bytes(bad) + good
    # Feed byte by byte to exercise partial buffers too.
    pkts = [pkt for i in range(len(data)) for pkt in p.feed(data[i:i + 1])]
    assert len(pkts) == 1 and pkts[0].points[0][1] == 800
    assert p.checksum_errors == 1


def test_assembler_bins_revolutions_in_robot_frame():
    asm = ScanAssembler(offset_deg=90, clockwise=True)
    p = Parser()
    out = []
    for f in (start(), frame(0, (0, 0), [(1200, 1)]), frame(0, (0, 0), [(900, 1)]),
              frame(0, (300, 300), [(20, 1)]), start()):
        for pkt in p.feed(f):
            out.append(asm.add(pkt))
    assert out[0] is None  # first start: nothing collected yet
    bins = out[-1]
    assert bins[90] == 900          # nearest return kept, rotated by the offset
    assert bins[(300 + 90) % 360] == 0  # 20 mm is below the minimum range
    assert nearest(bins) == (900, 90)


def test_counter_clockwise_mounting():
    asm = ScanAssembler(clockwise=False)
    assert asm.to_robot_deg(90) == 270
