"""SRT পার্স করা + অডিওর বিরতি (pause) দেখে ক্যাপশনের টাইমিং অডিওর সাথে মেলানো।

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
