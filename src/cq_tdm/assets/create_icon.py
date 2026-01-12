#!/usr/bin/env python3
"""Generate CQ TDM application icon."""

import math
from pathlib import Path

def create_svg_icon() -> str:
    """Create an SVG icon for CQ TDM (without noise - noise added in PNG generation).

    Design: Stylized water phantom cross-section with quality checkmark.
    - Outer plastic ring (slightly brighter gray)
    - Inner water area (darker gray)
    - ROI indicators for quality measurements
    - Checkmark for quality control validation
    """
    svg = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <!-- Plastic ring (outer phantom shell) -->
  <circle cx="128" cy="128" r="118" fill="#787878" />

  <!-- Water phantom interior (darker gray) -->
  <circle cx="128" cy="128" r="106" fill="#585858" />

  <!-- Central ROI indicator -->
  <circle cx="128" cy="128" r="40" fill="none" stroke="#2A82DA" stroke-width="5" stroke-dasharray="8,4" />

  <!-- Peripheral ROI indicators (4 cardinal positions) -->
  <circle cx="128" cy="48" r="12" fill="none" stroke="#2A82DA" stroke-width="4" />
  <circle cx="208" cy="128" r="12" fill="none" stroke="#2A82DA" stroke-width="4" />
  <circle cx="128" cy="208" r="12" fill="none" stroke="#2A82DA" stroke-width="4" />
  <circle cx="48" cy="128" r="12" fill="none" stroke="#2A82DA" stroke-width="4" />

  <!-- Quality checkmark (bottom right) -->
  <g transform="translate(156, 156)">
    <circle cx="40" cy="40" r="38" fill="#2ECC71" />
    <path d="M 22 40 L 34 52 L 58 26"
          fill="none" stroke="white" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" />
  </g>
</svg>'''
    return svg


def add_noise_to_image(img, noise_intensity=20, noise_scale=4):
    """Add Poisson-like grain noise to the phantom area, excluding colored/white pixels.

    Args:
        img: PIL Image to add noise to
        noise_intensity: Scale factor for Poisson noise
        noise_scale: Size of noise pixels (higher = larger grain)
    """
    import numpy as np
    from PIL import Image

    # Convert to numpy array
    arr = np.array(img).astype(np.float32)
    height, width = arr.shape[:2]

    if arr.shape[2] >= 3:
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

        # Check if pixel is approximately gray (R, G, B are similar)
        max_rgb = np.maximum(np.maximum(r, g), b)
        min_rgb = np.minimum(np.minimum(r, g), b)
        color_diff = max_rgb - min_rgb
        is_gray = color_diff < 30  # Threshold for "grayness"

        # Exclude white pixels (checkmark) - high brightness
        brightness = (r + g + b) / 3
        is_not_white = brightness < 200
        is_gray = is_gray & is_not_white
    else:
        is_gray = np.ones((height, width), dtype=bool)

    # Create phantom circle mask (including plastic ring)
    y, x = np.ogrid[:height, :width]
    center_x, center_y = width // 2, height // 2
    scale = width / 256
    radius = int(118 * scale)  # Include plastic ring
    dist_from_center = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)
    phantom_mask = dist_from_center <= radius

    # Also check alpha channel if present (exclude transparent areas)
    if arr.shape[2] == 4:
        alpha_mask = arr[:, :, 3] > 128
        phantom_mask = phantom_mask & alpha_mask

    # Final mask: gray non-white pixels inside phantom
    noise_mask = phantom_mask & is_gray

    # Generate coarse Poisson noise (at reduced resolution) then upscale
    noise_h = max(1, height // noise_scale)
    noise_w = max(1, width // noise_scale)

    # Poisson noise: lambda parameter controls intensity
    # Generate two Poisson samples and subtract to get centered noise
    lam = noise_intensity
    poisson_noise = (np.random.poisson(lam, (noise_h, noise_w)).astype(np.float32)
                     - np.random.poisson(lam, (noise_h, noise_w)).astype(np.float32))

    # Upscale noise using nearest neighbor for blocky effect
    noise_img = Image.fromarray(poisson_noise, mode='F')
    noise_img = noise_img.resize((width, height), Image.Resampling.NEAREST)
    noise = np.array(noise_img)

    # Apply noise only to RGB channels where mask is True
    for c in range(3):  # RGB channels
        arr[:, :, c] = np.where(
            noise_mask,
            np.clip(arr[:, :, c] + noise, 0, 255),
            arr[:, :, c]
        )

    return Image.fromarray(arr.astype(np.uint8))


def main():
    """Generate icon files."""
    assets_dir = Path(__file__).parent

    # Create SVG
    svg_content = create_svg_icon()
    svg_path = assets_dir / "icon.svg"
    svg_path.write_text(svg_content)
    print(f"Created: {svg_path}")

    # Try to create PNG and ICO using available tools
    try:
        import subprocess
        from io import BytesIO

        # Check for cairosvg (Python library)
        try:
            import cairosvg
            from PIL import Image

            # Create PNG with noise
            png_path = assets_dir / "icon.png"
            png_buffer = BytesIO()
            cairosvg.svg2png(bytestring=svg_content.encode(), write_to=png_buffer,
                           output_width=256, output_height=256)
            png_buffer.seek(0)
            img = Image.open(png_buffer).convert("RGBA")
            img_with_noise = add_noise_to_image(img, noise_intensity=20, noise_scale=4)
            img_with_noise.save(png_path)
            print(f"Created: {png_path}")

            # Create ICO (multiple sizes, each with appropriate noise)
            ico_path = assets_dir / "icon.ico"
            sizes = [256, 128, 64, 48, 32, 16]
            images = []
            for size in sizes:
                buffer = BytesIO()
                cairosvg.svg2png(bytestring=svg_content.encode(), write_to=buffer,
                               output_width=size, output_height=size)
                buffer.seek(0)
                img = Image.open(buffer).convert("RGBA")
                # Scale noise for different sizes
                noise_intensity = max(10, 20 * size // 256)
                noise_scale = max(2, 4 * size // 256)
                img_with_noise = add_noise_to_image(img, noise_intensity, noise_scale)
                images.append(img_with_noise)

            images[0].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes],
                          append_images=images[1:])
            print(f"Created: {ico_path}")

        except ImportError as e:
            print(f"cairosvg or PIL not available: {e}")
            print("Install with: pip install cairosvg Pillow")

    except Exception as e:
        print(f"Could not create PNG/ICO: {e}")
        print("SVG created successfully. Convert manually using online tools or ImageMagick.")


if __name__ == "__main__":
    main()
