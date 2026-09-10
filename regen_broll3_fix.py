import sys, random, time
sys.path.insert(0, r"G:\AI\YoutubeShortsPipeline")
from pathlib import Path
from verticals.broll import _generate_image_local_sd, _vision_qa_frame, animate_frame

work_dir = Path(r"C:\Users\szabo\.verticals\media\work_1788390102_en")
raw_path = work_dir / "broll_3_regen2_raw.png"
out_path = work_dir / "broll_3.mp4"

# Generic, rule-compliant replacement — no real named individual depicted.
prompt = (
    "A game industry veteran sitting in a dim office, looking thoughtful "
    "and concerned at a monitor displaying a horror game screen, silhouette "
    "lighting. gaming aesthetic, dramatic lighting, high contrast, "
    "cinematic, vibrant colors, 4K quality"
)

passed = False
for attempt in range(4):
    seed = random.randint(0, 2**31 - 1)
    _generate_image_local_sd(prompt, raw_path, seed=seed)
    passed, reason = _vision_qa_frame(raw_path)
    print(f"attempt {attempt+1}: seed={seed} passed={passed} reason={reason}")
    if passed:
        break
if not passed:
    print("WARNING: never passed vision QA, using last attempt anyway")

for wait_attempt in range(10):
    if raw_path.exists() and raw_path.stat().st_size > 0:
        break
    time.sleep(1)

animate_frame(raw_path, out_path, duration=47.013633, effect="zoom_in")
print("done ->", out_path)
