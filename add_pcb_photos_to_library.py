from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance
from verticals.media_library import add_to_library

SCRATCH = Path(r"C:\Users\szabo\AppData\Local\Temp\claude\C--Users-szabo-stable-diffusion-webui\807866e8-4ac8-43a4-921c-fa7e7ce9d047\scratchpad")

# 3730 dropped: too motion-blurred to touch up meaningfully.
SHARP_PHOTOS = ["IMG_3731", "IMG_3732", "IMG_3733", "IMG_3734", "IMG_3735"]

KEYWORDS = [
    "circuit board", "pcb", "printed circuit board", "computer chip", "microchip",
    "electronics", "hardware", "motherboard", "semiconductor", "close-up",
    "tech", "engineering",
]

def touch_up(src: Path, dst: Path):
    img = Image.open(src).convert("RGB")
    # mild sharpen + slight contrast lift — these are real macro phone photos,
    # some a bit soft; this is a light polish, not a fix for genuine motion blur.
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=3))
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.05)
    img.save(dst, quality=95)

added = []
for name in SHARP_PHOTOS:
    src = SCRATCH / f"{name}.jpg"
    dst = SCRATCH / f"{name}_touched.jpg"
    touch_up(src, dst)
    data = dst.read_bytes()
    path = add_to_library(data, KEYWORDS, "photo", source="user_provided", source_url="")
    added.append(str(path))
    print(f"Added {name} -> {path}")

print("Done. Added:", len(added))
