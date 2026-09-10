import json
from pathlib import Path
from verticals.broll import fetch_topical_broll_video, fetch_topical_broll

draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788636253.json")
draft = json.loads(draft_path.read_text(encoding="utf-8"))
WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788636253_en")
WORK_DIR.mkdir(parents=True, exist_ok=True)

used = set()
results = []
for i, prompt in enumerate(draft["broll_prompts"]):
    print(f"--- Frame {i} ---")
    print("prompt:", prompt[:90])
    vid = fetch_topical_broll_video(prompt, exclude_paths=used)
    if vid:
        p = WORK_DIR / f"stocktest_{i}.mp4"
        p.write_bytes(vid)
        results.append((i, "video", str(p)))
        print("  -> real video match")
        continue
    photo = fetch_topical_broll(prompt, exclude_paths=used)
    if photo:
        p = WORK_DIR / f"stocktest_{i}.jpg"
        p.write_bytes(photo)
        results.append((i, "photo", str(p)))
        print("  -> real photo match")
        continue
    results.append((i, "NONE", None))
    print("  -> NO STOCK MATCH")

print()
print("=== Summary ===")
for i, kind, path in results:
    print(i, kind, path)
