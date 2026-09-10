import sys, random
sys.path.insert(0, r"G:\AI\YoutubeShortsPipeline")
from pathlib import Path
from verticals.broll import _generate_image_local_sd, _vision_qa_frame, animate_frame

work_dir = Path(r"C:\Users\szabo\.verticals\media\work_1788390102_en")

SLOTS = {
    3: (
        "A shot of Hideki Kamiya, the director of the original Resident Evil "
        "2, looking serious and concerned, with a dark background. gaming "
        "aesthetic, dramatic lighting, high contrast, cinematic, vibrant "
        "colors, 4K quality"
    ),
    4: (
        "A split-screen comparison of a Resident Evil game from the early "
        "days and a current game, with a bold title overlay. gaming "
        "aesthetic, dramatic lighting, high contrast, cinematic, vibrant "
        "colors, 4K quality"
    ),
    5: (
        "A dark, fog-filled abandoned mansion corridor, a grotesque zombie "
        "creature lurking in the shadows ahead, a flickering flashlight beam "
        "cutting through the darkness, blood stains on the walls, survival "
        "horror game aesthetic, dramatic lighting, high contrast, cinematic, "
        "vibrant colors, 4K quality"
    ),
    6: (
        "A shot of the Resident Evil logo, with bold red and yellow colors, "
        "on a dark background. gaming aesthetic, dramatic lighting, high "
        "contrast, cinematic, vibrant colors, 4K quality"
    ),
    7: (
        "A comparison screenshot of the Resident Evil 2 remake and the "
        "original Resident Evil 2, with a bold title overlay. gaming "
        "aesthetic, dramatic lighting, high contrast, cinematic, vibrant "
        "colors, 4K quality"
    ),
}

for idx, prompt in SLOTS.items():
    raw_path = work_dir / f"broll_{idx}_regen_raw.png"
    out_path = work_dir / f"broll_{idx}.mp4"
    print(f"=== slot {idx} ===")
    passed = False
    for attempt in range(3):
        seed = random.randint(0, 2**31 - 1)
        _generate_image_local_sd(prompt, raw_path, seed=seed)
        passed, reason = _vision_qa_frame(raw_path)
        print(f"  attempt {attempt+1}: seed={seed} passed={passed} reason={reason}")
        if passed:
            break
    if not passed:
        print(f"  WARNING: slot {idx} never passed vision QA, using last attempt anyway")

    import time
    for wait_attempt in range(10):
        if raw_path.exists() and raw_path.stat().st_size > 0:
            break
        print(f"  raw file not visible yet, waiting... ({wait_attempt+1}/10)")
        time.sleep(1)
    else:
        raise RuntimeError(f"slot {idx}: {raw_path} never became visible on disk")

    animate_frame(raw_path, out_path, duration=47.013633, effect="zoom_in")
    print(f"  -> {out_path}")

print("ALL DONE")
