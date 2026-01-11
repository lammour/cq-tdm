#!/usr/bin/env python3
"""Generate CQ TDM application icon."""

import math
from pathlib import Path

def create_svg_icon() -> str:
    """Create an SVG icon for CQ TDM.

    Design: Stylized CT cross-section with quality checkmark.
    - Outer ring representing CT scanner gantry
    - Inner circles representing phantom cross-section
    - Subtle checkmark for quality control
    """
    svg = '''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <defs>
    <!-- Gradient for outer ring (CT gantry) -->
    <linearGradient id="gantryGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#4A90A4;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#2D5A6B;stop-opacity:1" />
    </linearGradient>

    <!-- Gradient for inner area -->
    <linearGradient id="innerGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1E1E1E;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#2D2D2D;stop-opacity:1" />
    </linearGradient>

    <!-- Gradient for phantom -->
    <radialGradient id="phantomGrad" cx="50%" cy="50%" r="50%">
      <stop offset="0%" style="stop-color:#6B6B6B;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#4A4A4A;stop-opacity:1" />
    </radialGradient>
  </defs>

  <!-- Background circle (CT gantry outer) -->
  <circle cx="128" cy="128" r="120" fill="url(#gantryGrad)" />

  <!-- Inner dark area (gantry bore) -->
  <circle cx="128" cy="128" r="100" fill="url(#innerGrad)" />

  <!-- Phantom circle (water phantom cross-section) -->
  <circle cx="128" cy="128" r="65" fill="url(#phantomGrad)" stroke="#5A5A5A" stroke-width="2" />

  <!-- Central ROI indicator -->
  <circle cx="128" cy="128" r="26" fill="none" stroke="#2A82DA" stroke-width="2" stroke-dasharray="4,2" opacity="0.8" />

  <!-- Peripheral ROI indicators (4 cardinal positions) -->
  <circle cx="128" cy="75" r="8" fill="none" stroke="#2A82DA" stroke-width="1.5" opacity="0.6" />
  <circle cx="181" cy="128" r="8" fill="none" stroke="#2A82DA" stroke-width="1.5" opacity="0.6" />
  <circle cx="128" cy="181" r="8" fill="none" stroke="#2A82DA" stroke-width="1.5" opacity="0.6" />
  <circle cx="75" cy="128" r="8" fill="none" stroke="#2A82DA" stroke-width="1.5" opacity="0.6" />

  <!-- Quality checkmark (bottom right) -->
  <g transform="translate(170, 170)">
    <circle cx="30" cy="30" r="28" fill="#2ECC71" />
    <path d="M 18 30 L 26 38 L 44 20"
          fill="none" stroke="white" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" />
  </g>
</svg>'''
    return svg


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

        # Check for cairosvg (Python library)
        try:
            import cairosvg

            # Create PNG
            png_path = assets_dir / "icon.png"
            cairosvg.svg2png(bytestring=svg_content.encode(), write_to=str(png_path),
                           output_width=256, output_height=256)
            print(f"Created: {png_path}")

            # Create ICO (multiple sizes)
            ico_path = assets_dir / "icon.ico"
            # Create temporary PNGs for different sizes
            sizes = [256, 128, 64, 48, 32, 16]
            temp_pngs = []
            for size in sizes:
                temp_path = assets_dir / f"icon_{size}.png"
                cairosvg.svg2png(bytestring=svg_content.encode(), write_to=str(temp_path),
                               output_width=size, output_height=size)
                temp_pngs.append(temp_path)

            # Try to combine into ICO using PIL
            try:
                from PIL import Image
                images = [Image.open(p) for p in temp_pngs]
                images[0].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes])
                print(f"Created: {ico_path}")
            except ImportError:
                print("PIL not available, skipping ICO creation")
                print("Install with: pip install Pillow")

            # Clean up temp files
            for p in temp_pngs:
                p.unlink()

        except ImportError:
            print("cairosvg not available, trying ImageMagick...")

            # Try ImageMagick
            png_path = assets_dir / "icon.png"
            result = subprocess.run(
                ["convert", str(svg_path), "-resize", "256x256", str(png_path)],
                capture_output=True
            )
            if result.returncode == 0:
                print(f"Created: {png_path}")

                # Create ICO
                ico_path = assets_dir / "icon.ico"
                subprocess.run([
                    "convert", str(png_path),
                    "-define", "icon:auto-resize=256,128,64,48,32,16",
                    str(ico_path)
                ])
                print(f"Created: {ico_path}")
            else:
                print("ImageMagick not available")
                print("Install cairosvg: pip install cairosvg")
                print("Or ImageMagick: sudo apt install imagemagick")

    except Exception as e:
        print(f"Could not create PNG/ICO: {e}")
        print("SVG created successfully. Convert manually using online tools or ImageMagick.")


if __name__ == "__main__":
    main()
