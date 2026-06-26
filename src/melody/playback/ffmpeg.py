"""FFmpeg transcoding subprocess wrapper."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator

from melody.logging import get_logger

logger = get_logger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2
# 40 ms frames align with Mumble's typical audio packet size and reduce overhead.
FRAME_DURATION_SEC = 0.04
PCM_FRAME_BYTES = int(SAMPLE_RATE * FRAME_DURATION_SEC * CHANNELS * BYTES_PER_SAMPLE)
_MAX_STDERR_LINES = 50


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError("ffmpeg not found in PATH")
    return path


class FFmpegTranscoder:
    """Pipe encoded audio through FFmpeg to Mumble-compatible PCM."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_lines: list[str] = []
        self._terminated = False
        self._released_to_drain = False

    @property
    def pcm_frame_bytes(self) -> int:
        return PCM_FRAME_BYTES

    async def start(self, *, input_format: str | None = None) -> None:
        """Decode audio from stdin pipe (legacy)."""
        if self._process is not None:
            await self.stop()
        ffmpeg = find_ffmpeg()
        args = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-probesize",
            "1M",
            "-analyzeduration",
            "5M",
        ]
        if input_format:
            args.extend(["-f", input_format])
        args.extend(
            [
                "-i",
                "pipe:0",
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ar",
                str(SAMPLE_RATE),
                "-ac",
                str(CHANNELS),
                "pipe:1",
            ]
        )
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._collect_stderr())

    async def start_from_url(
        self,
        url: str,
        *,
        probesize: str = "32k",
        analyzeduration: str = "500k",
    ) -> None:
        """Decode audio directly from an HTTP(S) stream URL."""
        if self._process is not None:
            await self.stop()
        ffmpeg = find_ffmpeg()
        args = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-fflags",
            "nobuffer+discardcorrupt",
            "-flags",
            "low_delay",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-probesize",
            probesize,
            "-analyzeduration",
            analyzeduration,
            "-i",
            url,
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            str(CHANNELS),
            "pipe:1",
        ]
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._collect_stderr())

    async def _collect_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if text:
                self._stderr_lines.append(text)
                if len(self._stderr_lines) > _MAX_STDERR_LINES:
                    del self._stderr_lines[:-_MAX_STDERR_LINES]

    async def write(self, data: bytes) -> None:
        if self._process is None or self._process.stdin is None:
            return
        if not data:
            return
        try:
            self._process.stdin.write(data)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return

    async def close_input(self) -> None:
        if self._process and self._process.stdin:
            try:
                self._process.stdin.close()
                await self._process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    async def read_pcm_frames(self) -> AsyncIterator[bytes]:
        if self._process is None or self._process.stdout is None:
            return
        stdout = self._process.stdout
        while True:
            frame = await stdout.read(PCM_FRAME_BYTES)
            if not frame:
                break
            if len(frame) < PCM_FRAME_BYTES:
                frame = frame + b"\x00" * (PCM_FRAME_BYTES - len(frame))
            yield frame

    async def wait(self) -> int:
        if self._process is None:
            return -1
        if self._stderr_task:
            await self._stderr_task
        return await self._process.wait()

    def stderr_summary(self) -> str:
        return "; ".join(self._stderr_lines[-5:])

    def terminate_sync(self) -> None:
        """Kill FFmpeg immediately without waiting (used during engine.stop())."""
        self.kill_sync()

    def kill_sync(self) -> None:
        """Force-kill FFmpeg and close pipes so readers unblock immediately."""
        proc = self._process
        if proc is None:
            return
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            if stream is not None:
                try:
                    stream.close()
                except (AttributeError, OSError):
                    pass
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._terminated = True
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()

    async def _release_stderr_task(self) -> None:
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
        self._stderr_task = None

    async def stop(self) -> int:
        """Terminate FFmpeg if needed and release subprocess resources."""
        if self._process is None:
            return -1
        proc = self._process
        if proc.returncode is not None:
            code = proc.returncode
            await self._release_stderr_task()
            self._process = None
            return code
        if proc.stdout is not None:
            try:
                proc.stdout.feed_eof()
            except (AttributeError, OSError):
                pass
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        await self._release_stderr_task()
        code = proc.returncode if proc.returncode is not None else -1
        self._process = None
        return code
