#!/usr/bin/env python3
"""একটা ফোল্ডার (অডিও + SRT + ছবি + config.json) থেকে MP4 ভিডিও বানায়।

ব্যবহার:
    python make_video.py .                      # ফাইলগুলো মূল ফোল্ডারে থাকলে
    python make_video.py product02 --out out/p2.mp4
টেস্ট:
    python make_video.py . --still 2 --still 12     # শুধু কয়েকটা স্টিল ছবি
    python make_video.py . --scale 0.5 --limit 10   # ছোট প্রিভিউ
"""
import argparse
import glob
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import align  # noqa: E402
import sfx  # noqa: E402

W, H = 1080, 1920
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


# --------------------------------------------------------------------------- ফাইল খোঁজা
def discover(project, cfg):
    files = sorted(os.listdir(project))

    def pick(pred, key):
        if cfg.get(key):
            p = os.path.join(project, cfg[key])
            if not os.path.exists(p):
                sys.exit(f"config.json-এ '{key}' হিসেবে দেওয়া ফাইল পাওয়া যায়নি: {cfg[key]}")
            return p
        for f in files:
            if pred(f.lower()):
                return os.path.join(project, f)
        return None

    is_bgm = lambda f: f.startswith(("bgm", "music", "background"))
    audio = pick(lambda f: f.endswith(AUDIO_EXT) and not is_bgm(f), "audio")
    bgm = pick(lambda f: f.endswith(AUDIO_EXT) and is_bgm(f), "bgm")
    image = pick(lambda f: f.endswith(IMG_EXT) and not f.startswith("logo"), "image")
    srt = pick(lambda f: f.endswith(".srt"), "srt")
    if not srt:  # .txt ফাইলেও SRT থাকতে পারে
        for f in files:
            if f.lower().endswith(".txt") and "-->" in open(os.path.join(project, f), encoding="utf-8-sig").read():
                srt = os.path.join(project, f)
    if not (audio and srt and image):
        sys.exit(
            "ফোল্ডারে অডিও (.mp3/.wav), সাবটাইটেল (.srt) আর প্রোডাক্ট ছবি (.png/.jpg/.webp) তিনটাই লাগবে।\n"
            f"পাওয়া গেছে: audio={audio} srt={srt} image={image}"
        )
    return audio, srt, image, bgm


# --------------------------------------------------------------------------- টাইমলাইন
def find_text(parts, text, from_part=0, from_char=0):
    """parts-এর মধ্যে text খুঁজে সময় বের করে (অংশের ভেতরে উচ্চারণ-অনুপাতে)।"""
    for pi in range(from_part, len(parts)):
        p = parts[pi]
        start_c = from_char if pi == from_part else 0
        idx = p["text"].find(text, start_c)
        if idx >= 0:
            before = align.syllables(p["text"][:idx]) if idx > 0 else 0
            tot = max(1, align.syllables(p["text"]))
            frac = min(0.95, before / tot) if idx > 0 else 0.0
            return p["start"] + (p["end"] - p["start"]) * frac, pi, idx + len(text)
    return None


def resolve_start(marker, parts, default=None):
    if isinstance(marker, (int, float)):
        return float(marker)
    if isinstance(marker, dict):
        if "cue" in marker:
            for p in parts:
                if p["cue"] == marker["cue"]:
                    return p["start"]
        if "text" in marker:
            r = find_text(parts, marker["text"])
            if r:
                return r[0]
        if "seconds" in marker:
            return float(marker["seconds"])
    return default


def build_timeline(cfg, parts, captions, audio_dur):
    tail = float(cfg.get("endHold", 1.4))
    total = audio_dur + tail
    scenes = [dict(s) for s in cfg["scenes"]]
    # শুরুর সময়
    for i, s in enumerate(scenes):
        if i == 0:
            s["start"] = 0.0
            continue
        t = resolve_start(s.get("start"), parts)
        if t is None:
            sys.exit(f"সিন #{i + 1} ({s.get('type')}) এর শুরুর জায়গা খুঁজে পাওয়া যায়নি: {s.get('start')}")
        s["start"] = max(0.0, t - float(cfg.get("sceneLead", 0.12)))
    for i, s in enumerate(scenes):
        s["end"] = scenes[i + 1]["start"] if i + 1 < len(scenes) else total
        if s["end"] <= s["start"] + 0.5:
            sys.exit(f"সিন #{i + 1} ({s['type']}) খুব ছোট বা ক্রম ঠিক নেই। config.json-এর start মার্কারগুলো ক্রমানুসারে দিন।")

    # আইটেম ও "…At" মার্কারের সময়
    for s in scenes:
        rng = [p for p in parts if s["start"] - 0.3 <= p["start"] < s["end"] - 0.05]
        first = parts.index(rng[0]) if rng else 0
        cur = (first, 0)
        items = []
        raw_items = s.get("items", [])
        for k, it in enumerate(raw_items):
            it = {"label": it} if isinstance(it, str) else dict(it)
            key = it.get("bn")
            t = None
            if key:
                r = find_text(parts, key, cur[0], cur[1])
                if r and r[0] < s["end"] - 0.1:
                    t, cur = r[0], (r[1], r[2])
            if t is None:  # না পেলে সিনের ভেতরে সমান দূরত্বে
                lead = 0.9
                n = len(raw_items)
                t = s["start"] + lead + (s["end"] - s["start"] - lead - 0.9) * (k / max(1, n))
            it["t"] = round(max(s["start"] + 0.35, t - 0.08), 3)
            items.append(it)
        if items:
            # সময় যেন ক্রমবর্ধমান থাকে
            for k in range(1, len(items)):
                items[k]["t"] = max(items[k]["t"], items[k - 1]["t"] + 0.3)
            s["items"] = items
        s["times"] = {}
        for key, val in list(s.items()):
            if key.endswith("At") and isinstance(val, dict):
                r = find_text(parts, val.get("text", ""), first, 0) if val.get("text") else None
                s["times"][key[:-2]] = round(max(s["start"] + 0.3, (r[0] if r else s["start"] + 1.0) - 0.05), 3)
                del s[key]
    return {
        "W": W, "H": H, "duration": round(total, 3), "audioDuration": round(audio_dur, 3),
        "scenes": scenes,
    }


# --------------------------------------------------------------------------- রেন্ডার
def _worker(args):
    (wid, frames, fps, scale, site_html, data_json, outdir) = args
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        exe = os.environ.get("CHROME_PATH") or None
        browser = pw.chromium.launch(
            executable_path=exe,
            args=["--no-sandbox", "--font-render-hinting=none", "--disable-gpu-vsync", "--hide-scrollbars"],
        )
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=scale)
        page = ctx.new_page()
        page.add_init_script(f"window.DATA = {data_json};")
        page.goto("file://" + site_html)
        page.wait_for_function("window.__ready === true", timeout=60000)
        done = 0
        for fr in frames:
            page.evaluate("t => window.renderFrame(t)", fr / fps)
            path = os.path.join(outdir, f"{fr:06d}.jpg")
            page.screenshot(path=path, type="jpeg", quality=94)
            done += 1
            if wid == 0 and done % 60 == 0:
                print(f"  ... {done}/{len(frames)} (worker 0)", flush=True)
        browser.close()


def render_stills(times, site_html, data_json, outdir, scale):
    from playwright.sync_api import sync_playwright

    os.makedirs(outdir, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=os.environ.get("CHROME_PATH") or None,
            args=["--no-sandbox", "--font-render-hinting=none", "--hide-scrollbars"],
        )
        page = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=scale).new_page()
        page.add_init_script(f"window.DATA = {data_json};")
        page.goto("file://" + site_html)
        page.wait_for_function("window.__ready === true", timeout=60000)
        for t in times:
            page.evaluate("t => window.renderFrame(t)", t)
            p = os.path.join(outdir, f"still_{t:07.2f}.png")
            page.screenshot(path=p, type="png")
            print("  ->", p)
        browser.close()


def mix_audio(voice, sfx_wav, bgm, duration, bgm_vol, out_wav):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", voice, "-i", sfx_wav]
    fmt = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    split = "asplit=2[v1][v2]" if bgm else "anull[v1]"
    fc = [f"[0:a]{fmt},apad,atrim=0:{duration},{split}", f"[1:a]{fmt},volume=0.5[sx]"]
    labels = "[v1][sx]"
    n = 2
    if bgm:
        cmd += ["-stream_loop", "-1", "-i", bgm]
        fc.append(
            f"[2:a]{fmt},atrim=0:{duration},afade=t=in:d=1.5,afade=t=out:st={max(0, duration - 2.5)}:d=2.5,volume={bgm_vol}[b0]"
        )
        fc.append("[b0][v2]sidechaincompress=threshold=0.03:ratio=9:attack=25:release=450[bg]")
        labels += "[bg]"
        n = 3
    fc.append(f"{labels}amix=inputs={n}:normalize=0:duration=longest,loudnorm=I=-15:TP=-1.5:LRA=11[out]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[out]", "-t", str(duration), "-ar", "44100", out_wav]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--out", default=None)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--scale", type=float, default=1.0, help="0.5 = অর্ধেক রেজোলিউশন (দ্রুত প্রিভিউ)")
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2))))
    ap.add_argument("--limit", type=float, default=None, help="শুধু প্রথম N সেকেন্ড")
    ap.add_argument("--still", type=float, action="append", help="শুধু এই সময়ের স্টিল ছবি")
    ap.add_argument("--build", default="build")
    a = ap.parse_args()

    project = os.path.abspath(a.project)
    name = os.path.basename(project.rstrip("/"))
    cfg_path = os.path.join(project, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
    audio, srt, image, bgm = discover(project, cfg)
    print(f"অডিও: {os.path.basename(audio)} | SRT: {os.path.basename(srt)} | ছবি: {os.path.basename(image)} | bgm: {os.path.basename(bgm) if bgm else '-'}")

    build = os.path.abspath(os.path.join(a.build, name))
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(build)

    D = align.audio_duration(audio)
    cues = align.parse_srt(srt)
    if not cues:
        sys.exit("SRT ফাইল থেকে কোনো লাইন পড়া যায়নি।")
    parts = align.build_phrases(cues)
    sync = cfg.get("sync", "auto")
    if sync == "srt":
        parts = align.phrases_from_srt_times(parts, cues)
        print("সিঙ্ক: SRT-এর সময় ব্যবহার হচ্ছে")
    else:
        parts, info = align.align_phrases(parts, audio, D)
        print(f"সিঙ্ক: অডিও বিরতি দেখে মেলানো হয়েছে (অডিও {D:.1f}s, SRT শেষ {cues[-1]['end']:.1f}s, বিরতি {info['pauses']}টি)")
    # ছোট হাতে-সূক্ষ্ম সমন্বয়: syncOffset সেকেন্ডে (ঋণাত্মক = ক্যাপশন আগে, ধনাত্মক = পরে)
    off = float(cfg.get("syncOffset", 0.0))
    if off:
        for p in parts:
            p["start"] = min(max(0.0, p["start"] + off), D)
            p["end"] = min(max(p["start"] + 0.05, p["end"] + off), D)
    captions = align.merge_captions(parts)

    if "scenes" not in cfg:
        cfg = default_config(cfg, cues)
    tl = build_timeline(cfg, parts, captions, D)
    tl["captions"] = [
        {"text": c["text"], "start": round(c["start"], 3), "end": round(c["end"], 3)} for c in captions
    ]
    tl["config"] = {
        k: cfg.get(k, d)
        for k, d in {
            "brand": "", "presenter": "", "productName": "", "productSubtitle": "",
            "website": "", "kicker": "", "theme": {},
        }.items()
    }
    srt_out = os.path.join(build, "captions_synced.srt")
    with open(srt_out, "w", encoding="utf-8") as f:
        for n_, c in enumerate(tl["captions"], 1):
            f.write(f"{n_}\n{align.fmt_srt(c['start'])} --> {align.fmt_srt(c['end'])}\n{c['text']}\n\n")
    prep_image(image, os.path.join(build, "product.png"))
    ext = ".png"
    tl["image"] = "product.png"
    tl["fps"] = a.fps
    json.dump(tl, open(os.path.join(build, "timeline.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    site_dir = os.path.join(build, "site")
    shutil.copytree(os.path.join(HERE, "site"), site_dir)
    shutil.copy(os.path.join(build, "product" + ext), site_dir)
    site_html = os.path.join(site_dir, "index.html")
    data_json = json.dumps(tl, ensure_ascii=False)

    if a.still:
        render_stills(a.still, site_html, data_json, os.path.join(build, "stills"), a.scale)
        return

    total = tl["duration"] if a.limit is None else min(a.limit, tl["duration"])
    nframes = int(math.ceil(total * a.fps))

    print("সাউন্ড ইফেক্ট বানানো হচ্ছে ...")
    sfx_wav = os.path.join(build, "sfx.wav")
    sfx.build_sfx(tl, sfx_wav)
    mix = os.path.join(build, "mix.wav")
    mix_audio(audio, sfx_wav, bgm, tl["duration"], float(cfg.get("bgmVolume", 0.16)), mix)

    frames_dir = os.path.join(build, "frames")
    os.makedirs(frames_dir)
    t0 = time.time()
    nw = max(1, a.workers)
    allf = list(range(nframes))
    chunks = [allf[i::nw] for i in range(nw)]
    print(f"{nframes} ফ্রেম রেন্ডার হচ্ছে ({nw}টি প্রসেস) ...")
    jobs = [(i, c, a.fps, a.scale, site_html, data_json, frames_dir) for i, c in enumerate(chunks) if c]
    with mp.get_context("spawn").Pool(len(jobs)) as pool:
        pool.map(_worker, jobs)
    print(f"ফ্রেম শেষ ({time.time() - t0:.0f}s)। ভিডিও জোড়া লাগানো হচ্ছে ...")

    out = a.out or os.path.join("out", f"{name}.mp4")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(a.fps),
         "-i", os.path.join(frames_dir, "%06d.jpg"), "-i", mix,
         "-vf", "scale=1080:1920:flags=lanczos,format=yuv420p" if a.scale != 1 else "format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-level", "4.2",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{nframes / a.fps:.3f}", out],
        check=True,
    )
    shutil.rmtree(frames_dir, ignore_errors=True)
    shutil.copy(srt_out, os.path.splitext(out)[0] + ".srt")  # অডিওর সাথে মেলানো নতুন SRT
    print(f"তৈরি: {out} ({os.path.getsize(out) / 1e6:.1f} MB)")


def prep_image(src, dst):
    """সাদা/হালকা ধূসর ব্যাকগ্রাউন্ড পুরোপুরি সাদা করে দেয়, যাতে ভিডিওর রঙিন ব্যাকগ্রাউন্ডে মিশে যায়।"""
    import numpy as np
    from PIL import Image

    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype("float32")
    h, w, _ = a.shape
    b = max(4, min(h, w) // 100)
    ring = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3), a[:, :b].reshape(-1, 3), a[:, -b:].reshape(-1, 3)])
    wp = np.percentile(ring, 8, axis=0)
    wp = np.clip(wp, 190, 255)
    out = np.clip(a * (255.0 / wp), 0, 255).astype("uint8")
    Image.fromarray(out).save(dst)


def default_config(cfg, cues):
    """config-এ scenes না থাকলে SRT-এর প্রতিটি লাইন একটা সিন হবে।"""
    sc = [{"type": "intro"}]
    for c in cues[1:-1]:
        sc.append({"type": "statement", "start": {"cue": c["i"]}, "text": c["text"]})
    if len(cues) > 1:
        sc.append({"type": "cta", "start": {"cue": cues[-1]["i"]}, "headline": "Order Now", "line": "Available on our website"})
    cfg["scenes"] = sc
    return cfg


if __name__ == "__main__":
    main()
