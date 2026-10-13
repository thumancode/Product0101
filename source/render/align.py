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


def align_phrases(phrases, audio, duration):
    """প্রতিটি phrase-এর start/end বসায় (সেকেন্ডে)।"""
    sil = detect_silences(audio)
    sp0 = 0.0
    sp1 = duration
    interior = []
    for s, e in sil:
        if s <= 0.05 and e is not None:
            sp0 = e
        elif e is None:
            sp1 = s
        else:
            interior.append((s, e))
    # বক্তৃতার অংশগুলো
    intervals = []
    t = sp0
    for s, e in interior:
        intervals.append((t, s))
        t = e
    intervals.append((t, sp1))
    cum = [0.0]
    for a, b in intervals:
        cum.append(cum[-1] + (b - a))
    total_speech = cum[-1]

    # বাউন্ডারি: 0 = শুরু, 1..n = বিরতি, n+1 = শেষ
    bnd_end = [sp0] + [s for s, _ in interior] + [sp1]       # বিরতির আগে
    bnd_start = [sp0] + [e for _, e in interior] + [sp1]     # বিরতির পরে
    bnd_S = [0.0] + cum[1:]                                   # এ পর্যন্ত মোট বক্তৃতার সময়
    bnd_pen = [0.0] + [0.9 if (e - s) < 0.17 else 0.45 if (e - s) < 0.25 else 0.0 for s, e in interior] + [0.0]
    nb = len(bnd_end)

    P = len(phrases)
    chars = [p["chars"] for p in phrases]
    pref = [0]
    for c in chars:
        pref.append(pref[-1] + c)
    rate = total_speech / pref[-1]

    INF = 1e18
    dp = [[INF] * nb for _ in range(P + 1)]
    back = [[None] * nb for _ in range(P + 1)]
    dp[0][0] = 0.0
    for m in range(1, P + 1):
        for b in range(1, nb):
            best, arg = INF, None
            for k in range(0, m):
                c = pref[m] - pref[k]
                exp = c * rate
                for a in range(0, b):
                    if dp[k][a] >= INF:
                        continue
                    dur = bnd_S[b] - bnd_S[a]
                    cost = c * (math.log((dur + 0.4) / (exp + 0.4))) ** 2
                    v = dp[k][a] + cost + bnd_pen[b] * (1.0 if b < nb - 1 else 0.0)
                    if v < best:
                        best, arg = v, (k, a)
            dp[m][b], back[m][b] = best, arg
    # ব্যাকট্র্যাক
    groups = []
    m, b = P, nb - 1
    while m > 0:
        k, a = back[m][b]
        groups.append((k, m, a, b))
        m, b = k, a
    groups.reverse()

    def inv(S, side):
        # বক্তৃতার সময় S -> আসল সময়
        idxs = [i for i in range(len(intervals)) if cum[i] - 1e-9 <= S <= cum[i + 1] + 1e-9]
        i = idxs[0] if side == "end" else idxs[-1]
        a, b2 = intervals[i]
        return a + (S - cum[i])

    for k, m2, a, b in groups:
        S0, S1 = bnd_S[a], bnd_S[b]
        tot = pref[m2] - pref[k]
        acc = 0
        for idx in range(k, m2):
            s_frac = S0 + (S1 - S0) * acc / tot
            acc += chars[idx]
            e_frac = S0 + (S1 - S0) * acc / tot
            phrases[idx]["start"] = bnd_start[a] if idx == k else inv(s_frac, "start")
            phrases[idx]["end"] = bnd_end[b] if idx == m2 - 1 else inv(e_frac, "end")
    return phrases, {"speech_start": sp0, "speech_end": sp1, "pauses": len(interior)}


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
