"""下载与本地校验：限大小的流式下载、ffprobe 校验、抽帧、提取音频。依赖 ffmpeg / ffprobe。"""

import json
import os
import shutil
import subprocess
import urllib.request

from .resolve import USER_AGENT, PREVIEW_HOST

DEFAULT_MAX_MB = 200


class SizeLimitExceededError(RuntimeError):
    """文件超过大小上限。"""


class MediaError(RuntimeError):
    """文件不是可读的媒体，或抽帧失败。"""


def require_tools() -> list:
    """返回缺少的外部命令名。"""
    return [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]


def download(url: str, dst_path: str, max_bytes: int = DEFAULT_MAX_MB * 1024 * 1024) -> int:
    """流式下载到 dst_path，超过 max_bytes 立刻中止并删除半成品。返回字节数。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*", "Referer": PREVIEW_HOST + "/"}
    )
    total = 0
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > max_bytes:
                raise SizeLimitExceededError(f"文件 {int(cl) / 1048576:.1f} MiB，超过上限 {max_bytes / 1048576:.0f} MiB")
            with open(dst_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise SizeLimitExceededError(f"下载超过上限 {max_bytes / 1048576:.0f} MiB，已中止")
                    f.write(chunk)
    except BaseException:
        try:
            os.unlink(dst_path)
        except OSError:
            pass
        raise
    return total


def probe(path: str) -> dict:
    """ffprobe 读取流信息；读不了就抛 MediaError。"""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe 读不了这个文件: {proc.stderr.strip()[:200]}")
    info = json.loads(proc.stdout)
    if not info.get("streams"):
        raise MediaError("ffprobe 没有找到任何媒体流")
    return info


def summarize_video(info: dict) -> dict:
    """从 ffprobe 结果里取时长、宽高、编码。"""
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = fmt.get("duration") or v.get("duration")
    return {
        "duration": round(float(dur), 3) if dur is not None else None,
        "width": v.get("width"),
        "height": v.get("height"),
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name") if a else None,
    }


def extract_frames(video_path: str, duration: float, out_dir: str) -> list:
    """在 10% / 50% / 85% 处各抽一帧存成 jpg，返回文件路径列表。抽不出来就抛 MediaError。"""
    os.makedirs(out_dir, exist_ok=True)
    ratios = [0.1, 0.5, 0.85] if duration and duration >= 1.5 else [0.0]
    paths = []
    for idx, r in enumerate(ratios, 1):
        ts = max(0.0, min(duration - 0.05, duration * r)) if duration and duration > 0.1 else 0.0
        out_jpg = os.path.join(out_dir, f"frame_{idx:02d}.jpg")
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{ts:.3f}", "-i", video_path, "-frames:v", "1", "-q:v", "3", out_jpg],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not os.path.isfile(out_jpg) or os.path.getsize(out_jpg) < 256:
            raise MediaError(f"第 {idx} 帧抽取失败，视频可能损坏")
        paths.append(out_jpg)
    return paths


def extract_speech_audio(video_path: str, out_flac: str) -> int:
    """提取 16kHz 单声道 16 位 FLAC（约 1.45 MB/分钟），给语音转写用。返回字节数。"""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", video_path,
         "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-c:a", "flac", out_flac],
        check=True,
    )
    return os.path.getsize(out_flac)
