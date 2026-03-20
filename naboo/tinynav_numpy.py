"""
TinyNav NumPy inference engine — runs on Pi Zero 2 W without PyTorch.

Loads weights from .npz exported by export_tinynav.py.
Uses im2col vectorised convolutions for speed (~40ms per inference).
"""
import numpy as np


def _im2col(x, kh, kw, stride=1, padding=0):
    """Extract sliding windows as columns for vectorised conv."""
    if padding > 0:
        x = np.pad(x, ((0, 0), (padding, padding), (padding, padding)), mode="constant")
    c, hp, wp = x.shape
    h_out = (hp - kh) // stride + 1
    w_out = (wp - kw) // stride + 1
    cols = np.zeros((c * kh * kw, h_out * w_out), dtype=x.dtype)
    idx = 0
    for ci in range(c):
        for ki in range(kh):
            for kj in range(kw):
                cols[idx] = x[ci, ki: ki + h_out * stride: stride,
                               kj: kj + w_out * stride: stride].flatten()
                idx += 1
    return cols, h_out, w_out


def _conv2d(x, w, b, stride=1, padding=0):
    """Vectorised 2D convolution."""
    co = w.shape[0]
    cols, h_out, w_out = _im2col(x, w.shape[2], w.shape[3], stride, padding)
    out = w.reshape(co, -1) @ cols + b.reshape(-1, 1)
    return out.reshape(co, h_out, w_out)


def _dw_conv2d(x, w, b, stride=1, padding=0):
    """Depthwise convolution — one filter per input channel."""
    c, kh, kw = x.shape[0], w.shape[2], w.shape[3]
    if padding > 0:
        x = np.pad(x, ((0, 0), (padding, padding), (padding, padding)), mode="constant")
    h_out = (x.shape[1] - kh) // stride + 1
    w_out = (x.shape[2] - kw) // stride + 1
    out = np.zeros((c, h_out, w_out), dtype=x.dtype)
    for ci in range(c):
        cols_1 = np.zeros((kh * kw, h_out * w_out), dtype=x.dtype)
        idx = 0
        for ki in range(kh):
            for kj in range(kw):
                cols_1[idx] = x[ci, ki: ki + h_out * stride: stride,
                                 kj: kj + w_out * stride: stride].flatten()
                idx += 1
        out[ci] = (w[ci, 0].flatten() @ cols_1).reshape(h_out, w_out) + b[ci]
    return out


def _relu(x):
    return np.maximum(x, 0)


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -10, 10)))


class TinyNavEngine:
    """Pure NumPy TinyNav CNN inference engine.

    Architecture (matching PyTorch TinyNavCNN):
        Conv2d(10->16, 3x3, stride=2, pad=1)   -> 12x12
        DW(16, 3x3, s=2, p=1) + PW(16->32)     -> 6x6
        DW(32, 3x3, p=1) + PW(32->32)           -> 6x6
        DW(32, 3x3, p=1) + PW(32->48)           -> 6x6
        Attention(48->1, 1x1)
        GAP -> Dense(48->64) -> steer + throttle
    """

    def __init__(self, npz_path):
        w = np.load(npz_path)

        # Load all weights
        self.conv1_w = w["conv1.weight"]
        self.conv1_b = w["conv1.bias"]

        self.dw2_w = w["dw2.weight"]
        self.dw2_b = w["dw2.bias"]
        self.pw2_w = w["pw2.weight"]
        self.pw2_b = w["pw2.bias"]

        self.dw3_w = w["dw3.weight"]
        self.dw3_b = w["dw3.bias"]
        self.pw3_w = w["pw3.weight"]
        self.pw3_b = w["pw3.bias"]

        self.dw4_w = w["dw4.weight"]
        self.dw4_b = w["dw4.bias"]
        self.pw4_w = w["pw4.weight"]
        self.pw4_b = w["pw4.bias"]

        self.att_w = w["attention.weight"]
        self.att_b = w["attention.bias"]

        self.fc1_w = w["fc1.weight"]
        self.fc1_b = w["fc1.bias"]
        self.fc_s_w = w["fc_steer.weight"]
        self.fc_s_b = w["fc_steer.bias"]
        self.fc_t_w = w["fc_throttle.weight"]
        self.fc_t_b = w["fc_throttle.bias"]

    def predict(self, x):
        """Run inference.

        Args:
            x: (10, 24, 24) float32, normalised 0-1

        Returns:
            (steering, throttle) — steering in [-1,1], throttle in [0,1]
        """
        # Conv1: (10,24,24) -> (16,12,12)
        h = _relu(_conv2d(x, self.conv1_w, self.conv1_b, stride=2, padding=1))

        # SepConv2: (16,12,12) -> (32,6,6)
        h = _relu(_conv2d(
            _dw_conv2d(h, self.dw2_w, self.dw2_b, stride=2, padding=1),
            self.pw2_w, self.pw2_b))

        # SepConv3: (32,6,6) -> (32,6,6)
        h = _relu(_conv2d(
            _dw_conv2d(h, self.dw3_w, self.dw3_b, padding=1),
            self.pw3_w, self.pw3_b))

        # SepConv4: (32,6,6) -> (48,6,6)
        h = _relu(_conv2d(
            _dw_conv2d(h, self.dw4_w, self.dw4_b, padding=1),
            self.pw4_w, self.pw4_b))

        # Spatial attention
        att = _sigmoid(_conv2d(h, self.att_w, self.att_b))
        h = h * att

        # Global average pooling
        features = h.mean(axis=(1, 2))  # (48,)

        # Dense head
        h = _relu(self.fc1_w @ features + self.fc1_b)  # (64,)

        steer = float(np.tanh(self.fc_s_w @ h + self.fc_s_b))
        throttle = float(_sigmoid(self.fc_t_w @ h + self.fc_t_b))

        return steer, throttle
