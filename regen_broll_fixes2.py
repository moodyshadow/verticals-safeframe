import sys, random, time
sys.path.insert(0, r"G:\AI\YoutubeShortsPipeline")
from pathlib import Path
from verticals.broll import _generate_image_local_sd, _vision_qa_frame, animate_frame

work_dir = Path(r"C:\Users\szabo\.verticals\media\work_1788390102_en")

SLOTS = {
    # was a mismatched Minecraft Enderman stock clip — genuinely wrong game
    1: (
        "A single grotesque zombie monster's face in extreme close-up, "
        "decayed skin, glowing eyes, emerging from darkness, one scene, no "
        "collage, no panels. survival horror game aesthetic, dramatic "
        "lighting, high contrast, cinematic, vibrant colors, 4K quality"
    ),
    # was a 4-panel tiled grid — "split-screen comparison" wording seems to
    # trigger multi-panel generation, so rephrased to force a single frame
    4: (
        "One single wide cinematic shot of an eerie abandoned city street "
        "at dusk, a lone survivor walking toward the camera, one continuous "
        "scene, no collage, no grid, no panels, no split screen. survival "
        "horror game aesthetic, dramatic lighting, high contrast, "
        "cinematic, vibrant colors, 4K quality"
    ),
    7: (
        "One single close-up cinematic shot of a determined character in a "
        "leather jacket holding a pistol, standing in a foggy alley, one "
        "continuous scene, no collage, no grid, no panels, no split screen. "
        "survival horror game aesthetic, dramatic lighting, high contrast, "
        "cinematic, vibrant colors, 4K quality"
    ),
}

for idx, prompt in SLOTS.items():
    raw_path = work_dir / f"broll_{idx}_regen3_raw.png"
    out_path = work_dir / f"broll_{idx}.mp4"
    print(f"=== slot {idx} ===")
    passed = False
    for attempt in range(4):
        seed = random.randint(0, 2**31 - 1)
        _generate_image_local_sd(prompt, raw_path, seed=seed)
        passed, reason = _vision_qa_frame(raw_path)
        print(f"  attempt {attempt+1}: seed={seed} passed={passed} reason={reason}")
        if passed:
            break
    if not passed:
        print(f"  WARNING: slot {idx} never passed vision QA, using last attempt anyway")

    for wait_attempt in range(10):
        if raw_path.exists() and raw_path.stat().st_size > 0:
            break
        time.sleep(1)

    animate_frame(raw_path, out_path, duration=47.013633, effect="zoom_in")
    print(f"  -> {out_path}")

print("ALL DONE")
