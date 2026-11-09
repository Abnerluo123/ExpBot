"""Generate monochrome ExpBot icons matching the warm-stone + ink palette."""
from io import BytesIO
from pathlib import Path
from struct import pack
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'app' / 'web'
S = 4

# Palette aligned with wizard.css monochrome editorial tokens.
STONE = '#f5f4f1'
PAPER = '#ffffff'
INK = '#1a1918'
LINE = '#c9c6bf'
MUTED = '#a8a59e'
ON_INK = '#fafaf8'


def artwork(size=1024):
    """Large icon: rounded stone tile, single document, ink check badge."""
    im = Image.new('RGBA', (1024 * S, 1024 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def rr(box, radius, fill, outline=None, width=1):
        d.rounded_rectangle(tuple(round(v * S) for v in box), radius * S,
                            fill=fill, outline=outline, width=max(1, width * S))

    def line(points, color, width):
        d.line([(int(x * S), int(y * S)) for x, y in points],
               fill=color, width=max(1, width * S), joint='curve')

    # Soft stone tile.
    rr((28, 28, 996, 996), 220, STONE)

    # Single document card — centered, quiet weight.
    rr((250, 170, 774, 780), 56, PAPER, INK, 14)
    # Folded corner.
    line([(620, 170), (620, 280), (774, 280)], INK, 12)
    d.polygon([(620 * S, 170 * S), (774 * S, 280 * S), (620 * S, 280 * S)], fill=STONE)

    # Text lines.
    line([(340, 390), (620, 390)], MUTED, 18)
    line([(340, 470), (680, 470)], MUTED, 18)
    line([(340, 550), (580, 550)], MUTED, 18)
    line([(340, 630), (640, 630)], MUTED, 18)

    # Ink check badge.
    d.ellipse((360 * S, 680 * S, 664 * S, 984 * S), fill=STONE)
    d.ellipse((388 * S, 708 * S, 636 * S, 956 * S), fill=INK)
    line([(450, 830), (510, 888), (590, 770)], ON_INK, 28)
    return im.resize((size, size), Image.Resampling.LANCZOS)


# Native-pixel layouts for taskbar / caption sizes.
# box, radius, stroke, text lines, badge circle, check polyline
SMALL_LAYOUTS = {
    16: ((3, 2, 12, 12), 1.5, 1.4, ((5, 5, 9), (5, 8, 8)), (7, 7, 16, 16), ((9, 11.5), (11, 13.5), (14, 10))),
    20: ((4, 2, 15, 15), 2, 1.6, ((6, 6, 12), (6, 9, 11)), (9, 9, 20, 20), ((11.5, 14.5), (14, 17), (18, 12.5))),
    24: ((4, 3, 18, 18), 2, 1.8, ((7, 7, 14), (7, 11, 13)), (10, 10, 24, 24), ((13.5, 17), (16.5, 20), (21, 14.5))),
    32: ((6, 4, 24, 25), 3, 2.2, ((10, 10, 19), (10, 15, 17)), (14, 14, 32, 32), ((18, 23), (22, 27), (28, 19.5))),
    40: ((7, 5, 30, 32), 3.5, 2.6, ((12, 12, 24), (12, 19, 22)), (17, 17, 40, 40), ((22.5, 28.5), (27.5, 33.5), (35.5, 24))),
    48: ((9, 6, 36, 38), 4, 3.0, ((15, 15, 29), (15, 23, 26)), (21, 21, 48, 48), ((27, 34.5), (33, 40.5), (42.5, 29))),
    64: ((12, 8, 48, 50), 5, 4.0, ((20, 20, 38), (20, 30, 34)), (28, 28, 64, 64), ((36, 46), (44, 54), (57, 38.5))),
}


def small_artwork(size):
    box, radius, stroke, lines, badge, check = SMALL_LAYOUTS[size]
    scale = 4
    image = Image.new('RGBA', (size * scale, size * scale))
    draw = ImageDraw.Draw(image)
    coords = lambda values: tuple(round(value * scale) for value in values)
    draw.rounded_rectangle(coords((0, 0, size - .25, size - .25)),
                           round(size * .2 * scale), fill=STONE)
    draw.rounded_rectangle(coords(box), round(radius * scale), fill=PAPER,
                           outline=INK, width=round(stroke * scale))
    for x, y, end in lines:
        draw.line(coords((x, y, end, y)), fill=MUTED, width=round(stroke * scale))
    gap = max(.75, stroke / 2)
    draw.ellipse(coords((badge[0] - gap, badge[1] - gap, badge[2] + gap, badge[3] + gap)), fill=STONE)
    draw.ellipse(coords(badge), fill=INK)
    points = [coords(point) for point in check]
    check_width = round(stroke * 1.15 * scale)
    draw.line(points, fill=ON_INK, width=check_width, joint='curve')
    for x, y in points:
        r = check_width / 2
        draw.ellipse((round(x - r), round(y - r), round(x + r), round(y + r)), fill=ON_INK)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(images, path):
    """Embed each authored image verbatim; Pillow's ICO save resizes one source."""
    frames = []
    for size, image in sorted(images.items()):
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        frames.append((size, buffer.getvalue()))
    offset = 6 + 16 * len(frames)
    directory, payload = [], []
    for size, data in frames:
        directory.append(pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        payload.append(data)
        offset += len(data)
    path.write_bytes(pack('<HHH', 0, 1, len(frames)) + b''.join(directory + payload))


def write_svg(path):
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">'
        f'<rect x="28" y="28" width="968" height="968" rx="220" fill="{STONE}"/>'
        f'<rect x="250" y="170" width="524" height="610" rx="56" fill="{PAPER}" stroke="{INK}" stroke-width="14"/>'
        f'<path d="M620 170v110h154" fill="none" stroke="{INK}" stroke-width="12" stroke-linejoin="round"/>'
        f'<path d="M620 170 774 280H620z" fill="{STONE}"/>'
        f'<g stroke="{MUTED}" stroke-width="18" stroke-linecap="round">'
        '<path d="M340 390h280M340 470h340M340 550h240M340 630h300"/>'
        '</g>'
        f'<circle cx="512" cy="832" r="152" fill="{STONE}"/>'
        f'<circle cx="512" cy="832" r="124" fill="{INK}"/>'
        f'<path d="m450 830 60 58 80-118" fill="none" stroke="{ON_INK}" stroke-width="28" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '</svg>\n',
        encoding='utf-8',
    )


if __name__ == '__main__':
    WEB.mkdir(parents=True, exist_ok=True)
    images = {size: small_artwork(size) for size in SMALL_LAYOUTS}
    for size in (96, 128, 192, 256, 512, 1024):
        images[size] = artwork(size)
    for size, image in images.items():
        image.save(WEB / f'icon-{size}.png')
    save_ico({size: image for size, image in images.items() if size <= 256}, WEB / 'icon.ico')
    write_svg(WEB / 'icon.svg')
    print('Generated monochrome ExpBot icons (stone + ink)')
