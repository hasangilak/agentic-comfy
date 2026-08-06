"""Local image and video work. No network, no GPU, no cost -- iterate freely."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import config


def ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-2000:]}")


def cutout(path: Path):
    """Key a subject off its flat paper backdrop, returning a tight RGBA crop.

    Keys on chromaticity rather than RGB distance. Source art carries a soft drop
    shadow measuring as backdrop x 0.94 -- same hue, lower luminance -- so a plain
    distance threshold either keeps the shadow as a halo or eats the pale paper.
    Normalising out brightness collapses shadow onto backdrop and separates cleanly.
    """
    import numpy as np
    from PIL import Image, ImageFilter
    from scipy import ndimage

    image = Image.open(path).convert("RGBA")
    pixels = np.asarray(image).astype(np.float32)[:, :, :3]

    patch = 24
    corners = np.concatenate([
        pixels[:patch, :patch].reshape(-1, 3), pixels[:patch, -patch:].reshape(-1, 3),
        pixels[-patch:, :patch].reshape(-1, 3), pixels[-patch:, -patch:].reshape(-1, 3),
    ])
    backdrop = np.median(corners, axis=0)

    chroma = pixels / np.clip(pixels.sum(axis=2, keepdims=True), 1e-6, None)
    reference = backdrop / max(backdrop.sum(), 1e-6)
    mask = ndimage.binary_fill_holes(np.linalg.norm(chroma - reference, axis=2) > 0.033)

    labels, count = ndimage.label(mask)
    if count > 1:
        sizes = ndimage.sum(mask, labels, range(1, count + 1))
        mask = labels == (int(np.argmax(sizes)) + 1)
    mask = ndimage.binary_erosion(mask, iterations=2, border_value=0)

    alpha = Image.fromarray((mask * 255).astype(np.uint8), "L").filter(
        ImageFilter.GaussianBlur(0.7)
    )
    image.putalpha(alpha)
    bbox = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    if bbox is None:
        raise ValueError(f"{path.name}: could not separate a subject from the backdrop")
    return image.crop(bbox)


def compose(background: Path, character: Path, out_path: Path,
            *, width_fraction: float = 0.62, baseline: float = 0.88) -> Path:
    """Build a vertical frame from a separate background and character."""
    from PIL import Image, ImageFilter

    source = Image.open(background).convert("RGB")
    ratio = config.GEN_WIDTH / config.GEN_HEIGHT
    crop_w = min(source.width, round(source.height * ratio))
    crop_h = round(crop_w / ratio)
    left, top = (source.width - crop_w) // 2, (source.height - crop_h) // 2
    frame = (
        source.crop((left, top, left + crop_w, top + crop_h))
        .resize((config.GEN_WIDTH, config.GEN_HEIGHT), Image.LANCZOS)
        .convert("RGBA")
    )

    subject = cutout(character)
    width = round(config.GEN_WIDTH * width_fraction)
    height = round(subject.height * width / subject.width)
    subject = subject.resize((width, height), Image.LANCZOS)
    x = (config.GEN_WIDTH - width) // 2
    y = round(config.GEN_HEIGHT * baseline) - height

    shadow = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 90), (x + 7, y + 12), subject.split()[3])
    frame = Image.alpha_composite(frame, shadow.filter(ImageFilter.GaussianBlur(9)))
    frame.alpha_composite(subject, (x, y))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.convert("RGB").save(out_path)
    return out_path


def fit_frame(source: Path, out_path: Path) -> Path:
    """Cover-crop any image onto the generation grid, losing nothing vertically."""
    from PIL import Image

    with Image.open(source) as image:
        image = image.convert("RGB")
        ratio = config.GEN_WIDTH / config.GEN_HEIGHT
        if image.width / image.height > ratio:
            width = round(image.height * ratio)
            box = ((image.width - width) // 2, 0, (image.width + width) // 2, image.height)
        else:
            height = round(image.width / ratio)
            box = (0, (image.height - height) // 2, image.width, (image.height + height) // 2)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.crop(box).resize(
            (config.GEN_WIDTH, config.GEN_HEIGHT), Image.LANCZOS
        ).save(out_path)
    return out_path


def last_frame(video: Path, out_path: Path) -> Path:
    """Grab a clip's TRUE final frame so the next beat can continue from it.

    Seeking to a fixed offset before the end and taking the first frame after it lands
    early -- measured on a 124-frame beat, `-sseof -0.2` returns frame 121 of 124. The
    beat that continues from it therefore restarts an eighth of a second in the past, so
    the stitched reel plays three frames forward and then jumps back to them: the hitch
    visible at every continuation seam.

    Decoding the tail and letting each frame overwrite the same file leaves the real last
    frame behind, verified bit-identical to a full frame dump. One second of tail is ~24
    PNG encodes, which is cheap next to the render that produced the clip.
    """
    run_ffmpeg(["-sseof", "-1.0", "-i", str(video), "-vsync", "0", "-update", "1",
                str(out_path)])
    return out_path


def tail_clip(video: Path, out_path: Path, seconds: float, *, mute: bool = False) -> Path:
    """Cut the last `seconds` off a clip, for use as a reference video.

    Re-encoded rather than stream-copied: a copy starts at the nearest keyframe before the cut
    and H3's output has few of them, so the "3 second" tail would arrive as anything from 3 to
    10 seconds -- and reference frames are paid for through every sampling step. Encoding a
    few seconds is fast next to the render it feeds.

    Frame rate is pinned because the model reads reference video as 24 fps; handing it 30 fps
    frames would have it read the motion as slower than it was.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-sseof", f"-{seconds:.2f}", "-i", str(video),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        *(["-an"] if mute else ["-c:a", "aac", "-b:a", "128k"]),
        str(out_path),
    ])
    return out_path


def _delivery_filter() -> str:
    return (
        f"scale=-2:{config.REEL_HEIGHT}:flags=lanczos,"
        f"crop={config.REEL_WIDTH}:{config.REEL_HEIGHT},format=yuv420p"
    )


def _encode_args(mute: bool) -> list[str]:
    args = ["-c:v", "libx264", "-profile:v", "high", "-preset", "slow", "-crf", "18",
            "-r", str(config.FPS), "-movflags", "+faststart"]
    return args + (["-an"] if mute else
                   ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])


def finish(raw: Path, out_path: Path, *, mute: bool = False) -> Path:
    """Scale one clip to Instagram's exact 1080x1920 H.264/AAC contract."""
    run_ffmpeg(["-i", str(raw), "-vf", _delivery_filter(),
                *_encode_args(mute), str(out_path)])
    return out_path


def stitch(clips: list[Path], out_path: Path, *, mute: bool = False) -> Path:
    """Concatenate beats and deliver one Reels-ready file."""
    if not clips:
        raise ValueError("nothing to stitch")
    listing = out_path.parent / "concat.txt"
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing),
                "-vf", _delivery_filter(), *_encode_args(mute), str(out_path)])
    listing.unlink(missing_ok=True)
    return out_path
