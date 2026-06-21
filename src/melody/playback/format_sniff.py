"""Detect encoded audio format from magic bytes or HTTP content type."""


def detect_encoded_format(data: bytes, content_type: str = "") -> str | None:
    """Return an ffmpeg -f format name, or None to let ffmpeg probe."""
    if len(data) >= 3 and data[:3] == b"ID3":
        return "mp3"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"
    if len(data) >= 4 and data[:4] == b"fLaC":
        return "flac"
    if len(data) >= 4 and data[:4] == b"OggS":
        return "ogg"
    if len(data) >= 4 and data[:4] == b"RIFF":
        return "wav"

    ct = content_type.lower()
    if "mpeg" in ct or "mp3" in ct:
        return "mp3"
    if "flac" in ct:
        return "flac"
    if "ogg" in ct:
        return "ogg"
    if "wav" in ct:
        return "wav"
    return None
