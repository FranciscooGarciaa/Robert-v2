"""Person detection with YOLO11n (ONNX, 320x320) on the CPU, ~170 ms on the Pi 4."""

import numpy as np

SIZE = 320
PERSON = 0
SCORE_MIN = 0.45
IOU_MAX = 0.5


def letterbox(rgb: np.ndarray) -> tuple[np.ndarray, float, int, int]:
    """Fits the image into SIZE x SIZE (no resampling library: nearest by striding)."""
    h, w, _ = rgb.shape
    scale = SIZE / max(h, w)
    ys = (np.arange(int(h * scale)) / scale).astype(int)
    xs = (np.arange(int(w * scale)) / scale).astype(int)
    small = rgb[ys][:, xs]
    out = np.full((SIZE, SIZE, 3), 114, dtype=np.uint8)
    top, left = (SIZE - small.shape[0]) // 2, (SIZE - small.shape[1]) // 2
    out[top:top + small.shape[0], left:left + small.shape[1]] = small
    return out, scale, top, left


def nms(boxes: np.ndarray, scores: np.ndarray) -> list[int]:
    """Greedy non-maximum suppression; boxes as x1, y1, x2, y2."""
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        iou = inter / (area(boxes[i]) + area(boxes[order[1:]]) - inter + 1e-9)
        order = order[1:][iou <= IOU_MAX]
    return keep


def decode(output: np.ndarray, scale: float, top: int, left: int) -> list[tuple[float, float, float, float, float]]:
    """YOLO (1, 84, N) -> person boxes (x1, y1, x2, y2, score) in original pixels."""
    pred = output[0]
    scores = pred[4 + PERSON]
    keep = scores >= SCORE_MIN
    if not keep.any():
        return []
    cx, cy, bw, bh = pred[:4, keep]
    boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
    s = scores[keep]
    out = []
    for i in nms(boxes, s):
        x1, y1, x2, y2 = boxes[i]
        out.append(((x1 - left) / scale, (y1 - top) / scale, (x2 - left) / scale,
                    (y2 - top) / scale, float(s[i])))
    return out


class Detector:
    def __init__(self, model_path: str, threads: int = 3):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        self._session = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def people(self, rgb: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        img, scale, top, left = letterbox(rgb)
        x = img.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        return decode(self._session.run(None, {self._input: x})[0], scale, top, left)
