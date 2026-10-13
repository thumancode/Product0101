"""সাউন্ড ইফেক্ট কোড দিয়ে বানানো হয় (কোনো ডাউনলোড বা কপিরাইট ঝামেলা নেই)।
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
