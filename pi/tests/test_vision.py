import numpy as np

from robert.vision.camera import yuv420_to_rgb
from robert.vision.detector import SIZE, decode, letterbox, nms


def test_yuv_grey_frame_converts_to_grey_rgb():
    w, h = 64, 48
    frame = bytes([120] * (w * h)) + bytes([128] * (w * h // 2))
    rgb = yuv420_to_rgb(frame, w, h)
    assert rgb.shape == (48, 64, 3) and (abs(rgb.astype(int) - 120) <= 1).all()
    assert yuv420_to_rgb(frame, w, h, step=2).shape == (24, 32, 3)


def test_letterbox_pads_a_wide_image():
    img, scale, top, left = letterbox(np.zeros((240, 320, 3), np.uint8))
    assert img.shape == (SIZE, SIZE, 3) and scale == 1.0 and top == 40 and left == 0


def test_decode_keeps_confident_people_and_merges_duplicates():
    out = np.zeros((1, 84, 4), np.float32)
    # Two overlapping boxes on the same person, one weak box, one other person.
    out[0, :4, 0] = [100, 160, 40, 120]
    out[0, :4, 1] = [102, 160, 40, 120]
    out[0, :4, 2] = [250, 160, 40, 120]
    out[0, :4, 3] = [200, 160, 40, 120]
    out[0, 4, :] = [0.9, 0.8, 0.3, 0.7]
    people = decode(out, scale=1.0, top=40, left=0)
    assert len(people) == 2
    assert people[0][4] == np.float32(0.9) and abs(people[0][1] - 60) < 1e-3   # y shifted by the pad


def test_nms_separates_far_boxes():
    boxes = np.array([[0, 0, 10, 10], [50, 50, 60, 60]], np.float32)
    assert sorted(nms(boxes, np.array([0.9, 0.8]))) == [0, 1]


def test_service_people_are_json_ready(monkeypatch):
    import json

    from robert.vision import service

    class FakeDetector:
        def people(self, rgb):
            return [(np.float32(100), np.float32(10), np.float32(160), np.float32(200), np.float32(0.9))]

    v = service.Vision.__new__(service.Vision)
    v.detector, v.hfov = FakeDetector(), 54.0
    w, h = 640, 480
    frame = bytes([120] * (w * h)) + bytes([128] * (w * h // 2))
    people = v.people(frame)
    json.dumps(people)
    assert people[0]["bearing_deg"] > 0   # left half of the image = left of the robot
