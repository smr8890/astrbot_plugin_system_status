from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path

import aiohttp
from PIL import Image

MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024


def mime_from_path(path: str | Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def to_data_uri(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def path_to_data_uri(path: str | Path) -> str:
    file_path = Path(path)
    return to_data_uri(file_path.read_bytes(), mime_from_path(file_path))


async def download_bytes(
    url: str,
    *,
    timeout: float = 8.0,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> bytes:
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(
        timeout=timeout_cfg,
        trust_env=True,
        headers={"User-Agent": "AstrBot-SystemStatus/1.0"},
    ) as session:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("image is too large")
                chunks.append(chunk)
            return b"".join(chunks)


def image_bytes_to_data_uri(data: bytes, mime: str = "image/png") -> str:
    return to_data_uri(data, mime)


def resize_image_bytes(
    data: bytes,
    *,
    max_width: int = 1400,
    max_height: int = 2400,
    quality: int = 82,
    fmt: str = "JPEG",
) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.mode not in ("RGB", "L"):
            background = Image.new("RGB", image.size, (233, 233, 233))
            if image.mode == "RGBA":
                background.paste(image, mask=image.getchannel("A"))
            else:
                background.paste(image.convert("RGB"))
            image = background
        else:
            image = image.convert("RGB")

        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format=fmt, quality=quality, optimize=True)
        return output.getvalue()


async def download_image_as_data_uri(
    url: str,
    *,
    timeout: float = 8.0,
    max_width: int = 1400,
    quality: int = 82,
) -> str:
    raw = await download_bytes(url, timeout=timeout)
    try:
        processed = resize_image_bytes(raw, max_width=max_width, quality=quality)
        return to_data_uri(processed, "image/jpeg")
    except Exception:
        return to_data_uri(raw)
