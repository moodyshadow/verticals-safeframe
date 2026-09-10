"""Streamlit control panel for the Daily Overclocked pipeline — script,
b-roll, voice/music/captions, and assemble/review/upload, all in one place
instead of one-off scripts per stage.

Run with:  streamlit run dashboard.py
"""
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st
import psutil
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verticals.config import DRAFTS_DIR, MEDIA_DIR, load_config
from verticals.niche import load_niche, list_niches, get_voice_config, get_caption_config, get_music_config
from verticals.draft import generate_draft
from verticals.research import research_topic
from verticals.tts import generate_voiceover
from verticals.captions import generate_captions
from verticals.music import select_and_prepare_music
from verticals.assemble import assemble_video
from verticals.upload import upload_to_youtube
from verticals.thumbnail import generate_thumbnail
from verticals.broll import (
    _generate_image_local_sd, _generate_image_img2img_local_sd,
    _generate_video_local_animatediff, _generate_video_local_ltx,
    _generate_video_kling,
    _comfyui_reachable, _check_sd_webui_reachable,
)
from verticals.config import get_kling_key
from verticals.stock_media import fetch_topical_broll, fetch_topical_broll_video
from verticals.agent_bus import read_all as read_conversation, GENERAL_CHANNEL

st.set_page_config(page_title="Daily Overclocked — Pipeline", layout="wide")

# --- Password gate ---
# This dashboard can trigger real actions (spend API credits, publish live
# to YouTube), so once it's reachable from more than just this machine's own
# loopback (e.g. over LAN, for phone access) it needs a login, not just
# network-level trust. Password lives in config.json, same place every other
# credential this pipeline uses is kept.
_DASHBOARD_PASSWORD = load_config().get("DASHBOARD_PASSWORD", "")
if _DASHBOARD_PASSWORD and not st.session_state.get("authed"):
    st.title("Daily Overclocked")
    pw = st.text_input("Password", type="password", key="login_pw")
    if st.button("Log in"):
        if pw == _DASHBOARD_PASSWORD:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()

DRAFTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Draft I/O helpers
# ---------------------------------------------------------------------------

def list_drafts() -> list[Path]:
    return sorted(DRAFTS_DIR.glob("*.json"), key=lambda p: int(p.stem), reverse=True)


def load_draft(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_draft(job_id: str, draft: dict) -> None:
    path = DRAFTS_DIR / f"{job_id}.json"
    path.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")


def work_dir_for(job_id: str, lang: str = "en") -> Path:
    d = MEDIA_DIR / f"work_{job_id}_{lang}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def draft_label(path: Path) -> str:
    try:
        d = load_draft(path)
    except Exception:
        return path.stem
    title = (d.get("script") or "")[:60].replace("\n", " ")
    status = "\U0001F7E2 live" if d.get("youtube_url_en") else "⚪ local"
    return f"{path.stem} [{d.get('niche', '?')}] {status} — {title}"


# ---------------------------------------------------------------------------
# Sidebar: pick or create a job
# ---------------------------------------------------------------------------

st.sidebar.title("Daily Overclocked")
draft_paths = list_drafts()

mode = st.sidebar.radio("Mode", ["Existing job", "New job", "Video Editor"], horizontal=True)

if mode == "Video Editor":
    from verticals.video_editor import edit_video, _video_info, split_into_clips

    st.title("\U0001F3AC Video Editor")
    st.caption("Edit any video file on disk with a plain-English instruction, one step at a time.")

    EDITOR_TMP = Path(r"C:\Users\szabo\.verticals\media\editor_sessions")
    EDITOR_TMP.mkdir(parents=True, exist_ok=True)

    if "editor_current_path" not in st.session_state:
        st.session_state["editor_current_path"] = ""
        st.session_state["editor_history"] = []
        st.session_state["editor_step"] = 0

    src_input = st.text_input(
        "Video file path", value=st.session_state.get("editor_source_input", ""),
        placeholder=r"C:\Users\szabo\Downloads\Phone gaming\generated_video.mp4",
    )
    if st.button("\U0001F4C2 Load this video"):
        p = Path(src_input.strip('"'))
        if not p.exists():
            st.error(f"File not found: {p}")
        else:
            st.session_state["editor_source_input"] = src_input
            st.session_state["editor_current_path"] = str(p)
            st.session_state["editor_history"] = []
            st.session_state["editor_step"] = 0
            st.rerun()

    current_path = st.session_state.get("editor_current_path", "")
    if current_path and Path(current_path).is_file():
        st.video(current_path)
        info = _video_info(Path(current_path))
        st.caption(f"{info['width']}x{info['height']} · {info['duration']:.1f}s")

        st.markdown("---")
        st.subheader("Edit history")
        if not st.session_state["editor_history"]:
            st.caption("No edits applied yet.")
        for i, h in enumerate(st.session_state["editor_history"]):
            st.write(f"**Step {i + 1}:** \"{h['instruction']}\" → `{h['action']}`")

        instruction = st.text_input(
            "Describe the next edit",
            placeholder="e.g. trim the first 3 seconds, or make it 20% brighter, or crop to 9:16",
            key="editor_instruction",
        )
        use_vision = st.checkbox(
            "\U0001F441\ufe0f Let a vision model watch a few frames first",
            value=True,
            help="Grounds instructions like 'cut the boring part' or 'start where the car appears' "
                 "in what's actually on screen, not just duration math. Adds a few seconds.",
        )
        col1, col2 = st.columns(2)
        with col1:
            if st.button("\u2728 Apply edit", type="primary", disabled=not instruction.strip()):
                with st.spinner("Watching frames + parsing instruction + running ffmpeg..."):
                    try:
                        step = st.session_state["editor_step"] + 1
                        out_path = EDITOR_TMP / f"edit_step_{step}.mp4"
                        action = edit_video(Path(current_path), instruction, out_path, use_vision=use_vision)
                        st.session_state["editor_history"].append({"instruction": instruction, "action": action})
                        st.session_state["editor_current_path"] = str(out_path)
                        st.session_state["editor_step"] = step
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
        with col2:
            if st.button("\u21A9\uFE0F Undo last edit", disabled=not st.session_state["editor_history"]):
                st.session_state["editor_history"].pop()
                st.session_state["editor_step"] = max(st.session_state["editor_step"] - 1, 0)
                step = st.session_state["editor_step"]
                st.session_state["editor_current_path"] = (
                    str(EDITOR_TMP / f"edit_step_{step}.mp4") if step > 0
                    else st.session_state.get("editor_source_input", "")
                )
                st.rerun()

        st.markdown("---")
        st.subheader("\U0001F4B0 Cut for sale")
        st.caption(
            "Split this video into sequential clips of a set length — for prepping "
            "footage into stock-site-sized pieces before uploading."
        )
        clip_len = st.slider("Clip length (seconds)", min_value=5, max_value=60, value=15, step=5)
        if st.button("✂️ Cut into clips"):
            with st.spinner("Cutting..."):
                try:
                    stock_dir = EDITOR_TMP / "for_sale"
                    clips = split_into_clips(Path(current_path), float(clip_len), stock_dir)
                    st.success(f"Cut into {len(clips)} clip(s), saved in {stock_dir}")
                    for c in clips:
                        st.write(f"- `{c.name}`")
                except Exception as e:
                    st.error(str(e))

        st.markdown("---")
        st.caption(f"Current file: `{current_path}`")
    st.stop()

if mode == "New job":
    niches = list_niches()
    new_niche = st.sidebar.selectbox("Niche", niches, index=niches.index("tech") if "tech" in niches else 0)

    if "new_topic_input" not in st.session_state:
        st.session_state["new_topic_input"] = ""
    if "new_url_input" not in st.session_state:
        st.session_state["new_url_input"] = ""

    if st.sidebar.button(
        "\U0001F525 Find biggest news",
        help="Scans this niche's real topic sources (Reddit, RSS, Google Trends) and auto-picks the top story.",
    ):
        with st.spinner(f"Scanning {new_niche} sources for the biggest story..."):
            try:
                from verticals.topics.engine import TopicEngine

                engine = TopicEngine(niche=new_niche)
                candidates = engine.discover(limit=15)
                if not candidates:
                    st.sidebar.error("No topics found — check source config in the Engines tab.")
                else:
                    picked_title = engine.auto_pick(candidates)
                    picked = next(
                        (c for c in candidates if c.title.strip() == picked_title.strip()),
                        candidates[0],
                    )
                    # Set the widgets' own session-state keys directly, before
                    # they're instantiated below in this same run — no rerun
                    # needed, and avoids the "value= ignored because the key
                    # already exists" gotcha.
                    st.session_state["new_topic_input"] = picked.title
                    st.session_state["new_url_input"] = picked.url or ""
            except Exception as e:
                st.sidebar.error(f"Failed: {e}")

    new_topic = st.sidebar.text_area("Topic / headline", height=80, key="new_topic_input")
    new_url = st.sidebar.text_input("Source URL (optional)", key="new_url_input")
    new_word_count = st.sidebar.slider(
        "Script length (words)", min_value=60, max_value=300, value=150, step=10,
        help="Roughly 2.5 words/second spoken — 150 words is about 60 seconds. Leave at the default to use the niche's own target instead.",
    )
    use_default_length = st.sidebar.checkbox("Use niche's default length", value=True)
    if st.sidebar.button("\U0001F195 Create draft (research + script)", type="primary"):
        if not new_topic.strip():
            st.sidebar.error("Enter a topic first.")
        else:
            with st.spinner("Researching + drafting script..."):
                try:
                    word_count_arg = None if use_default_length else str(new_word_count)
                    draft = generate_draft(new_topic, niche=new_niche, url=new_url, word_count=word_count_arg)
                    job_id = str(int(time.time()))
                    draft["niche"] = new_niche
                    save_draft(job_id, draft)
                    st.session_state["selected_job"] = job_id
                    st.sidebar.success(f"Created job {job_id}")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Failed: {e}")
    st.stop()

if not draft_paths:
    st.sidebar.warning("No drafts found yet. Switch to 'New job' to create one.")
    st.stop()

labels = {p.stem: draft_label(p) for p in draft_paths}
default_job = st.session_state.get("selected_job", draft_paths[0].stem)
if default_job not in labels:
    default_job = draft_paths[0].stem
selected_job = st.sidebar.selectbox(
    "Job", options=list(labels.keys()), format_func=lambda k: labels[k],
    index=list(labels.keys()).index(default_job),
)
st.session_state["selected_job"] = selected_job

draft_path = DRAFTS_DIR / f"{selected_job}.json"
draft = load_draft(draft_path)
niche_name = draft.get("niche", "general")
lang = "en"
wd = work_dir_for(selected_job, lang)

st.sidebar.markdown(f"**Niche:** {niche_name}")
is_live = bool(draft.get("youtube_url_en"))
if is_live:
    st.sidebar.markdown(f"**Live:** {draft['youtube_url_en']}")

with st.sidebar.expander("\U0001F5D1️ Delete this job"):
    if is_live:
        st.warning("This job is already LIVE on YouTube. Deleting only removes the local draft/files, not the published video.")
    if st.checkbox("Yes, delete it", key=f"confirm_delete_{selected_job}"):
        if st.button("Delete permanently", type="primary", key=f"do_delete_{selected_job}"):
            draft_path.unlink(missing_ok=True)
            if wd.exists():
                shutil.rmtree(wd, ignore_errors=True)
            st.session_state.pop("selected_job", None)
            st.sidebar.success(f"Deleted job {selected_job}")
            st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("**Engine status** (see 🖥️ Engines tab)")


def _sd_ok() -> bool:
    try:
        _check_sd_webui_reachable()
        return True
    except Exception:
        return False


st.sidebar.write("SD webui:", "✅ up" if _sd_ok() else "❌ down")
st.sidebar.write("ComfyUI/LTX:", "✅ up" if _comfyui_reachable() else "❌ down")

# ---------------------------------------------------------------------------
# Engine process control — find/kill/relaunch SD webui and ComfyUI by the
# port each one owns, and read live GPU/RAM usage so it's obvious which
# process is holding what before deciding to restart anything.
# ---------------------------------------------------------------------------
SD_WEBUI_DIR = Path(r"C:\Users\szabo\stable-diffusion-webui")
COMFYUI_DIR = Path(r"G:\AI\ComfyUI")


def _find_pid_by_port(port: int) -> int | None:
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
            return c.pid
    return None


def _kill_pid_tree(pid: int) -> None:
    try:
        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            child.kill()
        proc.kill()
    except psutil.NoSuchProcess:
        pass


def _restart_sd_webui() -> str:
    pid = _find_pid_by_port(7860)
    if pid:
        _kill_pid_tree(pid)
        time.sleep(2)
    log_out = open(SD_WEBUI_DIR / "dashboard-restart.log", "w")
    log_err = open(SD_WEBUI_DIR / "dashboard-restart-err.log", "w")
    subprocess.Popen(
        ["cmd", "/c", "webui-user.bat"],
        cwd=str(SD_WEBUI_DIR),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=log_out, stderr=log_err,
    )
    return ("SD webui restart launched — takes ~30-60s (sometimes longer if "
            "it lands on a busier GPU). Logs: dashboard-restart-err.log in "
            "the webui folder.")


def _restart_comfyui() -> str:
    pid = _find_pid_by_port(8188)
    if pid:
        _kill_pid_tree(pid)
        time.sleep(2)
    env = os.environ.copy()
    # Pins to the 2060 Super (device 0 by PCI bus order) — matches how it's
    # normally launched, so it doesn't land on the same card as SD webui.
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = "0"
    log_out = open(COMFYUI_DIR / "dashboard-restart.log", "w")
    log_err = open(COMFYUI_DIR / "dashboard-restart-err.log", "w")
    subprocess.Popen(
        [str(COMFYUI_DIR / "venv" / "Scripts" / "python.exe"), "main.py", "--port", "8188"],
        cwd=str(COMFYUI_DIR), env=env,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=log_out, stderr=log_err,
    )
    return ("ComfyUI restart launched — takes ~10-20s. Logs: "
            "dashboard-restart-err.log in the ComfyUI folder.")


def _restart_ollama() -> str:
    pid = _find_pid_by_port(11434)
    if pid:
        _kill_pid_tree(pid)
        time.sleep(2)
    ollama_exe = shutil.which("ollama") or r"C:\Users\szabo\AppData\Local\Programs\Ollama\ollama.exe"
    log_out = open(MEDIA_DIR / "dashboard-restart-ollama.log", "w")
    log_err = open(MEDIA_DIR / "dashboard-restart-ollama-err.log", "w")
    subprocess.Popen(
        [ollama_exe, "serve"],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=log_out, stderr=log_err,
    )
    return ("Ollama restart launched — takes ~5-10s. Logs: "
            "dashboard-restart-ollama-err.log in the media folder.")


def _ollama_ok() -> bool:
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _gpu_stats() -> list[dict]:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        gpus = []
        for line in r.stdout.strip().splitlines():
            idx, name, util, used, total = [x.strip() for x in line.split(",")]
            gpus.append({"index": idx, "name": name, "util": util, "used": int(used), "total": int(total)})
        return gpus
    except Exception:
        return []


def _gpu_processes() -> list[dict]:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory,process_name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        procs = []
        for line in r.stdout.strip().splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) != 3:
                continue
            pid, mem, name = parts
            procs.append({"pid": pid, "mem": mem, "name": Path(name).name})
        return procs
    except Exception:
        return []


tab_script, tab_broll, tab_audio, tab_final, tab_engines = st.tabs(
    ["\U0001F4DD Script", "\U0001F3AC B-roll", "\U0001F50A Voice/Music/Captions",
     "\U0001F4E6 Assemble/Review/Upload", "\U0001F5A5\uFE0F Engines"]
)

# ---------------------------------------------------------------------------
# Tab: Script
# ---------------------------------------------------------------------------
with tab_script:
    st.subheader("Script")
    if draft.get("mood"):
        st.caption(f"Mood: **{draft['mood']}**")
    script_text = st.text_area("Script text", value=draft.get("script", ""), height=250, key="script_edit")

    conversation_id = draft.get("writer_critic_conversation_id")
    if conversation_id:
        messages = read_conversation(conversation_id)
        with st.expander(f"\U0001F4AC Writer / critic / marketing conversation ({len(messages)} messages)"):
            if not messages:
                st.caption("No messages recorded for this conversation.")
            avatars = {"writer": "✍️", "critic": "\U0001F9D0", "marketing": "\U0001F4C8"}
            default_avatar = "\U0001F4AC"
            for m in messages:
                emoji = avatars.get(m["sender"], default_avatar)
                label = f"{emoji} {m['sender']}"
                attempt = m.get("meta", {}).get("attempt")
                if attempt:
                    label += f" · attempt {attempt}"
                with st.chat_message(m["sender"] if m["sender"] in ("writer", "critic") else "assistant"):
                    st.markdown(f"**{label}**")
                    st.write(m["text"])

    cta_variants = load_niche(niche_name).get("script", {}).get("cta_variants", [])
    if cta_variants:
        cta_ids = [c.get("id", str(i)) if isinstance(c, dict) else str(c) for i, c in enumerate(cta_variants)]
        cta_col1, cta_col2 = st.columns([2, 1])
        with cta_col1:
            picked_cta_id = st.selectbox("Subscribe/CTA to insert", cta_ids, key="cta_pick")
        with cta_col2:
            st.write("")  # vertical spacer to align the button with the selectbox
            if st.button("➕ Insert CTA"):
                picked = next(
                    c for c, cid in zip(cta_variants, cta_ids) if cid == picked_cta_id
                )
                template = picked.get("template", "") if isinstance(picked, dict) else str(picked)
                current = st.session_state.get("script_edit", "").rstrip()
                separator = " " if current.endswith((".", "!", "?")) else ". "
                draft["script"] = (current + separator + template).strip() if current else template
                save_draft(selected_job, draft)
                # A widget's own session_state key can't be reassigned directly
                # once instantiated in the same run — deleting it instead means
                # the next run's text_area(value=...) initializes fresh from
                # the just-saved draft rather than the stale in-widget text.
                del st.session_state["script_edit"]
                st.rerun()
        picked_preview = next(
            (c for c, cid in zip(cta_variants, cta_ids) if cid == picked_cta_id), None
        )
        if picked_preview:
            tmpl = picked_preview.get("template", "") if isinstance(picked_preview, dict) else str(picked_preview)
            st.caption(f"“{tmpl}” — appends to the end of the script above; fill in any {{placeholder}} by hand afterward.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("\U0001F4BE Save script"):
            draft["script"] = script_text
            save_draft(selected_job, draft)
            st.success("Saved.")
    with col2:
        if st.button("\U0001F504 Re-run research"):
            with st.spinner("Researching..."):
                try:
                    draft["research"] = research_topic(draft.get("topic", script_text[:80]))
                    save_draft(selected_job, draft)
                    st.success("Research refreshed.")
                except Exception as e:
                    st.error(str(e))

    with st.expander("\U0001F3B2 Regenerate script", expanded=False):
        st.caption(
            "Throws away the current script (and any b-roll/voiceover/captions/music/"
            "video already built from it) and drafts a fresh one from the same topic. "
            "The topic/research stay the same — only the writing changes."
        )
        regen_use_default_length = st.checkbox("Use niche's default length", value=True, key="regen_default_len")
        regen_word_count = st.slider(
            "Script length (words)", min_value=60, max_value=300, value=150, step=10,
            key="regen_word_count", disabled=regen_use_default_length,
        )
        if st.button("\U0001F3B2 Regenerate script", type="primary"):
            topic = draft.get("news", "")
            if draft.get("youtube_url_en"):
                st.error(
                    f"This job is already live at {draft['youtube_url_en']} — "
                    "regenerating the script here won't change the published video. "
                    "Start a new job instead."
                )
            elif not topic:
                st.error("This draft has no stored topic (`news`) to regenerate from.")
            else:
                with st.spinner("Rewriting script..."):
                    try:
                        wc = None if regen_use_default_length else str(regen_word_count)
                        new_draft = generate_draft(
                            topic,
                            niche=draft.get("niche", "general"),
                            platform=draft.get("platform", "shorts"),
                            word_count=wc,
                        )
                        # Only the script-and-metadata fields get replaced — job_id,
                        # niche, platform, and any already-live YouTube URL stay put.
                        for field in (
                            "script", "mood", "broll_prompts", "youtube_title",
                            "youtube_description", "youtube_tags", "instagram_caption",
                            "tiktok_caption", "thumbnail_prompt", "research",
                        ):
                            if field in new_draft:
                                draft[field] = new_draft[field]
                        # Everything downstream was built from the OLD script/b-roll
                        # prompts, so it's now stale — same reset this pipeline
                        # already does by hand whenever the script changes.
                        ps = draft.get("_pipeline_state", {})
                        for stage in ("broll", "voiceover", "whisper", "captions",
                                      "music", "assemble", "thumbnail", "video_qa"):
                            ps.pop(stage, None)
                        draft["_pipeline_state"] = ps
                        for field in ("video_en", "video_hi", "video_es", "video_pt",
                                      "video_de", "video_fr", "video_ja", "video_ko"):
                            draft.pop(field, None)
                        draft["reviewed_by_claude"] = False
                        save_draft(selected_job, draft)
                        st.success("Script regenerated — b-roll/voiceover/assemble stages were cleared, redo them below.")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    with st.expander("B-roll prompts"):
        prompts = draft.get("broll_prompts", [])
        new_prompts = []
        for i, p in enumerate(prompts):
            new_prompts.append(st.text_input(f"Frame {i}", value=p, key=f"prompt_{i}"))
        if st.button("\U0001F4BE Save prompts"):
            draft["broll_prompts"] = new_prompts
            save_draft(selected_job, draft)
            st.success("Saved.")

    with st.expander("Raw draft JSON"):
        st.json(draft)

# ---------------------------------------------------------------------------
# Tab: B-roll
# ---------------------------------------------------------------------------
with tab_broll:
    st.subheader("B-roll frames")
    frames = draft.get("_pipeline_state", {}).get("broll", {}).get("artifacts", {}).get("frames", [])
    prompts = draft.get("broll_prompts", [])
    n = max(len(prompts), len(frames))

    if not prompts:
        st.info("No b-roll prompts yet — write the script first.")

    for i in range(n):
        prompt = prompts[i] if i < len(prompts) else "(no prompt)"
        current = frames[i] if i < len(frames) else None
        st.markdown(f"**Frame {i}** — {prompt}")
        cols = st.columns([2, 1, 1, 1, 1, 1])
        with cols[0]:
            if current and Path(current).exists():
                if str(current).lower().endswith((".mp4", ".mov")):
                    st.video(current)
                else:
                    st.image(current)
            else:
                st.caption("No frame yet.")

        def _set_frame(path: Path, idx=i):
            frames_local = draft.setdefault("_pipeline_state", {}).setdefault(
                "broll", {}).setdefault("artifacts", {}).setdefault("frames", list(frames))
            while len(frames_local) <= idx:
                frames_local.append(None)
            frames_local[idx] = str(path)
            save_draft(selected_job, draft)

        with cols[1]:
            if st.button("Stock", key=f"stock_{i}"):
                with st.spinner("Searching stock..."):
                    try:
                        vid = fetch_topical_broll_video(prompt)
                        if vid:
                            out = wd / f"broll_{i}_stock.mp4"
                            out.write_bytes(vid)
                            _set_frame(out)
                            st.rerun()
                        else:
                            photo = fetch_topical_broll(prompt)
                            if photo:
                                out = wd / f"broll_{i}_stock.jpg"
                                out.write_bytes(photo)
                                _set_frame(out)
                                st.rerun()
                            else:
                                st.warning("No stock match found.")
                    except Exception as e:
                        st.error(str(e))
        with cols[2]:
            if st.button("SDXL still", key=f"sdxl_{i}"):
                with st.spinner("Generating (SDXL)..."):
                    try:
                        out = wd / f"broll_{i}_sdxl.png"
                        _generate_image_local_sd(prompt, out, seed=random.randint(0, 2**31 - 1), sdxl=True)
                        _set_frame(out)
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
        with cols[3]:
            if st.button("LTX video", key=f"ltx_{i}"):
                with st.spinner("Generating (LTX-Video, ~2-3 min)..."):
                    try:
                        out = wd / f"broll_{i}_ltx.mp4"
                        ok = _generate_video_local_ltx(prompt, out, seed=random.randint(0, 2**31 - 1))
                        if ok:
                            _set_frame(out)
                            st.rerun()
                        else:
                            st.warning("LTX generation failed — check ComfyUI is running.")
                    except Exception as e:
                        st.error(str(e))
        with cols[4]:
            if st.button("AnimateDiff", key=f"ad_{i}"):
                with st.spinner("Generating (AnimateDiff)..."):
                    try:
                        out = wd / f"broll_{i}_ad.mp4"
                        ok = _generate_video_local_animatediff(prompt, out)
                        if ok:
                            _set_frame(out)
                            st.rerun()
                        else:
                            st.warning("AnimateDiff generation failed.")
                    except Exception as e:
                        st.error(str(e))
        with cols[5]:
            if st.button("\U0001F3AC Kling", key=f"kling_{i}", help="Real motion via the Kling API — costs credits, best saved for shots a static frame can't fake"):
                if not get_kling_key():
                    st.error("No KLING_API_KEY configured.")
                else:
                    with st.spinner("Generating (Kling, ~1-3 min)..."):
                        try:
                            out = wd / f"broll_{i}_kling.mp4"
                            kling_prompt = prompt.split(".. ")[0].strip()
                            kling_prompt = f"{kling_prompt}, smooth cinematic motion, product photography style"
                            ok = _generate_video_kling(kling_prompt, out)
                            if ok:
                                _set_frame(out)
                                st.rerun()
                            else:
                                st.warning("Kling generation failed — check logs (often a balance/quota issue).")
                        except Exception as e:
                            st.error(str(e))

        with st.expander("\U0001F3AC Generate with Kling manually (web app credits)"):
            st.caption(
                "The API button above needs a separate paid API balance — until "
                "that's topped up, generate by hand on klingai.com using your "
                "existing credits, then drop the download in the box below."
            )
            kling_prompt = prompt.split(".. ")[0].strip()
            kling_prompt = f"{kling_prompt}, smooth cinematic motion, product photography style, 5 seconds"
            st.text_area("Kling prompt (copy this)", value=kling_prompt, height=70, key=f"klingprompt_{i}")
            st.markdown("[Open klingai.com ↗](https://klingai.com)")

        with st.expander("Use my own picture/video for this frame"):
            uploaded = st.file_uploader(
                "Choose an image or video file", type=["jpg", "jpeg", "png", "webp", "mp4", "mov"],
                key=f"upload_{i}",
            )
            if uploaded is not None and st.button("Use this file", key=f"useown_{i}"):
                dest = wd / f"broll_{i}_own{Path(uploaded.name).suffix.lower()}"
                dest.write_bytes(uploaded.getvalue())
                _set_frame(dest)
                st.success(f"Using {uploaded.name} for frame {i}.")
                st.rerun()
            with st.popover("...or paste a file path instead"):
                own_path = st.text_input(
                    "Full path to an image or video file on disk", key=f"ownpath_{i}",
                    placeholder=r"C:\Users\szabo\Downloads\photo.jpg",
                )
                if st.button("Use this path", key=f"useownpath_{i}"):
                    src = Path(own_path.strip('"'))
                    if not src.exists():
                        st.error(f"File not found: {src}")
                    elif not src.is_file():
                        st.error(f"Not a file: {src}")
                    else:
                        dest = wd / f"broll_{i}_own{src.suffix.lower()}"
                        dest.write_bytes(src.read_bytes())
                        _set_frame(dest)
                        st.success(f"Using {src.name} for frame {i}.")
                        st.rerun()
        st.markdown("---")

# ---------------------------------------------------------------------------
# Tab: Voice / Music / Captions
# ---------------------------------------------------------------------------
with tab_audio:
    st.subheader("Voiceover")
    profile = load_niche(niche_name)
    voice_cfg = get_voice_config(profile)
    current_voice = voice_cfg.get("voice_id") or "(niche default)"
    st.caption(f"Niche default voice: {current_voice}")
    override_voice = st.text_input("Voice override (Edge TTS voice name, blank = niche default)", value="")

    vo_path = wd / f"voiceover_{lang}.mp3"
    col1, col2 = st.columns(2)
    with col1:
        if st.button("\U0001F3A4 Generate voiceover"):
            with st.spinner("Generating voiceover..."):
                try:
                    cfg = dict(voice_cfg)
                    if override_voice.strip():
                        cfg["voice_id"] = override_voice.strip()
                    path = generate_voiceover(draft.get("script", ""), wd, lang=lang, voice_config=cfg)
                    st.success(f"Saved: {path}")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    with col2:
        if vo_path.exists():
            st.audio(str(vo_path))

    st.subheader("Captions")
    if st.button("\U0001F4C4 Generate captions"):
        if not vo_path.exists():
            st.warning("Generate the voiceover first.")
        else:
            with st.spinner("Running WhisperX alignment..."):
                try:
                    cap_cfg = get_caption_config(profile)
                    result = generate_captions(
                        vo_path, wd, lang=lang,
                        highlight_color=cap_cfg.get("highlight_color", "#FFFF00"),
                        words_per_group=cap_cfg.get("words_per_group", 4),
                        font_family=cap_cfg.get("font_family", "Arial"),
                        font_size=int(cap_cfg.get("font_size", 72)),
                        script=draft.get("script", ""),
                    )
                    st.session_state["captions_result"] = result
                    draft.setdefault("_pipeline_state", {})["captions"] = {
                        "status": "done",
                        "artifacts": {
                            "srt_path": result.get("srt_path", ""),
                            "ass_path": result.get("ass_path", ""),
                            "words": result.get("words", []),
                        },
                    }
                    save_draft(selected_job, draft)
                    st.success(f"Got {len(result.get('words', []))} words.")
                except Exception as e:
                    st.error(str(e))
    ass_path = draft.get("_pipeline_state", {}).get("captions", {}).get("artifacts", {}).get("ass_path", "")
    if ass_path:
        st.caption(f"ASS: {ass_path}")

    st.subheader("Music")
    if st.button("\U0001F3B5 Select + prepare music"):
        if not vo_path.exists():
            st.warning("Generate the voiceover first.")
        else:
            with st.spinner("Selecting track + building duck filter..."):
                try:
                    music_cfg = get_music_config(profile)
                    result = select_and_prepare_music(
                        vo_path, wd, niche=niche_name,
                        duck_speech=music_cfg.get("duck_volume_speech", 0.12),
                        duck_gap=music_cfg.get("duck_volume_gap", 0.25),
                    )
                    draft.setdefault("_pipeline_state", {})["music"] = {"status": "done", "artifacts": result}
                    save_draft(selected_job, draft)
                    st.success(f"Track: {result.get('track_path')}")
                except Exception as e:
                    st.error(str(e))
    music_path = draft.get("_pipeline_state", {}).get("music", {}).get("artifacts", {}).get("track_path", "")
    if music_path and Path(music_path).exists():
        st.audio(music_path)

def _video_info(path: Path) -> dict:
    """Duration/resolution via ffprobe, plus file size — for the preview
    panel's quick-facts line. Best-effort: ffprobe missing/failing just
    means those facts are omitted, never a hard error on the preview."""
    info = {"size_mb": path.stat().st_size / (1024 * 1024)}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(r.stdout)
        stream = (data.get("streams") or [{}])[0]
        info["width"] = stream.get("width")
        info["height"] = stream.get("height")
        info["duration"] = float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# Tab: Assemble / Review / Upload
# ---------------------------------------------------------------------------
with tab_final:
    st.subheader("\U0001F680 Full auto-produce")
    st.caption(
        "Runs every remaining stage (b-roll, voiceover, captions, music, "
        "assemble, thumbnail) back to back. Skips any stage already done — "
        "e.g. this job's manually-picked b-roll frames are left alone."
    )
    if st.button("\U0001F680 Produce this video end-to-end", type="primary", key="full_auto_produce"):
        if draft.get("youtube_url_en"):
            st.error(
                f"This job is already live at {draft['youtube_url_en']} — "
                "re-producing here won't change the published video. Start a new job instead."
            )
        else:
            from verticals.broll import generate_broll
            from verticals.config import BROLL_COUNT
            from verticals.niche import get_visual_source_priority
            from verticals.state import PipelineState

            state = PipelineState(draft)
            profile = load_niche(niche_name)
            progress = st.empty()
            try:
                if not state.is_done("broll"):
                    progress.info("Generating b-roll...")
                    default_prompts = [f"Cinematic landscape, variation {i + 1}" for i in range(BROLL_COUNT)]
                    use_stock = get_visual_source_priority(profile) != "ai_only"
                    frames, fallback_count, used_asset_paths = generate_broll(
                        draft.get("broll_prompts", default_prompts), wd, use_stock=use_stock
                    )
                    state.complete_stage("broll", {
                        "frames": [str(f) for f in frames],
                        "fallback_count": fallback_count,
                        "used_asset_paths": used_asset_paths,
                    })
                    save_draft(selected_job, draft)

                if not state.is_done("voiceover"):
                    progress.info("Generating voiceover...")
                    voice_cfg = get_voice_config(profile)
                    vo_out = generate_voiceover(draft.get("script", ""), wd, lang=lang, voice_config=voice_cfg)
                    state.complete_stage("voiceover", {"path": str(vo_out)})
                    save_draft(selected_job, draft)
                vo_path_auto = Path(state.get_artifact("voiceover", "path"))

                if not state.is_done("captions"):
                    progress.info("Aligning captions (WhisperX)...")
                    cap_cfg = get_caption_config(profile)
                    cap_result = generate_captions(
                        vo_path_auto, wd, lang=lang,
                        highlight_color=cap_cfg.get("highlight_color", "#FFFF00"),
                        words_per_group=cap_cfg.get("words_per_group", 4),
                        font_family=cap_cfg.get("font_family", "Arial"),
                        font_size=int(cap_cfg.get("font_size", 72)),
                        script=draft.get("script", ""),
                    )
                    state.complete_stage("captions", {
                        "srt_path": cap_result.get("srt_path", ""),
                        "ass_path": cap_result.get("ass_path", ""),
                        "words": cap_result.get("words", []),
                    })
                    save_draft(selected_job, draft)

                if not state.is_done("music"):
                    progress.info("Selecting + prepping music...")
                    music_cfg = get_music_config(profile)
                    music_result = select_and_prepare_music(
                        vo_path_auto, wd, niche=niche_name,
                        duck_speech=music_cfg.get("duck_volume_speech", 0.12),
                        duck_gap=music_cfg.get("duck_volume_gap", 0.25),
                    )
                    state.complete_stage("music", music_result)
                    save_draft(selected_job, draft)

                if not state.is_done("assemble"):
                    progress.info("Assembling final video...")
                    frames_auto = state.get_artifact("broll", "frames", [])
                    captions_art = draft["_pipeline_state"].get("captions", {}).get("artifacts", {})
                    music_art = draft["_pipeline_state"].get("music", {}).get("artifacts", {})
                    video_out = assemble_video(
                        frames=[Path(f) for f in frames_auto],
                        voiceover=vo_path_auto,
                        out_dir=wd,
                        job_id=selected_job,
                        lang=lang,
                        ass_path=captions_art.get("ass_path"),
                        music_path=music_art.get("track_path"),
                        duck_filter=music_art.get("duck_filter"),
                        srt_path=captions_art.get("srt_path"),
                        words=captions_art.get("words"),
                    )
                    state.complete_stage("assemble", {"video_path": str(video_out)})
                    draft[f"video_{lang}"] = str(video_out)
                    save_draft(selected_job, draft)

                if not state.is_done("thumbnail"):
                    progress.info("Generating thumbnail...")
                    thumb_out = generate_thumbnail(draft, wd)
                    state.complete_stage("thumbnail", {"thumbnail_path": str(thumb_out)})
                    save_draft(selected_job, draft)

                progress.success("Done — full video produced. Review it below before uploading.")
                st.rerun()
            except Exception as e:
                progress.error(f"Stopped at an error: {e}")

    st.markdown("---")
    st.subheader("\U0001F3AC Final preview")
    preview_video_path = draft.get(f"video_{lang}", "")
    preview_thumb_path = draft.get("_pipeline_state", {}).get("thumbnail", {}).get("artifacts", {}).get("thumbnail_path", "")

    if not preview_video_path or not Path(preview_video_path).exists():
        st.info("No assembled video yet — finish the stages below, then hit **Assemble video**.")
    else:
        pcol1, pcol2, _spacer = st.columns([1, 1, 2])
        with pcol1:
            st.video(preview_video_path, width=220)
            info = _video_info(Path(preview_video_path))
            facts = []
            if info.get("width") and info.get("height"):
                facts.append(f"{info['width']}x{info['height']}")
            if info.get("duration"):
                facts.append(f"{info['duration']:.1f}s")
            facts.append(f"{info['size_mb']:.1f} MB")
            st.caption(" · ".join(facts))
        with pcol2:
            st.markdown("**Thumbnail**")
            if preview_thumb_path and Path(preview_thumb_path).exists():
                st.image(preview_thumb_path, width=220)
            else:
                st.caption("Not generated yet.")
            if st.button("\U0001F5BC️ Generate thumbnail"):
                with st.spinner("Generating thumbnail..."):
                    try:
                        thumb_path = generate_thumbnail(draft, wd)
                        draft.setdefault("_pipeline_state", {})["thumbnail"] = {
                            "status": "done", "artifacts": {"thumbnail_path": str(thumb_path)},
                        }
                        save_draft(selected_job, draft)
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    st.markdown("---")
    st.subheader("Assemble")
    frames = draft.get("_pipeline_state", {}).get("broll", {}).get("artifacts", {}).get("frames", [])
    captions_art = draft.get("_pipeline_state", {}).get("captions", {}).get("artifacts", {})
    music_art = draft.get("_pipeline_state", {}).get("music", {}).get("artifacts", {})

    ready = bool(frames) and vo_path.exists()
    st.write(f"Frames: {len(frames)} | Voiceover: {'✅' if vo_path.exists() else '❌'} | "
             f"Captions: {'✅' if captions_art.get('ass_path') else '❌'} | "
             f"Music: {'✅' if music_art.get('track_path') else '❌'}")

    if st.button("\U0001F3AC Assemble video", disabled=not ready, type="primary"):
        with st.spinner("Assembling (ffmpeg + Whisper-derived captions)..."):
            try:
                video_path = assemble_video(
                    frames=[Path(f) for f in frames],
                    voiceover=vo_path,
                    out_dir=wd,
                    job_id=selected_job,
                    lang=lang,
                    ass_path=captions_art.get("ass_path"),
                    music_path=music_art.get("track_path"),
                    duck_filter=music_art.get("duck_filter"),
                    srt_path=captions_art.get("srt_path"),
                    words=captions_art.get("words"),
                )
                draft.setdefault("_pipeline_state", {})["assemble"] = {
                    "status": "done", "artifacts": {"video_path": str(video_path)},
                }
                draft[f"video_{lang}"] = str(video_path)
                save_draft(selected_job, draft)
                st.success(f"Assembled: {video_path}")
                st.rerun()  # so the Final preview panel above picks it up immediately
            except Exception as e:
                st.error(str(e))

    video_path = draft.get(f"video_{lang}", "")

    st.subheader("Review")
    reviewed = st.checkbox("I've personally reviewed this video", value=bool(draft.get("reviewed_by_claude")))
    if reviewed != bool(draft.get("reviewed_by_claude")):
        draft["reviewed_by_claude"] = reviewed
        save_draft(selected_job, draft)

    st.subheader("Upload")
    if draft.get("youtube_url_en"):
        st.success(f"Already live: {draft['youtube_url_en']}")
    else:
        colu1, colu2 = st.columns(2)
        with colu1:
            schedule_date = st.date_input("Schedule date")
        with colu2:
            schedule_time = st.time_input("Schedule time (UTC)")
        if st.button("⬆️ Upload to YouTube", disabled=not reviewed):
            with st.spinner("Uploading..."):
                try:
                    url = upload_to_youtube(
                        Path(video_path), draft,
                        srt_path=Path(captions_art.get("srt_path")) if captions_art.get("srt_path") else None,
                        lang=lang,
                    )
                    draft["youtube_url_en"] = url
                    save_draft(selected_job, draft)
                    st.success(f"Uploaded: {url}")
                except Exception as e:
                    st.error(str(e))
        if not reviewed:
            st.caption("Check the review box above to enable upload.")

# ---------------------------------------------------------------------------
# Tab: Engines
# ---------------------------------------------------------------------------
with tab_engines:
    st.subheader("Status & controls")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"**SD webui (port 7860)** — {'✅ up' if _sd_ok() else '❌ down'}")
        if st.button("🔁 Restart SD webui"):
            with st.spinner("Restarting..."):
                st.info(_restart_sd_webui())
    with col2:
        st.markdown(f"**ComfyUI/LTX (port 8188)** — {'✅ up' if _comfyui_reachable() else '❌ down'}")
        if st.button("🔁 Restart ComfyUI"):
            with st.spinner("Restarting..."):
                st.info(_restart_comfyui())
    with col3:
        st.markdown(f"**Ollama (port 11434)** — {'✅ up' if _ollama_ok() else '❌ down'}")
        if st.button("🔁 Restart Ollama"):
            with st.spinner("Restarting..."):
                st.info(_restart_ollama())

    st.markdown("---")
    st.subheader("GPU usage")
    if st.button("↻ Refresh"):
        st.rerun()

    gpus = _gpu_stats()
    if not gpus:
        st.warning("nvidia-smi not available.")
    else:
        for g in gpus:
            pct = g["used"] / g["total"] if g["total"] else 0
            st.markdown(f"**GPU {g['index']}: {g['name']}** — {g['used']} / {g['total']} MiB, {g['util']}% util")
            st.progress(min(pct, 1.0))

    st.markdown("**Processes using GPU memory**")
    procs = _gpu_processes()
    if not procs:
        st.caption("No GPU compute processes detected (or nvidia-smi unavailable).")
    else:
        for p in procs:
            mem_label = f"{p['mem']} MiB" if p["mem"] not in ("", "[N/A]", "N/A") else "N/A"
            st.write(f"PID {p['pid']} · {mem_label} · {p['name']}")

    st.markdown("---")
    st.subheader("System RAM")
    vm = psutil.virtual_memory()
    st.write(f"{vm.available / 1e9:.1f} GB available / {vm.total / 1e9:.1f} GB total "
             f"({vm.percent:.0f}% used)")
    st.progress(vm.percent / 100)

    with st.expander("Top RAM-using processes"):
        procs_ram = sorted(
            (p.info for p in psutil.process_iter(["pid", "name", "memory_info"])
             if p.info.get("memory_info")),
            key=lambda i: i["memory_info"].rss, reverse=True,
        )[:15]
        for p in procs_ram:
            st.write(f"PID {p['pid']} · {p['memory_info'].rss / 1e9:.2f} GB · {p['name']}")

    st.markdown("---")
    st.subheader("\U0001F4E2 General agent channel")
    st.caption(
        "A standing channel any stage can post to whenever it wants — not tied to "
        "one draft/topic. Currently used by the marketing agent's daily report."
    )
    general_messages = read_conversation(GENERAL_CHANNEL)
    if not general_messages:
        st.caption("No messages yet.")
    else:
        for m in reversed(general_messages[-20:]):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["ts"]))
            st.markdown(f"**\U0001F4C8 {m['sender']}** · {when}")
            st.write(m["text"])
