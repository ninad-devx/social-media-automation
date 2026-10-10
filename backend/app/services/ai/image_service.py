"""Generate a real, publishable image for a post.

Two strategies, tried in order:

1. `provider` - ask the configured model for an image. Works with any
   OpenAI-compatible endpoint that exposes `/images/generations`.
2. `card` - render the copy into a designed 1080x1080 card locally with
   Pillow. This ALWAYS works, needs no extra key or quota, and is what makes
   Instagram posts publishable at all (Instagram refuses text-only posts).

The card path exists because generative image models are frequently not
reachable with the same credential that generates text - Google, for example,
requires OAuth for its image models and rejects API-key auth with a 401. A
user who can generate captions must still be able to get a usable image.
"""
from __future__ import annotations

import io
import textwrap
from dataclasses import dataclass

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.services.ai.base import ProviderError, ProviderTimeoutError, ProviderUnavailableError

# Instagram feed images are square; 1080x1080 is the safe default for all
# three platforms we support.
DEFAULT_SIZE = (1080, 1080)

# Distinct, brand-neutral gradients. Chosen for contrast with white text.
_THEMES: tuple[tuple[str, tuple[str, str, str]], ...] = (
    ("indigo", ("#312e81", "#6366f1")),
    ("teal", ("#0f766e", "#2dd4bf")),
    ("sunset", ("#9a3412", "#fb923c")),
    ("rose", ("#9f1239", "#fb7185")),
    ("slate", ("#0f172a", "#475569")),
    ("forest", ("#14532d", "#4ade80")),
    ("violet", ("#4c1d95", "#a78bfa")),
)


@dataclass
class ImageResult:
    """A generated image, ready to attach to a post."""

    data: bytes
    mime_type: str
    #: "provider" or "card" - surfaced in the UI so the source is never a mystery.
    source: str
    prompt_used: str
    width: int = DEFAULT_SIZE[0]
    height: int = DEFAULT_SIZE[1]


class ImageGenerationError(ProviderError):
    code = "AI_IMAGE_FAILED"


# --------------------------------------------------------------------------- #
# strategy 1: the provider's own image endpoint
# --------------------------------------------------------------------------- #
def generate_via_provider(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    size: tuple[str, str] = ("1024x1024"),
    timeout: float = 90.0,
) -> ImageResult:
    """Call an OpenAI-compatible `/images/generations` endpoint."""
    endpoint = f"{base_url.rstrip('/')}/images/generations"
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {"model": model, "prompt": prompt, "n": 1, "size": size[0]}

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(endpoint, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError("The image model did not respond in time.") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError("Could not reach the image model.") from exc

    if response.status_code >= 400:
        # Reuse the shared classifier so quota/auth problems read the same as
        # they do for text generation.
        from app.services.ai.openai_adapter import classify_status

        raise classify_status(response.status_code, response.text)

    try:
        payload = response.json()
        item = payload["data"][0]
        raw = item["b64_json"] if "b64_json" in item else None
        if raw is None and item.get("url"):
            with httpx.Client(timeout=timeout) as client:
                raw = _b64(client.get(item["url"]).content)
        if raw is None:
            raise ValueError("no image data in response")
        import base64

        return ImageResult(
            data=base64.b64decode(raw),
            mime_type=item.get("mime_type", "image/png"),
            source="provider",
            prompt_used=prompt,
        )
    except (ValueError, KeyError, IndexError) as exc:
        raise ImageGenerationError("The image model returned an unexpected response.") from exc


def _b64(raw: bytes) -> str:
    import base64

    return base64.b64encode(raw).decode()


# --------------------------------------------------------------------------- #
# strategy 2: a designed card rendered locally
# --------------------------------------------------------------------------- #
def render_card(
    *,
    headline: str,
    caption: str = "",
    brand: str = "",
    theme: str | None = None,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> ImageResult:
    """Render the post's own copy into a clean, always-valid image.

    Using the real caption (rather than a generic quote) means the image is
    genuinely about the post, which matters for a feed.
    """
    width, height = size
    theme_name, (dark, light) = _pick_theme(theme)

    image = Image.new("RGB", (width, height), dark)
    draw = ImageDraw.Draw(image)

    # Soft diagonal wash so the flat colour does not look like a placeholder.
    glow = Image.new("RGB", (width, height), light)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).ellipse(
        (int(width * 0.15), int(-height * 0.35), int(width * 1.35), int(height * 0.75)),
        fill=210,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(160))
    image = Image.composite(Image.blend(image, glow, 0.55), image, mask)
    draw = ImageDraw.Draw(image)

    margin = int(width * 0.11)
    inner = width - margin * 2

    y = margin + int(height * 0.04)

    if brand:
        draw.text((margin, y), brand.upper(), font=_font(30, bold=True), fill=(255, 255, 255, 220))
        y += int(height * 0.06)

    # Headline: shrink and wrap until it fits the space available.
    headline = (headline or "").strip()
    headline = headline.split("\n")[0] if "\n" in headline else headline
    for size_px in (72, 64, 56, 48, 42, 36, 30):
        font = _font(size_px, bold=True)
        lines = _wrap(draw, headline, font, inner)
        line_h = int(size_px * 1.22)
        block_h = line_h * len(lines)
        if block_h < height * 0.52:
            break
    for line in lines:
        draw.text((margin, y), line, font=font, fill=(255, 255, 255))
        y += line_h

    y += int(height * 0.03)

    # Accent rule.
    draw.rounded_rectangle(
        (margin, y, margin + int(width * 0.16), y + 8), radius=4, fill=(255, 255, 255)
    )
    y += int(height * 0.06)

    # A little of the caption, so the image carries meaning beyond the headline.
    if caption.strip():
        cfont = _font(32, bold=False)
        for line in _wrap(draw, caption.strip(), cfont, inner)[:4]:
            if y > height - margin:
                break
            draw.text((margin, y), line, font=cfont, fill=(236, 240, 250))
            y += int(34 * 1.35)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return ImageResult(
        data=buffer.getvalue(),
        mime_type="image/png",
        source="card",
        prompt_used=theme_name,
        width=width,
        height=height,
    )


def image_prompt_for(topic: str, caption: str, platform_label: str) -> str:
    """A concise, safe image prompt derived from the post itself."""
    subject = topic.strip()
    if not subject and caption:
        subject = " ".join(caption.split()[:14])
    return (
        f"A clean, modern social media illustration about: {subject}. "
        f"Suitable for {platform_label}. Flat vector style, strong composition, "
        "generous negative space, no text, no words, no letters, no logos."
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _pick_theme(theme: str | None) -> tuple[str, tuple[str, str, str]]:
    if theme:
        for name, colors in _THEMES:
            if name == theme:
                return name, colors
    # Deterministic so the same post always gets the same look.
    from hashlib import sha256

    index = int(sha256((theme or "").encode()).hexdigest()[:8], 16) % len(_THEMES)
    return _THEMES[index]


def _font(size: int, *, bold: bool):
    """Load a real font, falling back to Pillow's bitmap font.

    Headless machines often have no system fonts; the fallback still renders,
    it just looks plainer.
    """
    candidates = (
        [
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        ]
        if hasattr(__import__("os"), "name") and __import__("os").name == "nt"
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines