"""Image cards for @fflensapp posts (posts with an image get far more reach than text-only ones).

One layout, 1200x675 (X's 16:9 preview size), drawn with Pillow in League Lens' colors:
  render(title, subtitle=None, badge=None, badge_color="bad", rows=[(label, value, color?)], note=None) -> PNG bytes
No emoji in images (Inter has none); colors: "good", "bad", "warn", "blue" or None.
"""
import io, os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, PAD = 1200, 675, 64
C = {"bg": "#0b0f14", "card": "#141b24", "card2": "#19222d", "line": "#243040", "text": "#e6edf3", "muted": "#8b98a8", "dim": "#5d6b7c",
     "good": "#3ee08f", "blue": "#27b3ff", "bad": "#ff5c6c", "warn": "#ffbd4a"}


def font(weight, size):
    return ImageFont.truetype(os.path.join(HERE, "fonts", f"Inter-{weight}.ttf"), size)


def fit(d, s, f, max_w):
    s = str(s)
    if d.textlength(s, font=f) <= max_w:
        return s
    while len(s) > 1 and d.textlength(s + "…", font=f) > max_w:
        s = s[:-1]
    return s + "…"


def logo(d, x, y):
    d.ellipse((x, y - 15, x + 28, y + 13), outline=C["good"], width=5)
    d.line((x + 24, y + 9, x + 34, y + 19), fill=C["good"], width=5)
    d.text((x + 48, y), "League Lens", font=font(800, 28), fill=C["text"], anchor="lm")


def render(title, subtitle=None, badge=None, badge_color="bad", rows=(), note=None):
    img = Image.new("RGB", (W, H), C["bg"])
    glow = Image.new("RGB", (W, H), C["bg"])
    gd = ImageDraw.Draw(glow)
    for i in range(26):  # soft green glow top-right, like the site's share images
        r = 520 - i * 18
        shade = tuple(int(int(C["bg"][k:k + 2], 16) + (int(C["good"][k:k + 2], 16) - int(C["bg"][k:k + 2], 16)) * 0.006 * i) for k in (1, 3, 5))
        gd.ellipse((W - 80 - r, 40 - r, W - 80 + r, 40 + r), fill=shade)
    img = Image.blend(img, glow, 0.9)
    d = ImageDraw.Draw(img)
    rows = list(rows)[:6]
    tight = len(rows) > 4
    logo(d, PAD, 58 if tight else 70)
    y = 122 if tight else 150
    bw = 0
    if badge:
        bf = font(800, 30)
        bw = int(d.textlength(badge, font=bf)) + 44
        col = C.get(badge_color, badge_color)
        d.rounded_rectangle((W - PAD - bw, y - 4, W - PAD, y + 54), 14, fill=col)
        d.text((W - PAD - bw / 2, y + 25), badge, font=bf, fill=C["bg"], anchor="mm")
    d.text((PAD, y + 25), fit(d, title, font(800, 54), W - PAD * 2 - bw - 24), font=font(800, 54), fill=C["text"], anchor="lm")
    y += 72
    if subtitle:
        d.text((PAD, y + 20), fit(d, subtitle, font(500, 28), W - PAD * 2), font=font(500, 28), fill=C["muted"], anchor="lm")
        y += 56
    top, bottom = y + (4 if tight else 14), H - 100
    rh = min(74, (bottom - top) // max(1, len(rows)))
    lf, vf = font(700, min(30, int(rh * 0.5))), font(800, min(32, int(rh * 0.54)))
    for i, row in enumerate(rows):
        label, value = row[0], row[1]
        color = C.get(row[2], C["text"]) if len(row) > 2 and row[2] else C["text"]
        ry = top + i * rh
        if i % 2 == 0:
            d.rounded_rectangle((PAD - 16, ry, W - PAD + 16, ry + rh - 8), 14, fill=C["card"])
        cy = ry + (rh - 8) / 2
        vw = d.textlength(str(value), font=vf)
        d.text((PAD + 8, cy), fit(d, label, lf, W - PAD * 2 - vw - 60), font=lf, fill=C["text"], anchor="lm")
        d.text((W - PAD - 8, cy), str(value), font=vf, fill=color, anchor="rm")
    d.line((PAD, H - 82, W - PAD, H - 82), fill=C["line"], width=2)
    d.text((PAD, H - 44), "fflens.app", font=font(800, 32), fill=C["good"], anchor="lm")
    d.text((W - PAD, H - 44), note or "Free fantasy league analyzer for Sleeper & ESPN", font=font(500, 22), fill=C["dim"], anchor="rm")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


if __name__ == "__main__":  # python3 card.py out.png: a sample card to eyeball
    import sys
    open(sys.argv[1] if len(sys.argv) > 1 else "card-sample.png", "wb").write(render(
        "Christian McCaffrey", "RB · SF · hamstring", "OUT", "bad",
        [("Rest of season", "RB3"), ("Dynasty Superflex", "RB5"), ("Next man up", "Isaac Guerendo", "good"), ("Sleeper adds, last 24h", "48,212", "good")]))
