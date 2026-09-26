"""RTSP capture into a time-bounded ring buffer.

One ffmpeg subprocess per stream decodes and re-encodes frames as a JPEG
sequence on stdout; a reader thread splits the stream and keeps the newest
frames within the configured window. Capture keeps running regardless of
what detection does with the frames, which is the concurrency contract the
session design needs.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import subprocess
import threading
import time

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


@dataclass(frozen=True)
class Frame:
    timestamp: float
    data: bytes  # complete JPEG image


class RingBuffer:
    """Thread-safe newest-first window of frames."""

    def __init__(self, window_s: float) -> None:
        self._frames: deque[Frame] = deque()
        self._window_s = window_s
        self._lock = threading.Lock()

    def add(self, frame: Frame) -> None:
        with self._lock:
            self._frames.append(frame)
            cutoff = frame.timestamp - self._window_s
            while self._frames and self._frames[0].timestamp < cutoff:
                self._frames.popleft()

    def snapshot(self) -> list[Frame]:
        with self._lock:
            return list(self._frames)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._frames[-1] if self._frames else None

    def window(self, start: float, end: float) -> list[Frame]:
        with self._lock:
            return [f for f in self._frames if start <= f.timestamp <= end]

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)


def split_jpeg_stream(chunk: bytes, partial: bytes = b"") -> tuple[list[bytes], bytes]:
    """Split an ffmpeg mjpeg pipe chunk into complete JPEG images.

    Returns (images, remainder). A frame runs from SOI to the first EOI:
    entropy-coded data can never contain FFD9 (spec requires FF stuffing),
    so the EOI boundary is unambiguous. Bytes after the last complete frame
    (including a SOI without its EOI yet) are carried over.
    """
    data = partial + chunk
    images: list[bytes] = []
    pos = data.find(JPEG_SOI)
    while pos != -1:
        eoi = data.find(JPEG_EOI, pos + 2)
        if eoi == -1:
            break
        images.append(data[pos:eoi + 2])
        pos = data.find(JPEG_SOI, eoi + 2)
    if pos != -1:
        remainder = data[pos:]
    elif images:
        last_end = data.rfind(JPEG_EOI) + 2
        remainder = data[last_end:]
    else:
        remainder = data
    return images, remainder


@dataclass(frozen=True)
class CaptureConfig:
    # Input URL for the ffmpeg pipe: an rtsp:// stream, or a local video
    # file path when replaying recordings offline.
    rtsp_url: str
    fps: float = 2.0
    buffer_window_s: float = 300.0
    # tcp transport tolerates wireless loss far better than udp for a
    # monitoring stream that must not silently stall.
    extra_ffmpeg_args: tuple[str, ...] = ("-rtsp_transport", "tcp")


class RtspCapture:
    """Own the ffmpeg process; readers use the ring buffer."""

    def __init__(self, config: CaptureConfig) -> None:
        self.config = config
        self.buffer = RingBuffer(config.buffer_window_s)
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._partial = b""
        # Wall time when ffmpeg exited (replay end, live stream loss).
        # The session watches this to stop instead of polling forever.
        self.stopped_at: float | None = None

    def _build_command(self) -> list[str]:
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-nostdin",
            *self.config.extra_ffmpeg_args,
            "-i", self.config.rtsp_url,
            "-vf", f"fps={self.config.fps}",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4",
            "pipe:1",
        ]

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("capture already started")
        self._process = subprocess.Popen(
            self._build_command(), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while not self._stop.is_set():
            # read1 returns whatever one underlying read yields; a plain
            # read(65536) would block until the full count is buffered,
            # delaying frames by unbounded seconds on low-bitrate pipes.
            chunk = process.stdout.read1(65536)
            if not chunk:
                break
            images, self._partial = split_jpeg_stream(chunk, self._partial)
            now = time.time()
            for image in images:
                self.buffer.add(Frame(timestamp=now, data=image))
        self.stopped_at = time.time()


class FileReplayCapture(RtspCapture):
    """Replay a recorded video file through the same capture pipe.

    ffmpeg's -re flag reads the file at its native frame rate, so
    wall-clock time tracks video time and the session's second-based
    thresholds (warmup, trigger sustain, clear, archive windows) keep
    their real-time meaning during offline validation.
    """

    def __init__(self, video_path: str | Path, fps: float = 2.0,
                 buffer_window_s: float = 300.0) -> None:
        super().__init__(CaptureConfig(
            rtsp_url=str(video_path),
            fps=fps,
            buffer_window_s=buffer_window_s,
            extra_ffmpeg_args=("-re",),
        ))
