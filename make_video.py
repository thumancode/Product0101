#!/usr/bin/env python3
"""একটা ফোল্ডার (অডিও + SRT + ছবি + config.json) থেকে MP4 ভিডিও বানায়।

ব্যবহার:
    python make_video.py .                      # ফাইলগুলো মূল ফোল্ডারে থাকলে
    python make_video.py product02 --out out/p2.mp4
টেস্ট:
    python make_video.py . --still 2 --still 12     # শুধু কয়েকটা স্টিল ছবি
    python make_video.py . --scale 0.5 --limit 10   # ছোট প্রিভিউ
"""
import os as _os

_ASSETS = {
    'align.py': r'''"""SRT পার্স করা + অডিওর বিরতি (pause) দেখে ক্যাপশনের টাইমিং অডিওর সাথে মেলানো।

কেন দরকার: SRT-এর টাইমিং অনেক সময় আনুমানিক হয় (যেমন ৬৭ সেকেন্ড), অথচ অডিও হয়তো ৬০ সেকেন্ডের।
এখানে আমরা অডিওতে কোথায় কোথায় থেমেছে সেটা ffmpeg দিয়ে বের করি, তারপর SRT-এর লেখাগুলোকে
ছোট ছোট ফ্রেজে ভেঙে সেই বিরতিগুলোর সাথে সবচেয়ে ভালোভাবে মিলিয়ে দিই।
"""
import math
import re
import subprocess


def audio_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def parse_srt(path):
    text = open(path, encoding="utf-8-sig").read().replace("\r", "")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        for i, l in enumerate(lines):
            m = re.match(
                r"\s*(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", l
            )
            if m:
                g = [int(x) for x in m.groups()]
                s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                txt = " ".join(x.strip() for x in lines[i + 1:]).strip()
                cues.append({"i": len(cues) + 1, "start": s, "end": e, "text": txt})
                break
    return cues


def split_phrases(text, min_chars=22, max_chars=54):
    parts = [p.strip() for p in re.split(r"(?<=[।,:;!?—–])\s+", text) if p.strip()]
    chunks, cur = [], ""
    for p in parts:
        if cur and (len(cur) >= min_chars or len(cur) + 1 + len(p) > max_chars):
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        chunks.append(cur)

    def cut(c):
        if len(c) <= max_chars + 8 or " " not in c:
            return [c]
        mid = len(c) // 2
        best = min((i for i, ch in enumerate(c) if ch == " "), key=lambda i: abs(i - mid))
        return cut(c[:best].strip()) + cut(c[best:].strip())

    out = []
    for c in chunks:
        out += cut(c)
    return out


def detect_silences(audio, noise_db=-38, min_dur=0.12):
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", audio, "-af",
         f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    res = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        res.append((max(0.0, s), e))
    return res


def syllables(text):
    """বাংলা লেখার উচ্চারণ-একক (syllable) আনুমানিক গণনা। অক্ষর গোনার চেয়ে বক্তৃতার সময়ের কাছাকাছি হয়।"""
    t = re.sub(r"[^\u0980-\u09FF ]", "", text)
    n = 0
    for i, ch in enumerate(t):
        o = ord(ch)
        if 0x0985 <= o <= 0x0994 or 0x09BE <= o <= 0x09CC:
            n += 1  # স্বরবর্ণ / কার-চিহ্ন
        elif 0x0995 <= o <= 0x09B9 or o in (0x09DC, 0x09DD, 0x09DF):
            nxt = t[i + 1] if i + 1 < len(t) else " "
            if nxt == " ":
                continue  # শব্দের শেষের অন্তর্নিহিত স্বর প্রায়ই উচ্চারিত হয় না
            no = ord(nxt)
            if 0x09BE <= no <= 0x09CC or no == 0x09CD:
                continue
            n += 1
    if n == 0:  # ইংরেজি বা অন্য লেখা হলে অক্ষর দিয়ে আন্দাজ
        n = max(1, len(re.sub(r"\W", "", text)) // 3)
    return max(2, n)


def build_phrases(cues):
    """প্রতিটি যতিচিহ্নের (, । : —) জায়গায় ভেঙে ছোট ছোট অংশ বানায়।"""
    parts = []
    for c in cues:
        for p in re.split(r"(?<=[।,:;!?—–])\s+", c["text"]):
            if p.strip():
                parts.append({"text": p.strip(), "cue": c["i"], "chars": syllables(p)})
    return parts


def merge_captions(parts, min_chars=22, max_chars=54):
    """সময় বসানোর পর ছোট অংশগুলো জুড়ে পর্দায় দেখানোর মতো ক্যাপশন বানায়।"""
    caps, cur = [], None
    for p in parts:
        if cur and cur["cue"] == p["cue"] and len(cur["text"]) < min_chars and len(cur["text"]) + 1 + len(p["text"]) <= max_chars:
            cur["text"] += " " + p["text"]
            cur["end"] = p["end"]
        else:
            if cur:
                caps.append(cur)
            cur = dict(p)
    if cur:
        caps.append(cur)
    return caps


def detect_pauses(audio, thr_db=-34.0, smooth=5, min_dur=0.08):
    """ভলিউমের গ্রাফ (১০ মিলিসেকেন্ড ধাপে) দেখে বিরতি খোঁজে। ffmpeg-এর silencedetect-এর চেয়ে নির্ভরযোগ্য,
    কারণ ছোট ছোট ওঠানামা মসৃণ করে নেয়, তাই কমার ছোট বিরতিও ধরা পড়ে। ফেরত দেয় [(শুরু, শেষ), ...]।"""
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", audio, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, check=True,
    ).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    hop = 160
    n = len(x) // hop
    power = (x[: n * hop].reshape(n, hop) ** 2).mean(1)
    sm = np.convolve(power, np.ones(smooth) / smooth, mode="same")
    low = 10 * np.log10(sm + 1e-12) < thr_db
    out, i = [], 0
    while i < n:
        if low[i]:
            j = i
            while j < n and low[j]:
                j += 1
            if (j - i) * 0.01 >= min_dur or i == 0 or j == n:
                out.append((i * 0.01, j * 0.01))
            i = j
        else:
            i += 1
    return out


def _speech_edges(pauses, duration):
    """অডিওর শুরু ও শেষের নীরবতা বাদ দিয়ে আসল কথা কখন শুরু ও শেষ হয় বের করে।"""
    sp0, sp1 = 0.0, duration
    for s, e in pauses:
        if s <= 0.02:
            sp0 = e
        elif e >= duration - 0.03:
            sp1 = min(sp1, s)
    return sp0, max(sp0 + 1.0, sp1)


def _punct_kind(text):
    """ফ্রেজের শেষের যতিচিহ্ন দেখে বুঝি পরে কতটা বিরতি আশা করা যায়।"""
    t = text.rstrip().rstrip("\"'”’)")
    if t.endswith(("।", "?", "!", ".")):
        return "strong"
    if t.endswith(("—", "–", ":", ";")):
        return "mid"
    if t.endswith(","):
        return "weak"
    return "none"


def align_phrases(phrases, audio, duration):
    """প্রতিটি phrase-এর start/end বসায় (সেকেন্ডে)।

    পদ্ধতি: অডিওর প্রতিটি আসল বিরতি (pause) একটা সম্ভাব্য "কমা/দাঁড়ির জায়গা"। ডাইনামিক প্রোগ্রামিং দিয়ে
    এমনভাবে ফ্রেজ-সীমানা বসাই যাতে (১) দাঁড়ি (।) লম্বা বিরতিতে পড়ে, কমা ছোট বিরতিতে পড়ে,
    (২) প্রতিটি ফ্রেজের দৈর্ঘ্য তার উচ্চারণ-একক (syllable) সংখ্যার সাথে মানানসই থাকে।
    আগের পদ্ধতিতে ভুল বিরতিতে বসে গিয়ে ধাপে ধাপে দেরি জমত; এখানে প্রতিটি বড় বিরতি নিজে থেকে নোঙর (anchor) হয়।
    """
    import numpy as np

    P = len(phrases)
    allp = detect_pauses(audio)
    sp0, sp1 = _speech_edges(allp, duration)
    pauses = [(s, e) for s, e in allp if s > sp0 + 0.25 and e < sp1 - 0.25]

    # প্রার্থী সীমানা: আসল বিরতিগুলো + ০.১ সেকেন্ড পরপর "ফাঁকা" জায়গা (বিরতি না পেলে শেষ ভরসা)
    cand = [(s, e, e - s) for s, e in pauses]
    t = sp0 + 0.3
    while t < sp1 - 0.3:
        cand.append((t, t, 0.0))
        t += 0.1
    cand.sort(key=lambda c: (c[0], -c[2]))
    S = np.array([c[0] for c in cand])   # বিরতি শুরু (ফ্রেজ শেষ)
    E = np.array([c[1] for c in cand])   # বিরতি শেষ (পরের ফ্রেজ শুরু)
    L = np.array([c[2] for c in cand])
    C = len(cand)

    chars = np.array([p["chars"] for p in phrases], dtype=float)
    pref = np.concatenate([[0.0], np.cumsum(chars)])
    pause_total = float(sum(l for l in L if l > 0))
    rate = (sp1 - sp0 - pause_total * 0.6) / pref[-1]   # প্রতি syllable-এ গড় সেকেন্ড

    kinds = [_punct_kind(p["text"]) for p in phrases]

    def bcost(kind):
        # সীমানা-খরচ (প্রতিটি প্রার্থীর জন্য একটা ভেক্টর): বিরতি যত উপযুক্ত, খরচ তত কম
        if kind == "strong":
            c = 7.0 * np.clip(0.38 - L, 0, None) / 0.38
        elif kind == "mid":
            c = 5.0 * np.clip(0.28 - L, 0, None) / 0.28
        elif kind == "weak":
            c = 3.5 * np.clip(0.16 - L, 0, None) / 0.16
        else:
            c = 1.5 * np.clip(0.10 - L, 0, None) / 0.10
        return c - 0.8 * np.minimum(L, 0.6)   # বড় বিরতি সবসময় একটু বেশি পছন্দ

    INF = 1e12
    # dp[i][k] = i-তম ফ্রেজ k নম্বর প্রার্থীর আগে শেষ হলে সর্বনিম্ন মোট খরচ (i = 0..P-1)
    dp = np.full((P, C), INF)
    bk = np.zeros((P, C), dtype=int)

    def phrase_cost(i, start_arr, end_arr):
        dur = end_arr - start_arr
        exp = chars[i] * rate
        bad = dur <= 0.12
        c = chars[i] * (np.log((np.maximum(dur, 0.01) + 0.35) / (exp + 0.35))) ** 2
        return np.where(bad, INF, c)

    # প্রথম ফ্রেজ: শুরু = sp0
    dp[0] = phrase_cost(0, np.full(C, sp0), S) + (bcost(kinds[0]) if P > 1 else 0)
    for i in range(1, P):
        last = i == P - 1
        bc = np.zeros(C) if last else bcost(kinds[i])
        if last:
            # শেষ ফ্রেজের শেষ = sp1, শুরু = আগের সীমানার E
            pc = phrase_cost(i, E, np.full(C, sp1))
            tot = dp[i - 1] + pc
            k = int(np.argmin(tot))
            dp[i][k] = tot[k]
            bk[i][k] = k
            continue
        # M[a, b] = dp[i-1][a] + ফ্রেজ i-এর খরচ (শুরু E[a], শেষ S[b]), শুধু a < b
        dur = S[None, :] - E[:, None]
        exp = chars[i] * rate
        pcm = chars[i] * (np.log((np.maximum(dur, 0.01) + 0.35) / (exp + 0.35))) ** 2
        pcm = np.where(dur <= 0.12, INF, pcm)
        M = dp[i - 1][:, None] + pcm
        arg = np.argmin(M, axis=0)
        dp[i] = M[arg, np.arange(C)] + bc
        bk[i] = arg
    # শেষ ফ্রেজের জন্য সেরা প্রার্থী খুঁজে ব্যাকট্র্যাক
    if P == 1:
        bounds = []
    else:
        i = P - 1
        pc = phrase_cost(i, E, np.full(C, sp1))
        tot = dp[i - 1] + pc
        k = int(np.argmin(tot))
        bounds = [k]
        for i in range(P - 2, 0, -1):
            k = int(bk[i][k])
            bounds.append(k)
        bounds.reverse()   # bounds[j] = j-তম ফ্রেজের পরের সীমানা

    t0 = sp0
    for i, ph in enumerate(phrases):
        if i < len(bounds):
            k = bounds[i]
            ph["start"], ph["end"] = float(t0), float(S[k])
            t0 = float(E[k])
        else:
            ph["start"], ph["end"] = float(t0), float(sp1)
    used = sum(1 for k in bounds if L[k] > 0)
    return phrases, {"speech_start": sp0, "speech_end": sp1, "pauses": len(pauses), "used_pauses": used}


def fmt_srt(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def phrases_from_srt_times(phrases, cues):
    """sync = "srt" হলে: SRT-এর সময় যেমন আছে তেমন, cue-এর ভেতরে অক্ষরের অনুপাতে ভাগ।"""
    by_cue = {}
    for p in phrases:
        by_cue.setdefault(p["cue"], []).append(p)
    for c in cues:
        ps = by_cue.get(c["i"], [])
        tot = sum(p["chars"] for p in ps) or 1
        acc = 0
        for p in ps:
            p["start"] = c["start"] + (c["end"] - c["start"]) * acc / tot
            acc += p["chars"]
            p["end"] = c["start"] + (c["end"] - c["start"]) * acc / tot
    return phrases
''',
    'sfx.py': r'''"""সাউন্ড ইফেক্ট কোড দিয়ে বানানো হয় (কোনো ডাউনলোড বা কপিরাইট ঝামেলা নেই)।
hook, whoosh, pop, chime, impact, riser, click, shimmer: সব numpy দিয়ে সিন্থেসাইজ।
"""
import numpy as np
from scipy.signal import butter, lfilter
import wave

SR = 44100
RNG = np.random.default_rng(7)


def _t(d):
    return np.arange(int(d * SR)) / SR


def _norm(x, peak=0.9):
    m = np.max(np.abs(x)) or 1.0
    return x / m * peak


def _fade(x, a=0.004, b=0.01):
    n = len(x)
    na, nb = int(a * SR), int(b * SR)
    x = x.copy()
    if na:
        x[:na] *= np.linspace(0, 1, na)
    if nb:
        x[-nb:] *= np.linspace(1, 0, nb)
    return x


def _bp_sweep(noise, f0, f1, q=1.2, block=512):
    """কেন্দ্র-ফ্রিকোয়েন্সি f0 থেকে f1 পর্যন্ত সরতে থাকা ব্যান্ডপাস ফিল্টার।"""
    out = np.zeros_like(noise)
    zi = None
    n = len(noise)
    for i in range(0, n, block):
        fc = f0 * (f1 / f0) ** (i / max(1, n))
        lo, hi = max(40, fc / (1 + 1 / q)), min(SR / 2 - 100, fc * (1 + 1 / q))
        b, a = butter(2, [lo / (SR / 2), hi / (SR / 2)], btype="band")
        if zi is None:
            zi = np.zeros(max(len(a), len(b)) - 1)
        else:
            zi = np.zeros(max(len(a), len(b)) - 1) if len(zi) != max(len(a), len(b)) - 1 else zi
        seg, zi = lfilter(b, a, noise[i:i + block], zi=zi)
        out[i:i + block] = seg
    return out


def whoosh(d=0.75, up=True):
    t = _t(d)
    noise = RNG.standard_normal(len(t))
    f0, f1 = (300, 5000) if up else (5000, 300)
    x = _bp_sweep(noise, f0, f1, q=1.1)
    env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2.2
    return _fade(_norm(x * env))


def impact(d=1.6):
    t = _t(d)
    f = 45 + 90 * np.exp(-t * 14)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 3.2)
    noise = lfilter(*butter(2, 1800 / (SR / 2)), RNG.standard_normal(len(t))) * np.exp(-t * 18)
    return _fade(_norm(body + 0.55 * noise))


def bell(freq=880, d=1.4, bright=1.0):
    t = _t(d)
    parts = [(1, 1.0, 3.0), (2.0, 0.45, 4.0), (2.76, 0.35, 5.0), (4.07, 0.18 * bright, 7.0), (5.4, 0.1 * bright, 9.0)]
    x = sum(a * np.sin(2 * np.pi * freq * m * t) * np.exp(-t * dec) for m, a, dec in parts)
    return _fade(_norm(x), 0.002, 0.02)


def pop(freq=520, d=0.16):
    t = _t(d)
    f = freq * (1 + 1.4 * np.exp(-t * 55))
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(ph) * np.exp(-t * 26)
    return _fade(_norm(x), 0.001, 0.01)


def click(d=0.05):
    t = _t(d)
    n = RNG.standard_normal(len(t))
    b, a = butter(2, 2500 / (SR / 2), btype="high")
    return _norm(lfilter(b, a, n) * np.exp(-t * 90))


def riser(d=2.0):
    t = _t(d)
    noise = RNG.standard_normal(len(t))
    x = _bp_sweep(noise, 250, 7000, q=0.9)
    tone = np.sin(2 * np.pi * np.cumsum(180 * (1 + 5 * (t / d) ** 2)) / SR) * 0.35
    env = (t / d) ** 1.8
    return _fade(_norm((x + tone) * env), 0.01, 0.03)


def shimmer(d=1.8, base=1568):
    t = _t(d)
    out = np.zeros(len(t))
    notes = [1, 1.25, 1.5, 2, 2.5, 3]
    for i, m in enumerate(notes):
        st = i * 0.09
        mask = (t >= st)
        tt = np.where(mask, t - st, 0)
        out += mask * np.sin(2 * np.pi * base * m * tt) * np.exp(-tt * 4.2) * (0.8 ** i)
    return _fade(_norm(out), 0.002, 0.05)


def hook(d=2.0):
    """ভিডিওর শুরুর hook: নিচু বুম + ঝিলমিল।"""
    n = int(d * SR)
    out = np.zeros(n)
    for sig, at, g in ((impact(1.6), 0.0, 0.75), (shimmer(1.8), 0.12, 0.35), (whoosh(0.8, up=False), 0.0, 0.4)):
        i = int(at * SR)
        seg = sig[: n - i] * g
        out[i:i + len(seg)] += seg
    return _norm(out, 0.9)


def chime_arp(base=659.3, steps=(1, 1.25, 1.5, 2.0), gap=0.11, d=2.2):
    n = int(d * SR)
    out = np.zeros(n)
    for i, m in enumerate(steps):
        b = bell(base * m, 1.6)
        s = int(i * gap * SR)
        seg = b[: n - s] * (0.9 ** i)
        out[s:s + len(seg)] += seg
    return _norm(out, 0.9)


class Track:
    def __init__(self, duration):
        self.n = int(duration * SR)
        self.L = np.zeros(self.n)
        self.R = np.zeros(self.n)

    def add(self, sig, t, gain=0.5, pan=0.0):
        i = int(max(0, t) * SR)
        if i >= self.n:
            return
        seg = sig[: self.n - i] * gain
        gl = np.cos((pan + 1) * np.pi / 4)
        gr = np.sin((pan + 1) * np.pi / 4)
        self.L[i:i + len(seg)] += seg * gl * 1.414
        self.R[i:i + len(seg)] += seg * gr * 1.414

    def save(self, path):
        data = np.stack([self.L, self.R], axis=1)
        peak = np.max(np.abs(data)) or 1
        if peak > 0.95:
            data = data / peak * 0.95
        pcm = (data * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())


PENTA = [1, 9 / 8, 5 / 4, 3 / 2, 5 / 3, 2]


def build_sfx(timeline, path):
    """timeline-এর সিন ও আইটেম দেখে সাউন্ড বসায়।"""
    tr = Track(timeline["duration"])
    W = {"whoosh": whoosh(0.75), "whoosh_down": whoosh(0.6, up=False), "impact": impact(),
         "riser": riser(2.2), "shimmer": shimmer(), "click": click()}
    tr.add(hook(), 0.0, 0.55)
    scenes = timeline["scenes"]
    for si, sc in enumerate(scenes):
        st, typ = sc["start"], sc["type"]
        if si > 0:
            tr.add(W["whoosh"], max(0, st - 0.35), 0.30)
        if typ == "title":
            rv = sc.get("times", {}).get("reveal", st + 1.0)
            tr.add(W["impact"], rv + 0.02, 0.40)
            tr.add(W["shimmer"], rv + 0.15, 0.22)
        elif typ == "blend":
            tr.add(W["riser"], max(0, st - 0.05), 0.30)
            tr.add(bell(523.3, 2.0), sc.get("times", {}).get("accords", st + 2.15), 0.28)
        elif typ == "cta":
            tr.add(chime_arp(), st + 0.35, 0.40)
            tr.add(W["click"], st + 1.65, 0.45)
            tr.add(bell(1318.5, 1.6), st + 1.7, 0.2)
        elif typ == "intro":
            pass
        for k, it in enumerate(sc.get("items", [])):
            pan = -0.35 if k % 2 == 0 else 0.35
            if typ in ("notes", "traits", "occasions"):
                tr.add(pop(420 * PENTA[k % len(PENTA)]), it["t"], 0.42, pan)
            elif typ == "family":
                tr.add(bell(523.3 * PENTA[k % len(PENTA)], 1.2), it["t"], 0.30, (k - 2) * 0.25)
            elif typ == "character":
                tr.add(W["impact"][: int(0.7 * SR)] * np.linspace(1, 0, int(0.7 * SR)), it["t"], 0.16)
                tr.add(shimmer(1.2, 1318), it["t"] + 0.05, 0.10)
    tr.add(bell(1046.5, 2.2), timeline["duration"] - 1.5, 0.18)
    tr.save(path)
''',
    'site/index.html': r'''<!doctype html>
<html lang="bn">
<head>
<meta charset="utf-8">
<title>frame</title>
<!-- GitHub Actions-এ ইন্টারনেট আছে, তাই সুন্দর ফন্ট লোড হবে। না পেলে সিস্টেম ফন্টে চলবে। -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;0,700;1,500;1,600&family=Jost:wght@400;500;600&family=Noto+Sans+Bengali:wght@400;600;700&display=block" rel="stylesheet">
<link rel="stylesheet" href="style.css">
</head>
<body>
<div id="stage"></div>
<script src="stage.js"></script>
</body>
</html>
''',
    'site/style.css': r''':root {
  --gold: #b98a3e;
  --gold-light: #e6cc8f;
  --gold-dark: #8a6224;
  --ink: #2a1c10;
  --ivory: #f4ecdd;
  --emerald: #1f4d46;
  --serif: "Cormorant Garamond", "Noto Serif", "FreeSerif", serif;
  --sans: "Jost", "Noto Sans", "FreeSans", sans-serif;
  --bn: "Noto Sans Bengali", "Hind Siliguri", "Lohit Bengali", "FreeSans", sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 1080px; height: 1920px; overflow: hidden; background: #efe4cf; }
#stage {
  position: relative; width: 1080px; height: 1920px; overflow: hidden;
  background: radial-gradient(ellipse at 50% 40%, #fbf6ec 0%, #f2e8d4 55%, #e2d1ae 100%);
  color: var(--ink); font-family: var(--sans);
}
.layer { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; }
.abs { position: absolute; }
.center { left: 0; width: 1080px; text-align: center; }

/* ছবি */
#bottle { position: absolute; left: 0; top: 0; width: 1000px; height: 1000px; mix-blend-mode: multiply; transform-origin: 50% 50%; will-change: transform;
  -webkit-mask-image: radial-gradient(circle closest-side, #000 84%, transparent 100%); mask-image: radial-gradient(circle closest-side, #000 84%, transparent 100%); }
#bottle img { width: 1000px; height: 1000px; display: block; }

/* টেক্সট */
.serif { font-family: var(--serif); font-weight: 700; }
.sans { font-family: var(--sans); font-weight: 500; }
.bn { font-family: var(--bn); }
.gold { color: var(--gold-dark); }
.upper { text-transform: uppercase; }
.nowrap { white-space: nowrap; }

.chip {
  position: absolute; display: flex; align-items: center; gap: 16px;
  padding: 14px 30px 14px 20px; border-radius: 60px; white-space: nowrap;
  background: rgba(255, 252, 245, 0.82); border: 2px solid rgba(185, 138, 62, 0.85);
  box-shadow: 0 10px 28px rgba(80, 50, 10, 0.16), inset 0 0 0 4px rgba(255, 255, 255, 0.5);
  font-family: var(--sans); font-weight: 600; font-size: 38px; letter-spacing: 1px; color: var(--ink);
}
.chip .dot { width: 34px; height: 34px; border-radius: 50%; flex: none; box-shadow: inset 0 -4px 8px rgba(0,0,0,0.22), inset 0 3px 6px rgba(255,255,255,0.55), 0 0 0 3px rgba(255,255,255,0.8), 0 0 0 4.5px rgba(185,138,62,0.55); }

.cap {
  position: absolute; left: 60px; width: 960px; top: 1380px; height: 200px;
  display: flex; align-items: center; justify-content: center; text-align: center;
  font-family: var(--bn); font-weight: 700; font-size: 56px; line-height: 1.5; color: var(--ink);
  text-shadow: 0 2px 0 rgba(255, 255, 255, 0.85), 0 0 18px rgba(255, 250, 238, 0.95), 0 0 34px rgba(255, 250, 238, 0.9);
}
''',
    'site/stage.js': r'''/* মোশন গ্রাফিক্স স্টেজ: সবকিছু শুধু সময় t থেকে হিসাব হয় (window.renderFrame(t)),
   তাই প্রতিটি ফ্রেম ঠিকঠাক একই রকম আসে। কোনো ইমোজি নেই — সব ভেক্টর (SVG/CSS)। */
(function () {
  const D = window.DATA;
  const CFG = D.config || {};
  const W = 1080, H = 1920;
  const NS = "http://www.w3.org/2000/svg";

  // ---------- ছোট সহায়ক ----------
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const E = {
    lin: (x) => x,
    outCubic: (x) => 1 - Math.pow(1 - x, 3),
    outQuart: (x) => 1 - Math.pow(1 - x, 4),
    inCubic: (x) => x * x * x,
    inOutCubic: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
    inOutSine: (x) => -(Math.cos(Math.PI * x) - 1) / 2,
    outBack: (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
    outExpo: (x) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x)),
  };
  const prog = (t, s, d, e = E.outCubic) => e(clamp((t - s) / d));
  function el(tag, cls, parent, css) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (css) Object.assign(e.style, css);
    if (parent) parent.appendChild(e);
    return e;
  }
  function sv(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function svgRoot(parent, w = W, h = H, css) {
    const s = sv("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}` }, parent);
    s.style.position = "absolute"; s.style.left = "0"; s.style.top = "0"; s.style.overflow = "visible";
    if (css) Object.assign(s.style, css);
    return s;
  }
  function rng(seed) {
    let a = seed >>> 0;
    return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  const css = (n, o) => Object.assign(n.style, o);
  const GOLD = "#b98a3e", GOLD_L = "#e6cc8f", GOLD_D = "#8a6224", INK = "#2a1c10";
  // থিম রং বদলানো যায়
  const TH = CFG.theme || {};
  if (TH.gold) document.documentElement.style.setProperty("--gold", TH.gold);

  function setT(n, dx, dy, s = 1, rot = 0) {
    n.style.transform = `translate(${dx.toFixed(2)}px, ${dy.toFixed(2)}px) scale(${s.toFixed(4)}) rotate(${rot.toFixed(3)}deg)`;
  }
  function appear(n, p, dx = 0, dy = 24, s0 = 1) {
    n.style.opacity = clamp(p * 1.5).toFixed(3);
    setT(n, dx * (1 - p), dy * (1 - p), lerp(s0, 1, p));
  }

  // 8-কোণা তারা (রুব আল-হিজব)
  function starPath(cx, cy, R, r) {
    let d = "";
    for (let i = 0; i < 16; i++) {
      const a = (Math.PI / 8) * i - Math.PI / 2;
      const rad = i % 2 === 0 ? R : r;
      d += (i ? "L" : "M") + (cx + rad * Math.cos(a)).toFixed(2) + " " + (cy + rad * Math.sin(a)).toFixed(2);
    }
    return d + "Z";
  }
  function sparkPath(cx, cy, R) {
    const r = R * 0.18;
    return `M${cx} ${cy - R} Q${cx + r} ${cy - r} ${cx + R} ${cy} Q${cx + r} ${cy + r} ${cx} ${cy + R} Q${cx - r} ${cy + r} ${cx - R} ${cy} Q${cx - r} ${cy - r} ${cx} ${cy - R}Z`;
  }

  // ---------- স্তর ----------
  const stage = document.getElementById("stage");
  const bgL = el("div", "layer", stage);
  const fx = el("canvas", "layer", stage);
  fx.width = W; fx.height = H;
  const backL = el("div", "layer", stage);
  const bottle = el("div", "", stage); bottle.id = "bottle";
  const bImg = el("img", "", bottle); bImg.src = D.image;
  const frontL = el("div", "layer", stage);
  const capL = el("div", "layer", stage);
  const chromeL = el("div", "layer", stage);

  // ---------- পটভূমি: জ্যামিতিক নকশা + আলো ----------
  const bgSvg = svgRoot(bgL);
  const defs = sv("defs", {}, bgSvg);
  const pat = sv("pattern", { id: "geo", width: 120, height: 120, patternUnits: "userSpaceOnUse" }, defs);
  sv("path", { d: starPath(60, 60, 44, 31), fill: "none", stroke: GOLD, "stroke-width": 1.6, opacity: 0.55 }, pat);
  sv("rect", { x: 36, y: 36, width: 48, height: 48, fill: "none", stroke: GOLD, "stroke-width": 1.2, opacity: 0.35, transform: "rotate(45 60 60)" }, pat);
  sv("path", { d: "M0 0L26 26M120 0L94 26M0 120L26 94M120 120L94 94", stroke: GOLD, "stroke-width": 1.2, opacity: 0.4 }, pat);
  sv("circle", { cx: 60, cy: 60, r: 4, fill: GOLD, opacity: 0.5 }, pat);
  const patRect = sv("rect", { x: -120, y: -120, width: W + 240, height: H + 240, fill: "url(#geo)", opacity: 0.2 }, bgSvg);
  const vign = el("div", "layer", bgL, { background: "radial-gradient(ellipse at 50% 45%, rgba(255,255,255,0) 40%, rgba(120,84,30,0.20) 100%)" });
  const glow = el("div", "abs", bgL, { width: "1100px", height: "1100px", borderRadius: "50%", background: "radial-gradient(circle, rgba(226,184,100,0.55) 0%, rgba(226,184,100,0.18) 40%, rgba(226,184,100,0) 68%)" });

  // কোণার নকশা + ফ্রেম
  const frame = svgRoot(chromeL);
  const fr1 = sv("rect", { x: 30, y: 30, width: W - 60, height: H - 60, rx: 6, fill: "none", stroke: GOLD, "stroke-width": 2.5, opacity: 0.8 }, frame);
  const fr2 = sv("rect", { x: 46, y: 46, width: W - 92, height: H - 92, rx: 3, fill: "none", stroke: GOLD, "stroke-width": 1, opacity: 0.55 }, frame);
  const corners = [[30, 30], [W - 30, 30], [30, H - 30], [W - 30, H - 30]].map(([x, y]) => {
    const g = sv("g", {}, frame);
    sv("path", { d: starPath(x, y, 24, 12), fill: "#f4ecdd", stroke: GOLD, "stroke-width": 2.5 }, g);
    sv("circle", { cx: x, cy: y, r: 4.5, fill: GOLD }, g);
    return g;
  });
  const perim = 2 * (W - 60 + H - 60);
  fr1.setAttribute("stroke-dasharray", perim); fr2.setAttribute("stroke-dasharray", perim);

  // উপরে প্রগ্রেস বার
  const progBar = el("div", "abs", chromeL, { left: "0px", top: "0px", height: "7px", width: "0px", background: `linear-gradient(90deg, ${GOLD_L}, ${GOLD}, ${GOLD_D})` });

  // নিচের ব্র্যান্ড
  const brandBar = el("div", "abs", chromeL, { left: "0px", top: "1668px", width: W + "px", textAlign: "center" });
  const bRow = el("div", "", brandBar, { display: "flex", alignItems: "center", justifyContent: "center", gap: "22px" });
  const bl1 = el("div", "", bRow, { height: "2px", width: "110px", background: `linear-gradient(90deg, transparent, ${GOLD})` });
  const bTxt = el("div", "serif upper nowrap", bRow, { fontSize: "40px", letterSpacing: "9px", color: GOLD_D });
  bTxt.textContent = CFG.brand || "";
  const bl2 = el("div", "", bRow, { height: "2px", width: "110px", background: `linear-gradient(270deg, transparent, ${GOLD})` });
  const bSite = el("div", "sans nowrap", brandBar, { marginTop: "10px", fontSize: "28px", letterSpacing: "4px", color: INK, opacity: 0.75 });
  bSite.textContent = CFG.website || "";
  if (!CFG.brand) bRow.style.display = "none";

  // ---------- কণা (সোনালি ধুলো) ----------
  const fctx = fx.getContext("2d");
  const pr = rng(11);
  const parts = Array.from({ length: 48 }, () => ({ x: pr() * W, y: pr() * (H + 200), sp: 14 + pr() * 34, r: 2 + pr() * 5, ph: pr() * 6.28, tw: 0.8 + pr() * 1.8 }));
  function drawParticles(t) {
    fctx.clearRect(0, 0, W, H);
    for (const p of parts) {
      const y = (((p.y - p.sp * t) % (H + 200)) + (H + 200)) % (H + 200) - 100;
      const x = p.x + 28 * Math.sin(t * 0.55 + p.ph);
      const a = 0.25 + 0.55 * (0.5 + 0.5 * Math.sin(t * p.tw * 2 + p.ph));
      fctx.save();
      fctx.translate(x, y);
      fctx.globalAlpha = a * 0.8;
      fctx.fillStyle = p.r > 4 ? "#d7a84b" : "#c99533";
      const R = p.r * 1.6;
      fctx.beginPath();
      fctx.moveTo(0, -R); fctx.quadraticCurveTo(R * 0.16, -R * 0.16, R, 0); fctx.quadraticCurveTo(R * 0.16, R * 0.16, 0, R); fctx.quadraticCurveTo(-R * 0.16, R * 0.16, -R, 0); fctx.quadraticCurveTo(-R * 0.16, -R * 0.16, 0, -R);
      fctx.fill();
      fctx.restore();
    }
  }

  // ---------- বোতলের অবস্থা (সিন অনুযায়ী) ----------
  const BS = {
    intro: { x: 540, y: 850, s: 1.0 },
    title: { x: 540, y: 1050, s: 0.78 },
    blend: { x: 540, y: 905, s: 0.72 },
    traits: { x: 540, y: 905, s: 0.86 },
    notes: { x: 540, y: 905, s: 0.86 },
    family: { x: 540, y: 905, s: 0.5 },
    character: { x: 540, y: 1170, s: 0.5 },
    occasions: { x: 855, y: 800, s: 0.6 },
    cta: { x: 540, y: 835, s: 0.8 },
    statement: { x: 540, y: 1000, s: 0.7 },
  };
  const scenes = D.scenes;
  const states = scenes.map((s) => Object.assign({ x: 540, y: 900, s: 0.78 }, BS[s.type] || {}));
  let bState = { x: 540, y: 900, s: 1, r: 0, o: 1 };
  function bottleAt(t) {
    let i = 0;
    for (let k = 0; k < scenes.length; k++) if (t >= scenes[k].start) i = k;
    const cur = states[i];
    const prev = states[Math.max(0, i - 1)];
    const k = i === 0 ? 1 : E.inOutCubic(clamp((t - scenes[i].start) / 1.0));
    let x = lerp(prev.x, cur.x, k), y = lerp(prev.y, cur.y, k), s = lerp(prev.s, cur.s, k);
    // শুরুর প্রবেশ
    const e = prog(t, 0.05, 1.5, E.outExpo);
    y += (1 - e) * 520; s *= lerp(0.72, 1, e);
    let o = clamp(t / 0.5);
    // ভাসমান ভাব
    y += Math.sin(t * 1.5) * 9;
    const r = Math.sin(t * 0.9) * 0.9 + (1 - e) * -8;
    return { x, y, s, r, o };
  }

  // ---------- সিন তৈরির ফাংশন ----------
  const B = {};

  function header(front, sc, opts = {}) {
    const y = opts.y || 215;
    const wrap = el("div", "abs center", front, { top: y + "px" });
    const t1 = el("div", "serif upper nowrap", wrap, { fontSize: (opts.size || 96) + "px", letterSpacing: "10px", lineHeight: "1.05", color: INK });
    t1.textContent = sc.heading || "";
    const rule = el("div", "", wrap, { margin: "14px auto 0", width: "420px", height: "22px", position: "relative" });
    const rs = svgRoot(rule, 420, 22);
    const l1 = sv("line", { x1: 210, y1: 11, x2: 20, y2: 11, stroke: GOLD, "stroke-width": 2 }, rs);
    const l2 = sv("line", { x1: 210, y1: 11, x2: 400, y2: 11, stroke: GOLD, "stroke-width": 2 }, rs);
    sv("path", { d: starPath(210, 11, 11, 5.5), fill: GOLD }, rs);
    const t2 = el("div", "bn nowrap", wrap, { fontSize: "38px", fontWeight: 600, color: GOLD_D, marginTop: "8px", letterSpacing: "1px" });
    t2.textContent = sc.bn || "";
    // ফিট
    const mw = 960;
    if (t1.scrollWidth > mw) t1.style.fontSize = Math.floor((opts.size || 96) * mw / t1.scrollWidth) + "px";
    return (t, s) => {
      const p = prog(t, s + 0.15, 0.7);
      css(t1, { opacity: clamp(p * 1.6), transform: `translateY(${(1 - p) * 30}px)`, filter: `blur(${(1 - p) * 8}px)` });
      const q = prog(t, s + 0.45, 0.7);
      l1.setAttribute("stroke-dasharray", 200); l1.setAttribute("stroke-dashoffset", (1 - q) * 200);
      l2.setAttribute("stroke-dasharray", 200); l2.setAttribute("stroke-dashoffset", (1 - q) * 200);
      rule.style.opacity = q;
      const r = prog(t, s + 0.6, 0.6);
      css(t2, { opacity: clamp(r * 1.5), transform: `translateY(${(1 - r) * 16}px)` });
    };
  }

  function pyramid(front, level, x, y) {
    const g = svgRoot(front, 150, 130, { left: x - 75 + "px", top: y + "px" });
    const tiers = {
      top: "M75 6 L50 44 L100 44 Z",
      heart: "M46 50 L104 50 L122 88 L28 88 Z",
      base: "M24 94 L126 94 L148 128 L2 128 Z",
    };
    const els = {};
    for (const k in tiers) {
      const on = k === level;
      els[k] = sv("path", { d: tiers[k], fill: on ? GOLD : "none", stroke: GOLD, "stroke-width": 2.5, "stroke-linejoin": "round", opacity: on ? 1 : 0.5 }, g);
    }
    return (t, s) => {
      const p = prog(t, s + 0.1, 0.6, E.outBack);
      g.style.opacity = clamp(p * 2);
      g.style.transform = `scale(${lerp(0.7, 1, p)})`;
      els[level].setAttribute("opacity", 0.75 + 0.25 * Math.sin(t * 5));
    };
  }

  function orbitChips(front, back, sc, kind) {
    const items = sc.items || [];
    const n = items.length;
    const gap = n <= 4 ? 310 : 262;
    const y0 = n <= 4 ? 640 : 600;
    const halfW = 0.155 * 1000 * states[scenes.indexOf(sc)].s;
    const lineSvg = svgRoot(back);
    const nodes = items.map((it, k) => {
      const left = k % 2 === 0;
      const row = Math.floor(k / 2);
      const y = y0 + row * gap + (left ? 0 : gap / 2);
      const chip = el("div", "chip", front, { top: y + "px" });
      if (left) chip.style.right = W - 372 + "px"; else chip.style.left = 708 + "px";
      chip.style.transformOrigin = left ? "100% 50%" : "0% 50%";
      let dot;
      if (kind === "traits") {
        dot = el("div", "", chip, { width: "34px", height: "34px", flex: "none" });
        const ds = svgRoot(dot, 34, 34);
        sv("path", { d: starPath(17, 17, 16, 8), fill: GOLD }, ds);
        sv("circle", { cx: 17, cy: 17, r: 4, fill: "#fbf3e0" }, ds);
      } else {
        dot = el("div", "dot", chip);
        dot.style.background = `radial-gradient(circle at 35% 30%, #ffffffaa 0%, ${it.color || GOLD} 55%)`;
        dot.style.backgroundColor = it.color || GOLD;
      }
      const lab = el("span", "", chip); lab.textContent = it.label;
      const w = chip.offsetWidth;
      const fit = w > 336 ? 336 / w : 1;
      const ex = left ? 372 : 708;
      const bx = left ? W / 2 - halfW : W / 2 + halfW;
      const line = sv("line", { x1: ex, y1: y + 36, x2: bx, y2: y + 36, stroke: GOLD, "stroke-width": 2.5 }, lineSvg);
      const node = sv("circle", { cx: bx, cy: y + 36, r: 8, fill: GOLD }, lineSvg);
      const ring = sv("circle", { cx: ex, cy: y + 36, r: 10, fill: "none", stroke: GOLD, "stroke-width": 2.5 }, lineSvg);
      return { chip, left, fit, line, node, ring, ex, bx, y, it };
    });
    return (t) => {
      nodes.forEach((n) => {
        const p = prog(t, n.it.t, 0.65, E.outBack);
        const dir = n.left ? -1 : 1;
        n.chip.style.opacity = clamp(p * 2).toFixed(3);
        n.chip.style.transform = `translate(${dir * 70 * (1 - p)}px, 0) scale(${n.fit * lerp(0.85, 1, clamp(p, 0, 1.2))})`;
        const q = prog(t, n.it.t + 0.12, 0.5);
        const len = Math.abs(n.bx - n.ex);
        n.line.setAttribute("stroke-dasharray", len);
        n.line.setAttribute("stroke-dashoffset", (1 - q) * len);
        n.line.style.opacity = q > 0 ? 0.9 : 0;
        n.node.setAttribute("r", 8 * clamp(q * 1.2));
        n.node.style.opacity = q;
        const rp = clamp((t - n.it.t) / 0.7);
        n.ring.setAttribute("r", 10 + 34 * E.outCubic(rp));
        n.ring.style.opacity = t < n.it.t ? 0 : (1 - rp) * 0.8;
      });
    };
  }

  B.intro = (sc, L) => {
    // রশ্মি
    const rays = el("div", "abs", L.b, { width: "1500px", height: "1500px", left: (540 - 750) + "px", top: (850 - 750) + "px", borderRadius: "50%",
      background: "repeating-conic-gradient(from 0deg, rgba(200,150,60,0.30) 0deg 5deg, rgba(200,150,60,0) 5deg 12deg)",
      WebkitMaskImage: "radial-gradient(circle, #000 12%, rgba(0,0,0,0.55) 40%, transparent 68%)", maskImage: "radial-gradient(circle, #000 12%, rgba(0,0,0,0.55) 40%, transparent 68%)" });
    const rs = svgRoot(L.b);
    const rings = [0, 1, 2].map(() => sv("circle", { cx: 540, cy: 850, r: 100, fill: "none", stroke: GOLD, "stroke-width": 3 }, rs));
    // ওয়েলকাম + প্রেজেন্টার
    const wrap = el("div", "abs center", L.f, { top: "170px" });
    const w1 = el("div", "sans upper nowrap", wrap, { fontSize: "30px", letterSpacing: "14px", color: GOLD_D });
    w1.textContent = "Welcome to";
    const w2 = el("div", "serif upper nowrap", wrap, { fontSize: "104px", letterSpacing: "8px", lineHeight: "1.05", marginTop: "6px", color: INK });
    w2.textContent = CFG.presenter || "";
    if (w2.scrollWidth > 960) w2.style.fontSize = Math.floor(104 * 960 / w2.scrollWidth) + "px";
    const ln = el("div", "", wrap, { margin: "16px auto 0", width: "320px", height: "22px" });
    const ls = svgRoot(ln, 320, 22);
    const a = sv("line", { x1: 160, y1: 11, x2: 10, y2: 11, stroke: GOLD, "stroke-width": 2 }, ls);
    const b = sv("line", { x1: 160, y1: 11, x2: 310, y2: 11, stroke: GOLD, "stroke-width": 2 }, ls);
    sv("path", { d: starPath(160, 11, 11, 5.5), fill: GOLD }, ls);
    return (t) => {
      const lt = t - sc.start;
      rays.style.opacity = prog(lt, 0.2, 1.4) * 0.9;
      rays.style.transform = `rotate(${lt * 6}deg)`;
      rings.forEach((c, i) => {
        const p = ((lt - 0.2 - i * 0.55) % 1.65 + 1.65) % 1.65 / 1.65;
        const on = lt > 0.2 + i * 0.55;
        c.setAttribute("r", 120 + 520 * E.outCubic(p));
        c.style.opacity = on ? (1 - p) * 0.7 : 0;
      });
      const p1 = prog(lt, 0.25, 0.8);
      appear(w1, p1, 0, 18);
      const p2 = prog(lt, 0.45, 0.9);
      css(w2, { opacity: clamp(p2 * 1.5), transform: `translateY(${(1 - p2) * 30}px)`, filter: `blur(${(1 - p2) * 10}px)` });
      const q = prog(lt, 0.9, 0.8);
      [a, b].forEach((l) => { l.setAttribute("stroke-dasharray", 150); l.setAttribute("stroke-dashoffset", (1 - q) * 150); });
    };
  };

  function arch(back, cx, cy, sc = 1) {
    const g = svgRoot(back);
    const d = "M-300 340 V-60 C-300 -200 -150 -290 0 -370 C150 -290 300 -200 300 -60 V340 Z";
    const grp = sv("g", { transform: `translate(${cx} ${cy}) scale(${sc})` }, g);
    const fill = sv("path", { d, fill: "rgba(200,150,60,0.10)" }, grp);
    const s1 = sv("path", { d, fill: "none", stroke: GOLD, "stroke-width": 4, "stroke-linejoin": "round" }, grp);
    const s2 = sv("path", { d, fill: "none", stroke: GOLD, "stroke-width": 1.6, transform: "scale(0.9)" }, grp);
    s1.setAttribute("stroke-dasharray", 2600); s2.setAttribute("stroke-dasharray", 2600);
    return (p) => {
      fill.style.opacity = p;
      s1.setAttribute("stroke-dashoffset", (1 - p) * 2600);
      s2.setAttribute("stroke-dashoffset", (1 - p) * 2600);
    };
  }

  function splitLetters(parent, text, css0) {
    const wrap = el("div", "nowrap", parent, Object.assign({ display: "inline-block" }, css0 || {}));
    const spans = [...text].map((ch) => { const s = el("span", "", wrap, { display: "inline-block", whiteSpace: "pre" }); s.textContent = ch; return s; });
    return { wrap, spans };
  }

  B.title = (sc, L) => {
    const drawArch = arch(L.b, 540, 1010, 1.05);
    // কিকার (ব্র্যান্ড)
    const kick = el("div", "abs center", L.f, { top: "250px" });
    const k1 = el("div", "serif upper nowrap", kick, { fontSize: "82px", letterSpacing: "10px", color: INK, lineHeight: "1.05" });
    k1.textContent = CFG.brand || "";
    if (k1.scrollWidth > 960) k1.style.fontSize = Math.floor(82 * 960 / k1.scrollWidth) + "px";
    const k2 = el("div", "sans upper nowrap", kick, { fontSize: "28px", letterSpacing: "12px", color: GOLD_D, marginTop: "16px" });
    k2.textContent = "presents a premium creation";
    // বড় নাম
    const words = (CFG.productName || "").toUpperCase().split(" ");
    const line1 = words.slice(0, 1).join(" "), line2 = words.slice(1).join(" ");
    const tw = el("div", "abs center", L.f, { top: "290px" });
    const l1 = splitLetters(tw, line1, { fontFamily: "var(--serif)", fontWeight: 700, fontSize: "132px", letterSpacing: "8px", lineHeight: "1.0", color: INK, display: "block", textAlign: "center" });
    const l2 = splitLetters(tw, line2, { fontFamily: "var(--serif)", fontWeight: 600, fontSize: "84px", letterSpacing: "14px", lineHeight: "1.1", color: GOLD_D, display: "block", textAlign: "center", marginTop: "-4px" });
    [l1, l2].forEach((l, i) => { const mw = 980; const w = l.wrap.scrollWidth; if (w > mw) l.wrap.style.transform = ""; l._fit = w > mw ? mw / w : 1; });
    const sub = el("div", "abs center", L.f, { top: "590px" });
    const sRule = el("div", "", sub, { margin: "0 auto", width: "380px", height: "22px" });
    const srs = svgRoot(sRule, 380, 22);
    const la = sv("line", { x1: 190, y1: 11, x2: 10, y2: 11, stroke: GOLD, "stroke-width": 2 }, srs);
    const lb = sv("line", { x1: 190, y1: 11, x2: 370, y2: 11, stroke: GOLD, "stroke-width": 2 }, srs);
    sv("path", { d: starPath(190, 11, 11, 5.5), fill: GOLD }, srs);
    const sT = el("div", "sans upper nowrap", sub, { fontSize: "40px", letterSpacing: "16px", color: INK, marginTop: "12px", paddingLeft: "16px" });
    sT.textContent = CFG.productSubtitle || "";
    return (t) => {
      const lt = t - sc.start;
      const rv = sc.times.reveal;
      drawArch(prog(lt, 0.2, 1.8, E.inOutCubic));
      const pk = prog(lt, 0.3, 0.9);
      const move = E.inOutCubic(clamp((t - rv) / 0.9));
      css(kick, { opacity: clamp(pk * 1.5) * (1 - 0.0), top: lerp(430, 150, move) + "px", transform: `scale(${lerp(1.0, 0.6, move)}) translateY(${(1 - pk) * 20}px)` });
      k2.style.opacity = 1 - move;
      tw.style.top = "290px";
      [l1, l2].forEach((l, li) => {
        l.spans.forEach((s, i) => {
          const p = prog(t, rv + 0.04 * i + li * 0.35, 0.55, E.outBack);
          css(s, { opacity: clamp(p * 2), transform: `translateY(${(1 - p) * 60}px)`, filter: `blur(${(1 - clamp(p)) * 10}px)` });
        });
        l.wrap.style.transform = l._fit < 1 ? `scale(${l._fit})` : "";
      });
      const q = prog(t, rv + 1.1, 0.7);
      [la, lb].forEach((l) => { l.setAttribute("stroke-dasharray", 190); l.setAttribute("stroke-dashoffset", (1 - q) * 190); });
      sub.style.opacity = q > 0 ? 1 : 0;
      css(sT, { opacity: clamp(prog(t, rv + 1.25, 0.7) * 1.5), transform: `translateY(${(1 - prog(t, rv + 1.25, 0.7)) * 16}px)` });
    };
  };

  B.blend = (sc, L) => {
    const cols = ["#d9a441", "#d6587f", "#2f7f6b", "#8f7bc4", "#c8672a", "#7a5a37"];
    const g = svgRoot(L.b);
    const circles = cols.map((c) => sv("circle", { r: 150, fill: c, "fill-opacity": 0.34, stroke: c, "stroke-opacity": 0.7, "stroke-width": 2.5 }, g));
    sv("circle", { cx: 540, cy: 905, r: 0 }, g);
    // টেক্সট
    const t1 = el("div", "serif upper nowrap abs center", L.f, { top: "215px", fontSize: "132px", letterSpacing: "10px", lineHeight: "1.0", color: INK });
    t1.textContent = sc.title || "";
    const t2 = el("div", "serif nowrap abs center", L.f, { top: "370px", fontSize: "62px", fontStyle: "italic", fontWeight: 600, color: GOLD_D });
    t2.textContent = "= " + (sc.meaning || "");
    const t3 = el("div", "sans upper nowrap abs center", L.f, { top: "468px", fontSize: "32px", letterSpacing: "8px", color: INK });
    t3.textContent = sc.accords || "";
    return (t) => {
      const lt = t - sc.start;
      const e = E.outCubic(clamp((lt - 0.3) / 2.3));
      circles.forEach((c, i) => {
        const a = ((i * 60 - 90) + (1 - e) * 140 + lt * 4) * Math.PI / 180;
        const R = lerp(620, 150, e);
        c.setAttribute("cx", 540 + R * Math.cos(a));
        c.setAttribute("cy", 905 + R * Math.sin(a));
        c.style.opacity = clamp(lt / 0.4);
        c.setAttribute("r", lerp(110, 150, e));
      });
      const p1 = prog(lt, 0.15, 0.8);
      css(t1, { opacity: clamp(p1 * 1.5), transform: `translateY(${(1 - p1) * 30}px)`, filter: `blur(${(1 - p1) * 8}px)` });
      const p2 = prog(t, sc.times.meaning, 0.8);
      css(t2, { opacity: clamp(p2 * 1.5), transform: `translateY(${(1 - p2) * 22}px)` });
      const p3 = prog(t, sc.times.accords, 0.8);
      css(t3, { opacity: clamp(p3 * 1.5), transform: `translateY(${(1 - p3) * 18}px)` });
    };
  };

  B.traits = (sc, L) => {
    const h = header(L.f, sc);
    const o = orbitChips(L.f, L.b, sc, "traits");
    return (t) => { h(t, sc.start); o(t); };
  };

  B.notes = (sc, L) => {
    const h = header(L.f, sc, { y: 252, size: 92 });
    const py = pyramid(L.f, sc.level || "top", 540, 95);
    const o = orbitChips(L.f, L.b, sc, "notes");
    return (t) => { h(t, sc.start); py(t, sc.start); o(t); };
  };

  B.family = (sc, L) => {
    const h = header(L.f, sc);
    const cx = 540, cy = 905, R = 372;
    const g = svgRoot(L.b);
    const ring = sv("circle", { cx, cy, r: R, fill: "none", stroke: GOLD, "stroke-width": 2, "stroke-dasharray": "4 14", "stroke-linecap": "round", opacity: 0.8 }, g);
    const items = sc.items || [];
    const N = items.length;
    const nodes = items.map((it, k) => {
      const ang = (-90 + k * (360 / N)) * Math.PI / 180;
      const x = cx + R * Math.cos(ang), y = cy + R * Math.sin(ang);
      const wrap = el("div", "abs", L.f, { left: x - 120 + "px", top: y - 86 + "px", width: "240px", height: "230px", textAlign: "center" });
      const s = svgRoot(wrap, 240, 172, { left: "0px", top: "0px" });
      const c = it.color || GOLD;
      sv("circle", { cx: 120, cy: 86, r: 78, fill: "#fffaf0", stroke: GOLD, "stroke-width": 3 }, s);
      sv("circle", { cx: 120, cy: 86, r: 68, fill: c, "fill-opacity": 0.2 }, s);
      sv("path", { d: starPath(120, 86, 56, 37), fill: c, "fill-opacity": 0.95, stroke: "#fffaf0", "stroke-width": 2.5 }, s);
      sv("circle", { cx: 120, cy: 86, r: 15, fill: "#fffaf0", stroke: GOLD, "stroke-width": 2.5 }, s);
      const lab = el("div", "sans nowrap", wrap, { position: "absolute", left: "0px", top: "176px", width: "240px", fontSize: "36px", fontWeight: 600, letterSpacing: "1px", color: INK });
      lab.textContent = it.label;
      const spoke = sv("line", { x1: cx + (R - 90) * Math.cos(ang), y1: cy + (R - 90) * Math.sin(ang), x2: cx + 112 * Math.cos(ang), y2: cy + 112 * Math.sin(ang), stroke: GOLD, "stroke-width": 2, opacity: 0.7 }, g);
      return { wrap, spoke, it };
    });
    return (t) => {
      h(t, sc.start);
      const lt = t - sc.start;
      ring.setAttribute("transform", `rotate(${lt * 8} ${cx} ${cy})`);
      ring.style.opacity = prog(lt, 0.3, 0.8) * 0.8;
      nodes.forEach((n) => {
        const p = prog(t, n.it.t, 0.7, E.outBack);
        n.wrap.style.opacity = clamp(p * 2);
        n.wrap.style.transform = `scale(${lerp(0.4, 1, clamp(p, 0, 1.15))}) rotate(${(1 - clamp(p)) * -40}deg)`;
        n.spoke.style.opacity = prog(t, n.it.t + 0.1, 0.5) * 0.8;
      });
    };
  };

  B.character = (sc, L) => {
    const h = header(L.f, sc, { y: 190, size: 78 });
    const items = sc.items || [];
    const rows = items.map((it, k) => {
      const y = 405 + k * 130;
      const r = el("div", "abs center", L.f, { top: y + "px", height: "120px" });
      const w = splitLetters(r, it.label.toUpperCase(), { fontFamily: "var(--serif)", fontWeight: 700, fontSize: "112px", letterSpacing: "12px", lineHeight: "1.05", display: "block", textAlign: "center",
        backgroundImage: `linear-gradient(100deg, #6f4a14 0%, #a9772a 40%, #d6a748 50%, #a9772a 60%, #6f4a14 100%)`, backgroundSize: "300% 100%" });
      w.wrap.style.color = "transparent"; w.wrap.style.webkitBackgroundClip = "text"; w.wrap.style.backgroundClip = "text";
      const width = w.wrap.scrollWidth;
      const fit = width > 960 ? 960 / width : 1;
      return { r, w, it, fit };
    });
    return (t) => {
      h(t, sc.start);
      rows.forEach((o, k) => {
        const p = prog(t, o.it.t, 0.7, E.outCubic);
        const lt = t - o.it.t;
        css(o.r, { opacity: clamp(p * 2), transform: `translateY(${(1 - p) * 50}px) scale(${o.fit * lerp(0.94, 1, p)})`, filter: `blur(${(1 - p) * 10}px)` });
        o.w.wrap.style.backgroundPosition = `${100 - clamp(lt / 1.6) * 100}% 0`;
      });
    };
  };

  B.occasions = (sc, L) => {
    const h = header(L.f, sc, { y: 205, size: 88 });
    const items = sc.items || [];
    const rows = items.map((it, k) => {
      const y = 470 + k * 138;
      const r = el("div", "abs", L.f, { left: "70px", top: y + "px", width: "660px", height: "110px", display: "flex", alignItems: "center", gap: "22px" });
      const num = el("div", "serif", r, { fontSize: "56px", color: GOLD, width: "84px", flex: "none", fontWeight: 600, fontStyle: "italic" });
      num.textContent = String(k + 1).padStart(2, "0");
      const lab = el("div", "sans nowrap", r, { fontSize: "44px", fontWeight: 600, letterSpacing: "1px", color: INK, transformOrigin: "0 50%" });
      lab.textContent = it.label;
      const lw = lab.scrollWidth;
      const fit = lw > 540 ? 540 / lw : 1;
      lab.style.transform = fit < 1 ? `scale(${fit})` : "";
      const line = el("div", "abs", L.f, { left: "70px", top: y + 112 + "px", height: "2px", width: "0px", background: `linear-gradient(90deg, ${GOLD}, rgba(185,138,62,0))` });
      return { r, line, it };
    });
    return (t) => {
      h(t, sc.start);
      rows.forEach((o) => {
        const p = prog(t, o.it.t, 0.65, E.outCubic);
        css(o.r, { opacity: clamp(p * 2), transform: `translateX(${(1 - p) * -90}px)` });
        o.line.style.width = prog(t, o.it.t + 0.1, 0.6) * 640 + "px";
      });
    };
  };

  B.cta = (sc, L) => {
    const drawArch = arch(L.b, 540, 840, 1.0);
    const rays = el("div", "abs", L.b, { width: "1500px", height: "1500px", left: "-210px", top: "100px", borderRadius: "50%",
      background: "repeating-conic-gradient(from 0deg, rgba(200,150,60,0.26) 0deg 5deg, rgba(200,150,60,0) 5deg 12deg)",
      WebkitMaskImage: "radial-gradient(circle, #000 12%, rgba(0,0,0,0.5) 40%, transparent 66%)", maskImage: "radial-gradient(circle, #000 12%, rgba(0,0,0,0.5) 40%, transparent 66%)" });
    const t1 = el("div", "serif upper nowrap abs center", L.f, { top: "200px", fontSize: "142px", letterSpacing: "10px", lineHeight: "1.0", color: INK });
    t1.textContent = sc.headline || "Order Now";
    if (t1.scrollWidth > 960) t1.style.fontSize = Math.floor(142 * 960 / t1.scrollWidth) + "px";
    const t2 = el("div", "sans upper nowrap abs center", L.f, { top: "385px", fontSize: "32px", letterSpacing: "10px", color: GOLD_D });
    t2.textContent = sc.line || "";
    // বাটন
    const btn = el("div", "abs", L.f, { left: "130px", top: "1205px", width: "820px", height: "124px", borderRadius: "62px", overflow: "hidden",
      background: `linear-gradient(135deg, #e9c978 0%, ${GOLD} 55%, ${GOLD_D} 100%)`, boxShadow: "0 18px 40px rgba(120,80,10,0.35), inset 0 2px 0 rgba(255,255,255,0.55)", display: "flex", alignItems: "center", justifyContent: "center" });
    const bt = el("div", "sans upper nowrap", btn, { fontSize: "40px", fontWeight: 600, letterSpacing: "5px", color: "#2a1608", position: "relative" });
    bt.textContent = sc.button || "Order";
    const shine = el("div", "abs", btn, { top: "-20px", left: "-200px", width: "120px", height: "170px", background: "linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,0.65), rgba(255,255,255,0))", transform: "skewX(-20deg)" });
    // কার্সর
    const cur = svgRoot(L.f, 80, 90);
    const cg = sv("g", {}, cur);
    sv("path", { d: "M6 4 L6 66 L22 52 L34 80 L46 74 L34 48 L56 48 Z", fill: "#ffffff", stroke: INK, "stroke-width": 4, "stroke-linejoin": "round" }, cg);
    const ripple = svgRoot(L.f);
    const rc = sv("circle", { cx: 890, cy: 1266, r: 10, fill: "none", stroke: GOLD_D, "stroke-width": 4 }, ripple);
    // ঝিলমিল
    const sp = svgRoot(L.f);
    const sr = rng(5);
    const sparks = Array.from({ length: 22 }, () => ({ a: sr() * 6.28, d: 260 + sr() * 360, s: 14 + sr() * 22, e: sr() * 0.5 }));
    const spEls = sparks.map(() => sv("path", { d: sparkPath(0, 0, 1), fill: "#d4a244" }, sp));
    return (t) => {
      const lt = t - sc.start;
      drawArch(prog(lt, 0.1, 1.6, E.inOutCubic));
      rays.style.opacity = prog(lt, 0.2, 1.2);
      rays.style.transform = `rotate(${lt * 5}deg)`;
      const p1 = prog(lt, 0.15, 0.9, E.outBack);
      css(t1, { opacity: clamp(p1 * 1.5), transform: `scale(${lerp(0.8, 1, clamp(p1, 0, 1.1))})` });
      const p2 = prog(lt, 0.6, 0.8);
      css(t2, { opacity: clamp(p2 * 1.5), transform: `translateY(${(1 - p2) * 18}px)` });
      const pb = prog(lt, 0.9, 0.8, E.outBack);
      const click = clamp((lt - 1.65) / 0.25);
      const press = lt > 1.65 ? 1 - 0.05 * Math.sin(Math.PI * clamp((lt - 1.65) / 0.3)) : 1;
      const pulse = lt > 2.2 ? 1 + 0.018 * Math.sin((lt - 2.2) * 4.5) : 1;
      css(btn, { opacity: clamp(pb * 2), transform: `translateY(${(1 - pb) * 60}px) scale(${lerp(0.8, 1, clamp(pb, 0, 1.1)) * press * pulse})` });
      shine.style.left = -200 + ((lt * 0.7) % 2.4) / 2.4 * 1200 + "px";
      // কার্সর পথ
      const cp = E.inOutCubic(clamp((lt - 0.95) / 0.7));
      const cx = lerp(930, 884, cp), cy = lerp(1560, 1262, cp);
      cur.style.left = cx + "px"; cur.style.top = cy + "px";
      cur.style.opacity = prog(lt, 0.9, 0.3) * (lt > 2.6 ? clamp(1 - (lt - 2.6) / 0.5) : 1);
      cg.setAttribute("transform", `scale(${lt > 1.65 ? 1 - 0.12 * Math.sin(Math.PI * clamp((lt - 1.65) / 0.3)) : 1})`);
      const rp = clamp((lt - 1.65) / 0.8);
      rc.setAttribute("r", 10 + 120 * E.outCubic(rp));
      rc.style.opacity = lt < 1.65 ? 0 : (1 - rp) * 0.9;
      // ঝিলমিল
      sparks.forEach((s, i) => {
        const q = clamp((lt - 0.35 - s.e) / 1.6);
        const R = s.d * E.outCubic(q);
        const x = 540 + R * Math.cos(s.a), y = 840 + R * Math.sin(s.a);
        const sc2 = s.s * Math.sin(Math.PI * q);
        spEls[i].setAttribute("transform", `translate(${x} ${y}) scale(${sc2}) rotate(${q * 90})`);
        spEls[i].style.opacity = q > 0 && q < 1 ? 1 : 0;
      });
    };
  };

  B.statement = (sc, L) => {
    const t1 = el("div", "bn abs center", L.f, { top: "300px", fontSize: "64px", fontWeight: 700, lineHeight: "1.5", padding: "0 70px", color: INK });
    t1.textContent = sc.text || "";
    return (t) => { const p = prog(t, sc.start + 0.2, 0.8); css(t1, { opacity: clamp(p * 1.5), transform: `translateY(${(1 - p) * 30}px)` }); };
  };

  // ---------- সিন বসানো ----------
  const built = [];
  function buildAll() {
    scenes.forEach((sc) => {
      const f = el("div", "layer", frontL), b = el("div", "layer", backL);
      const fn = (B[sc.type] || B.statement)(sc, { f, b });
      built.push({ sc, f, b, fn });
    });
  }

  // ---------- ক্যাপশন ----------
  const capEl = el("div", "cap", capL);
  const capTxt = el("div", "", capEl, { maxWidth: "940px" });
  const capLine = el("div", "abs", capL, { left: "430px", top: "1590px", width: "220px", height: "4px", borderRadius: "2px", background: "rgba(185,138,62,0.25)", overflow: "hidden" });
  const capFill = el("div", "", capLine, { height: "4px", width: "0px", background: `linear-gradient(90deg, ${GOLD_L}, ${GOLD})` });
  const caps = D.captions;
  let lastCap = -2;
  function updateCaps(t) {
    let idx = -1;
    for (let i = 0; i < caps.length; i++) {
      const next = caps[i + 1] ? caps[i + 1].start : 1e9;
      const endShow = Math.min(caps[i].end + 0.3, next);
      if (t >= caps[i].start - 0.08 && t < endShow) { idx = i; break; }
    }
    if (idx !== lastCap) { capTxt.textContent = idx >= 0 ? caps[idx].text : ""; lastCap = idx; }
    if (idx < 0) { capEl.style.opacity = 0; capLine.style.opacity = 0; return; }
    const c = caps[idx];
    const next = caps[idx + 1] ? caps[idx + 1].start : 1e9;
    const endShow = Math.min(c.end + 0.3, next);
    const pin = prog(t, c.start - 0.08, 0.16);
    const pout = endShow < 1e8 && next - c.end < 0.4 ? 0 : clamp((endShow - t) / 0.18);
    capEl.style.opacity = Math.min(clamp(pin * 2.5), pout === 0 ? 1 : pout).toFixed(3);
    capTxt.style.transform = `translateY(${(1 - pin) * 26}px)`;
    capLine.style.opacity = 1;
    capFill.style.width = clamp((t - c.start) / Math.max(0.2, c.end - c.start)) * 220 + "px";
  }

  // ---------- মূল ফ্রেম ----------
  window.renderFrame = function (t) {
    // পটভূমি
    patRect.setAttribute("transform", `translate(${(t * 7) % 120} ${(t * 4) % 120})`);
    const b = bottleAt(t);
    css(bottle, { opacity: b.o, transform: `translate(${b.x - 500}px, ${b.y - 510}px) scale(${b.s.toFixed(4)}) rotate(${b.r.toFixed(3)}deg)` });
    css(glow, { left: b.x - 550 + "px", top: b.y - 550 + "px", opacity: (0.75 + 0.25 * Math.sin(t * 1.8)) * clamp(t / 0.8), transform: `scale(${b.s * 0.9 + 0.35})` });
    // ফ্রেম অঙ্কন
    const fp = prog(t, 0.1, 1.6, E.inOutCubic);
    fr1.setAttribute("stroke-dashoffset", (1 - fp) * perim);
    fr2.setAttribute("stroke-dashoffset", (1 - fp) * perim);
    corners.forEach((c, i) => { c.style.opacity = prog(t, 0.9 + i * 0.1, 0.5); });
    progBar.style.width = clamp(t / D.duration) * W + "px";
    const bp = prog(t, 0.8, 0.8);
    brandBar.style.opacity = bp; brandBar.style.transform = `translateY(${(1 - bp) * 14}px)`;
    // সিন
    built.forEach(({ sc, f, b: bk, fn }) => {
      const vis = t >= sc.start - 0.02 && t < sc.end + 0.02;
      f.style.display = bk.style.display = vis ? "block" : "none";
      if (!vis) return;
      const last = sc === scenes[scenes.length - 1];
      const ex = last ? 0 : E.inCubic(clamp((t - (sc.end - 0.38)) / 0.38));
      f.style.opacity = bk.style.opacity = (1 - ex).toFixed(3);
      f.style.transform = bk.style.transform = `translateY(${-24 * ex}px)`;
      fn(t);
    });
    updateCaps(t);
    drawParticles(t);
  };

  // ---------- প্রস্তুত ----------
  async function init() {
    try {
      const bnText = D.captions.map((c) => c.text).join("") + (CFG.presenter || "") + "০১২৩৪৫৬৭৮৯";
      const latin = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,-=:";
      const all = D.scenes.map((sc) => JSON.stringify(sc)).join("") + (CFG.brand || "") + (CFG.productName || "") + latin;
      await Promise.race([
        Promise.all([
          document.fonts.load('700 100px "Cormorant Garamond"', latin), document.fonts.load('600 100px "Cormorant Garamond"', latin),
          document.fonts.load('italic 600 60px "Cormorant Garamond"', latin),
          document.fonts.load('500 40px "Jost"', latin), document.fonts.load('600 40px "Jost"', latin),
          document.fonts.load('700 56px "Noto Sans Bengali"', bnText + all), document.fonts.load('600 40px "Noto Sans Bengali"', bnText + all),
        ]).then(() => document.fonts.ready),
        new Promise((r) => setTimeout(r, 8000)),
      ]);
    } catch (e) {}
    await new Promise((r) => { if (bImg.complete) r(); else { bImg.onload = r; bImg.onerror = r; } });
    try { await bImg.decode(); } catch (e) {}
    buildAll();
    window.renderFrame(0);
    window.__ready = true;
  }
  init();
})();
''',
}


def _extract_engine():
    """ভেতরের ফাইলগুলো (ডিজাইন, সাউন্ড, সিঙ্ক কোড) build/_engine-এ নামায়।"""
    d = _os.path.join(_os.getcwd(), "build", "_engine")
    for name, text in _ASSETS.items():
        p = _os.path.join(d, name)
        _os.makedirs(_os.path.dirname(p), exist_ok=True)
        if not _os.path.exists(p) or open(p, encoding="utf-8").read() != text:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
    return d

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

HERE = _extract_engine()
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
