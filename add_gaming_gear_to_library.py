import subprocess
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance
from verticals.media_library import add_to_library

SRC_DIR = Path(r"D:\verticals_media_library\photos")
SCRATCH = Path(r"C:\Users\szabo\AppData\Local\Temp\claude\C--Users-szabo-stable-diffusion-webui\807866e8-4ac8-43a4-921c-fa7e7ce9d047\scratchpad")

PHOTOS = ["IMG_3736", "IMG_3737", "IMG_3739", "IMG_3740", "IMG_3741",
          "IMG_3742", "IMG_3743", "IMG_3744", "IMG_3745", "IMG_3746", "IMG_3747"]
VIDEOS = ["IMG_3748", "IMG_3749", "IMG_3750"]

KEYWORDS = [
    "gaming headset", "headset", "gaming keyboard", "keyboard", "mechanical keyboard",
    "ps5 controller", "dualsense", "controller", "gaming mouse", "mouse",
    "gaming desk", "gaming setup", "rgb", "desk setup",
]

def touch_up(src: Path, dst: Path):
    img = Image.open(src).convert("RGB")
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=110, threshold=3))
    img = ImageEnhance.Contrast(img).enhance(1.06)
    img.save(dst, quality=95)

added = []
for name in PHOTOS:
    heic = SRC_DIR / f"{name}.HEIC"
    jpg = SCRATCH / f"{name}.jpg"
    touched = SCRATCH / f"{name}_lib.jpg"
    touch_up(jpg, touched)
    data = touched.read_bytes()
    path = add_to_library(data, KEYWORDS, "photo", source="user_provided", source_url="")
    added.append(str(path))
    print(f"Added photo {name} -> {path}")

for name in VIDEOS:
    mov = SRC_DIR / f"{name}.MOV"
    mp4 = SCRATCH / f"{name}_lib.mp4"
    subprocess.run(["ffmpeg", "-y", "-i", str(mov), "-c:v", "libx264", "-preset", "fast",
                     "-crf", "20", "-c:a", "aac", str(mp4)], check=True, capture_output=True)
    data = mp4.read_bytes()
    path = add_to_library(data, KEYWORDS, "video", source="user_provided", source_url="")
    added.append(str(path))
    print(f"Added video {name} -> {path}")

print("Done. Added:", len(added))
