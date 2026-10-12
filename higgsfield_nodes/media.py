"""Convert between ComfyUI media types and files Higgsfield can accept or returns."""

from __future__ import annotations

import io as _io
import wave
from pathlib import Path
from typing import Any

import numpy as np

PNG_LIMIT = 15 * 1024 * 1024  # fall back to JPEG above this size


def _to_numpy(value: Any) -> np.ndarray:
    """Tensor (any dtype/device, incl. float16/bfloat16 which numpy can't take directly) -> float32 array."""
    if hasattr(value, "detach"):
        return value.detach().float().cpu().numpy()
    return np.asarray(value, dtype=np.float32)


def image_batch_to_list(value: Any) -> list[Any]:
    """A ComfyUI IMAGE is a [B,H,W,C] tensor; split it into single images."""
    if value is None:
        return []
    if hasattr(value, "shape") and len(value.shape) == 4:
        return [value[i] for i in range(value.shape[0])]
    return [value]


def image_to_upload(image: Any) -> tuple[bytes, str]:
    from PIL import Image

    array = _to_numpy(image)
    if array.ndim == 4:
        array = array[0]
    array = (np.clip(array, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    if array.shape[-1] == 1:
        array = array[..., 0]
    picture = Image.fromarray(array)
    if picture.mode == "RGBA":
        picture = picture.convert("RGB") if not np.any(array[..., 3] < 255) else picture
    buffer = _io.BytesIO()
    picture.save(buffer, format="PNG", compress_level=4)
    if buffer.tell() <= PNG_LIMIT:
        return buffer.getvalue(), "image/png"
    buffer = _io.BytesIO()
    picture.convert("RGB").save(buffer, format="JPEG", quality=95)
    return buffer.getvalue(), "image/jpeg"


def video_to_upload(video: Any) -> tuple[bytes, str]:
    from comfy_api.latest import Types

    buffer = _io.BytesIO()
    video.save_to(buffer, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
    return buffer.getvalue(), "video/mp4"


def audio_to_upload(audio: Any) -> tuple[bytes, str]:
    """ComfyUI AUDIO = {"waveform": [B, C, T] float tensor, "sample_rate": int} -> 16-bit WAV."""
    if not isinstance(audio, dict) or "waveform" not in audio:
        raise ValueError("Expected a ComfyUI AUDIO input.")
    samples = _to_numpy(audio["waveform"])
    if samples.ndim == 3:
        samples = samples[0]
    if samples.ndim == 1:
        samples = samples[None, :]
    pcm = (np.clip(samples, -1.0, 1.0).T * 32767.0).astype("<i2")
    buffer = _io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(samples.shape[0])
        wav.setsampwidth(2)
        wav.setframerate(int(audio.get("sample_rate", 44100)))
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue(), "audio/wav"


def to_upload(kind: str, value: Any) -> tuple[bytes, str]:
    if kind == "image":
        return image_to_upload(value)
    if kind == "video":
        return video_to_upload(value)
    if kind == "audio":
        return audio_to_upload(value)
    raise ValueError(f"Unsupported media kind {kind}")


def load_images(paths: list[Path]):
    """Load downloaded images as one ComfyUI IMAGE batch (padded to the largest size if they differ)."""
    import torch
    from PIL import Image, ImageOps

    frames = []
    for path in paths:
        with Image.open(path) as picture:
            picture = ImageOps.exif_transpose(picture).convert("RGB")
            frames.append(np.asarray(picture, dtype=np.float32) / 255.0)
    height = max(f.shape[0] for f in frames)
    width = max(f.shape[1] for f in frames)
    padded = []
    for frame in frames:
        if frame.shape[:2] != (height, width):
            canvas = np.zeros((height, width, 3), dtype=np.float32)
            canvas[: frame.shape[0], : frame.shape[1]] = frame
            frame = canvas
        padded.append(frame)
    return torch.from_numpy(np.stack(padded))


def load_video(path: Path):
    from comfy_api.latest import InputImpl

    return InputImpl.VideoFromFile(str(path))


def extension_for(url: str, fallback: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".wav", ".mp3", ".zip"}
    return suffix if suffix in allowed else fallback
