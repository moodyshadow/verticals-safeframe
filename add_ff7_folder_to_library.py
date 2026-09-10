from pathlib import Path
from PIL import Image
from verticals.media_library import add_to_library

SRC = Path(r"C:\Users\szabo\Downloads\FF8 b-roll")

items = [
    ("EN_25_ff7rebirth_SS_0207_Trailer.webp",
     ["final fantasy 7", "final fantasy vii rebirth", "ff7", "cloud strife", "character closeup", "square enix"]),
    ("FF7 - Rebith.jpeg",
     ["final fantasy 7", "final fantasy vii rebirth", "ff7", "game logo", "square enix"]),
    ("FF7Rebirth_Nibelheim-1024x576.jpg",
     ["final fantasy 7", "final fantasy vii rebirth", "ff7", "sephiroth", "cloud strife", "tifa", "square enix"]),
    ("final-fantasy-vii-rebirth-is-top-5-all-time-ff-v0-wb6rgtp8u1nd1.webp",
     ["final fantasy 7", "final fantasy vii rebirth", "ff7", "key art", "cast", "square enix"]),
    ("square-enix-logo.jpg",
     ["square enix", "game publisher logo", "final fantasy"]),
]

for name, keywords in items:
    src = SRC / name
    img = Image.open(src).convert("RGB")
    tmp = src.with_suffix(".converted.jpg")
    img.save(tmp, quality=95)
    data = tmp.read_bytes()
    path = add_to_library(data, keywords, "photo", source="user_provided", source_url="")
    print(f"Added {name} -> {path}")
    tmp.unlink()

print("Done.")
