import json
from pathlib import Path
from verticals.broll import generate_broll

draft_path = Path(r"C:\Users\szabo\.verticals\drafts\1788467225.json")
draft = json.loads(draft_path.read_text(encoding="utf-8"))

WORK_DIR = Path(r"C:\Users\szabo\.verticals\media\work_1788467225_en")

frames, fallback_count, used_asset_paths = generate_broll(
    draft["broll_prompts"], WORK_DIR, use_stock=True,
)

print()
print("=== Results ===")
for i, f in enumerate(frames):
    print(i, f)
print("fallback_count (generated, no stock/library match):", fallback_count)
print("used_asset_paths:", used_asset_paths)

draft["_pipeline_state"]["broll"]["artifacts"]["frames"] = [str(f) for f in frames]
draft["_pipeline_state"]["broll"]["artifacts"]["fallback_count"] = fallback_count
draft["_pipeline_state"]["broll"]["artifacts"]["used_asset_paths"] = list(used_asset_paths)
draft["broll_fallback_count"] = fallback_count
draft["broll_frame_count"] = len(frames)
draft.pop("reviewed_by_claude", None)
draft.pop("reviewed_at", None)
draft_path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
print("Draft frames updated (not yet reassembled).")
