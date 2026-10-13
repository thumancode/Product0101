/* মোশন গ্রাফিক্স স্টেজ: সবকিছু শুধু সময় t থেকে হিসাব হয় (window.renderFrame(t)),
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
      if (t >= caps[i].start - 0.04 && t < endShow) { idx = i; break; }
    }
    if (idx !== lastCap) { capTxt.textContent = idx >= 0 ? caps[idx].text : ""; lastCap = idx; }
    if (idx < 0) { capEl.style.opacity = 0; capLine.style.opacity = 0; return; }
    const c = caps[idx];
    const next = caps[idx + 1] ? caps[idx + 1].start : 1e9;
    const endShow = Math.min(c.end + 0.3, next);
    const pin = prog(t, c.start - 0.04, 0.28);
    const pout = endShow < 1e8 && next - c.end < 0.4 ? 0 : clamp((endShow - t) / 0.18);
    capEl.style.opacity = Math.min(clamp(pin * 1.6), pout === 0 ? 1 : pout).toFixed(3);
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
