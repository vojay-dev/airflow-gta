// GTA2 Dashcam run tab: what Airflow saw, what nimble picked, and where the car went.
// Live while the run is going (follows the newest move), a replay with a scrubber once it has finished.
const { createElement: h, useCallback, useEffect, useMemo, useRef, useState } = globalThis.React;
const { Badge, Box, Button, Heading, HStack, Stack, Text } = globalThis.ChakraUI;

const DRIVE_TASK = "turn.drive";
const FIRST_TASK = "insert_coin";
const POLL_MS = 1000;
const REPLAY_MS = 450;

// The eight moves around the car, clockwise from straight ahead (car-relative).
const MOVES = [
  { id: "forward", icon: "↑", angle: 0 },
  { id: "forward_right", icon: "↗", angle: 45 },
  { id: "handbrake_right", icon: "↻", angle: 90 },
  { id: "reverse_right", icon: "↘", angle: 135 },
  { id: "reverse", icon: "↓", angle: 180 },
  { id: "reverse_left", icon: "↙", angle: 225 },
  { id: "handbrake_left", icon: "↺", angle: 270 },
  { id: "forward_left", icon: "↖", angle: 315 },
];
const ICON = Object.fromEntries(MOVES.map((m) => [m.id, m.icon]));
const TRAIL = ["#facc15", "#22d3ee", "#f472b6", "#a78bfa", "#4ade80"];
const GROUND = { R: "#3f4a5c", P: "#252f40", F: "#173b28", "#": "#0b1020" };

// ---- Airflow API --------------------------------------------------------------

const api = async (path) => {
  const res = await fetch(`/api/v2${path}`, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
};
const runPath = (dagId, runId) => `/dags/${encodeURIComponent(dagId)}/dagRuns/${encodeURIComponent(runId)}`;
const firstFrame = async (run) =>
  (await api(`${run}/taskInstances/${FIRST_TASK}/xcomEntries/return_value`).catch(() => null))?.value ?? null;
const driveEntries = async (run) =>
  (await api(`${run}/taskInstances/${DRIVE_TASK}/xcomEntries?xcom_key=return_value&order_by=region_index&limit=1000`).catch(
    () => null,
  ))?.xcom_entries ?? [];
const driveFrame = async (run, e) =>
  (await api(
    `${run}/taskInstances/${DRIVE_TASK}/xcomEntries/return_value?map_index=-1&region_id=${e.region_id}&region_index=${e.region_index}`,
  )).value;

// ---- small pieces ---------------------------------------------------------------

const Stat = ({ label, value, color }) =>
  h(
    Box,
    { minW: "88px" },
    h(Text, { fontSize: "xs", color: "fg.muted", textTransform: "uppercase", letterSpacing: "wider" }, label),
    h(Text, { fontSize: "2xl", fontWeight: "bold", color, fontFamily: "mono" }, value),
  );

const Panel = ({ title, children, ...rest }) =>
  h(
    Box,
    { borderWidth: "1px", borderRadius: "lg", p: 3, bg: "bg.subtle", ...rest },
    title && h(Text, { fontSize: "xs", fontWeight: "bold", color: "fg.muted", textTransform: "uppercase", letterSpacing: "wider", mb: 2 }, title),
    children,
  );

// The dashcam: the game's own screenshot, with the move and event laid over it.
const Dashcam = ({ frame }) => {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [frame?.frame_url]);
  const event = frame?.event;
  const bad = event && /Lost|Wasted/.test(event);
  const good = event && /Delivered|got a car/.test(event);
  return h(
    Box,
    { position: "relative", borderRadius: "lg", overflow: "hidden", bg: "black", aspectRatio: "16 / 9", w: "100%" },
    frame?.frame_url && !broken
      ? h("img", { src: frame.frame_url, onError: () => setBroken(true), style: { width: "100%", height: "100%", objectFit: "cover", display: "block" } })
      : h(Text, { color: "gray.400", p: 6 }, broken ? "Screenshot not available: is the GTA2 bridge running (make bridge)?" : "Waiting for the first frame..."),
    frame &&
      h(
        Box,
        { position: "absolute", top: 3, left: 3, bg: "rgba(0,0,0,0.72)", color: "white", px: 3, py: 1, borderRadius: "md", fontFamily: "mono", fontSize: "lg", fontWeight: "bold" },
        frame.move === 0 ? "START" : `MOVE ${frame.move}  ${ICON[frame.last_action] ?? ""} ${frame.last_action ?? ""}`,
      ),
    frame?.route_distance != null &&
      h(
        Box,
        { position: "absolute", top: 3, right: 3, bg: "rgba(0,0,0,0.72)", color: "#facc15", px: 3, py: 1, borderRadius: "md", fontFamily: "mono", fontWeight: "bold" },
        `${frame.route_distance} blocks to go`,
      ),
    event &&
      h(
        Box,
        {
          position: "absolute", bottom: 3, left: "50%", transform: "translateX(-50%)", maxW: "90%",
          bg: bad ? "rgba(185,28,28,0.9)" : good ? "rgba(21,128,61,0.9)" : "rgba(0,0,0,0.75)",
          color: "white", px: 4, py: 2, borderRadius: "md", fontWeight: "bold", textAlign: "center",
        },
        event,
      ),
  );
};

// The steering wheel: every move nimble could pick, colored by what the lookahead said about it.
const Wheel = ({ options, chosen, size = 240 }) => {
  const c = size / 2;
  const R = size * 0.38;
  const best = Math.max(1, ...Object.values(options ?? {}).map((o) => o.gain));
  return h(
    "svg",
    { width: size, height: size, viewBox: `0 0 ${size} ${size}`, style: { display: "block", margin: "0 auto" } },
    h("circle", { cx: c, cy: c, r: R + 26, fill: "none", stroke: "#334155", strokeWidth: 1, strokeDasharray: "3 5" }),
    h("rect", { x: c - 13, y: c - 22, width: 26, height: 44, rx: 7, fill: "#f8fafc" }),
    h("rect", { x: c - 9, y: c - 17, width: 18, height: 9, rx: 2, fill: "#38bdf8" }),
    ...MOVES.map((m) => {
      const o = options?.[m.id];
      const a = ((m.angle - 90) * Math.PI) / 180;
      const x = c + R * Math.cos(a);
      const y = c + R * Math.sin(a);
      const picked = m.id === chosen;
      let fill = "#475569";
      let label = "";
      if (o) {
        if (o.blocked) {
          fill = "#ef4444";
          label = "💥";
        } else if (o.gain > 0.5) {
          fill = `rgba(34,197,94,${0.35 + 0.65 * Math.min(1, o.gain / best)})`;
          label = `+${Math.round(o.gain)}`;
        } else if (o.gain < -0.5) {
          label = `${Math.round(o.gain)}`;
        } else label = "±0";
      }
      return h(
        "g",
        { key: m.id, style: picked ? { filter: "drop-shadow(0 0 8px #facc15)" } : undefined },
        h("line", { x1: c, y1: c, x2: x, y2: y, stroke: picked ? "#facc15" : "#1e293b", strokeWidth: picked ? 3 : 1 }),
        h("circle", { cx: x, cy: y, r: 22, fill, stroke: picked ? "#facc15" : "#0f172a", strokeWidth: picked ? 4 : 2 }),
        h("text", { x, y: y - 2, textAnchor: "middle", fontSize: 18, fill: "#fff", fontWeight: "bold" }, m.icon),
        h("text", { x, y: y + 14, textAnchor: "middle", fontSize: 10, fill: "#fff", fontFamily: "monospace" }, label),
      );
    }),
  );
};

// The city map: the real Downtown road grid, the car's trail per delivery, drop-offs and the car.
const CityMap = ({ grid, frames, index, size = 360 }) => {
  const canvas = useRef(null);
  const shown = frames.slice(0, index + 1);
  const box = useMemo(() => {
    const pts = frames.flatMap((f) => [[f.x, f.y], f.target ? [f.target.x, f.target.y] : null]).filter((p) => p && p[0] != null);
    if (!pts.length) return null;
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const span = Math.max(40, Math.max(...xs) - Math.min(...xs) + 16, Math.max(...ys) - Math.min(...ys) + 16);
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
    const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    return { x0: cx - span / 2, y0: cy - span / 2, span };
  }, [frames]);

  useEffect(() => {
    let raf;
    const draw = (now) => {
      const ctx = canvas.current?.getContext("2d");
      if (ctx && box) {
        const s = size / box.span;
        const X = (x) => (x - box.x0) * s;
        const Y = (y) => (y - box.y0) * s;
        ctx.fillStyle = GROUND["#"];
        ctx.fillRect(0, 0, size, size);
        if (grid) {
          for (let gy = Math.max(0, Math.floor(box.y0)); gy < Math.min(256, box.y0 + box.span); gy++) {
            const row = grid[gy];
            for (let gx = Math.max(0, Math.floor(box.x0)); gx < Math.min(256, box.x0 + box.span); gx++) {
              const c = row[gx];
              if (c === "#") continue;
              ctx.fillStyle = GROUND[c];
              ctx.fillRect(X(gx), Y(gy), s + 0.5, s + 0.5);
            }
          }
        }
        // trail, one color per delivery
        ctx.lineWidth = Math.max(2, s * 0.35);
        ctx.lineJoin = ctx.lineCap = "round";
        for (let i = 1; i < shown.length; i++) {
          const a = shown[i - 1];
          const b = shown[i];
          if (a.x == null || b.x == null) continue;
          ctx.strokeStyle = TRAIL[(a.deliveries ?? 0) % TRAIL.length];
          ctx.beginPath();
          ctx.moveTo(X(a.x), Y(a.y));
          ctx.lineTo(X(b.x), Y(b.y));
          ctx.stroke();
        }
        const last = shown[shown.length - 1];
        // delivered drop-offs
        (last?.delivered ?? []).forEach((t, i) => {
          ctx.fillStyle = TRAIL[i % TRAIL.length];
          ctx.beginPath();
          ctx.arc(X(t.x), Y(t.y), 8, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillStyle = "#0b1020";
          ctx.font = "bold 11px sans-serif";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText("✓", X(t.x), Y(t.y) + 1);
        });
        // current drop-off, pulsing
        if (last?.target && last.status === "playing") {
          const r = 9 + Math.sin(now / 200) * 4;
          ctx.strokeStyle = TRAIL[(last.deliveries ?? 0) % TRAIL.length];
          ctx.lineWidth = 3;
          ctx.beginPath();
          ctx.arc(X(last.target.x), Y(last.target.y), r, 0, Math.PI * 2);
          ctx.stroke();
          ctx.fillStyle = "#fff";
          ctx.font = "13px sans-serif";
          ctx.fillText("📦", X(last.target.x), Y(last.target.y) + 1);
        }
        // the car, pointing where it faces (rot 0 = +y, 360 = +x)
        if (last?.x != null) {
          const a = ((last.rot ?? 0) / 1440) * Math.PI * 2;
          ctx.save();
          ctx.translate(X(last.x), Y(last.y));
          ctx.rotate(-a);
          ctx.fillStyle = "#f8fafc";
          ctx.beginPath();
          ctx.moveTo(0, 10);
          ctx.lineTo(-6, -7);
          ctx.lineTo(6, -7);
          ctx.closePath();
          ctx.fill();
          ctx.restore();
        }
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [grid, box, shown, size]);

  return h("canvas", { ref: canvas, width: size, height: size, style: { borderRadius: 8, display: "block", maxWidth: "100%" } });
};

// Blocks to go over time: a sawtooth, one tooth per delivery.
const Sparkline = ({ frames, index, width = 360, height = 70 }) => {
  const pts = frames.map((f, i) => [i, f.route_distance]).filter((p) => p[1] != null);
  if (pts.length < 2) return h(Text, { fontSize: "sm", color: "fg.muted" }, "Not enough moves yet.");
  const maxD = Math.max(...pts.map((p) => p[1]), 1);
  const n = Math.max(frames.length - 1, 1);
  const X = (i) => (i / n) * (width - 4) + 2;
  const Y = (d) => height - 4 - (d / maxD) * (height - 8);
  const line = pts.map(([i, d], k) => `${k ? "L" : "M"}${X(i).toFixed(1)},${Y(d).toFixed(1)}`).join(" ");
  return h(
    "svg",
    { width: "100%", height, viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none" },
    h("path", { d: line, fill: "none", stroke: "#facc15", strokeWidth: 2 }),
    h("line", { x1: X(index), x2: X(index), y1: 0, y2: height, stroke: "#f8fafc", strokeWidth: 1, strokeDasharray: "3 3" }),
  );
};

// How often nimble picked each move so far.
const Habits = ({ frames, index }) => {
  const counts = {};
  frames.slice(1, index + 1).forEach((f) => (counts[f.last_action] = (counts[f.last_action] ?? 0) + 1));
  const max = Math.max(1, ...Object.values(counts));
  return h(
    Stack,
    { gap: 1 },
    ...MOVES.map((m) =>
      h(
        HStack,
        { key: m.id, gap: 2 },
        h(Text, { w: "130px", fontSize: "xs", fontFamily: "mono" }, `${m.icon} ${m.id}`),
        h(Box, { h: "10px", borderRadius: "sm", bg: "purple.400", w: `${((counts[m.id] ?? 0) / max) * 160}px` }),
        h(Text, { fontSize: "xs", color: "fg.muted" }, counts[m.id] ?? 0),
      ),
    ),
  );
};

// ---- the tab ----------------------------------------------------------------------

const Gta2Dashcam = ({ dagId, runId }) => {
  const [frames, setFrames] = useState([]);
  const [index, setIndex] = useState(0);
  const [follow, setFollow] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [runState, setRunState] = useState(undefined);
  const [grid, setGrid] = useState(null);
  const [error, setError] = useState(null);
  const loaded = useRef([]);

  // Poll the run: the start frame, then every drive XCom we have not loaded yet.
  useEffect(() => {
    let alive = true;
    let timer;
    const run = runPath(dagId, runId);
    const poll = async () => {
      try {
        const info = await api(run);
        if (!alive) return;
        setRunState(info.state);
        const active = ["queued", "running"].includes(info.state);
        if (!loaded.current.length) {
          const start = await firstFrame(run);
          if (start) loaded.current = [start];
        }
        if (loaded.current.length) {
          const entries = await driveEntries(run);
          const missing = entries.slice(loaded.current.length - 1);
          for (let i = 0; i < missing.length; i += 12) {
            const batch = await Promise.all(missing.slice(i, i + 12).map((e) => driveFrame(run, e)));
            loaded.current = [...loaded.current, ...batch];
          }
          if (alive) setFrames(loaded.current);
        }
        setError(null);
        if (!active) return; // finished: no more polling, the replay has everything
      } catch (err) {
        if (alive) setError(String(err));
      }
      if (alive) timer = setTimeout(poll, POLL_MS);
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [dagId, runId]);

  // The road grid, straight from the bridge (it keeps a copy on disk).
  const gridUrl = frames[0]?.grid_url;
  useEffect(() => {
    if (!gridUrl) return;
    fetch(gridUrl)
      .then((r) => r.json())
      .then((d) => setGrid(d.rows))
      .catch(() => setGrid(null));
  }, [gridUrl]);

  // Live: follow the newest move. Replay: step through with play/pause.
  useEffect(() => {
    if (follow && frames.length) setIndex(frames.length - 1);
  }, [frames, follow]);
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      setIndex((i) => {
        if (i >= frames.length - 1) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, REPLAY_MS);
    return () => clearInterval(t);
  }, [playing, frames.length]);
  // preload the next screenshot so the replay does not flicker
  useEffect(() => {
    const next = frames[index + 1]?.frame_url;
    if (next) new Image().src = next;
  }, [index, frames]);

  const frame = frames[index];
  const before = index > 0 ? frames[index - 1] : null;
  const live = ["queued", "running"].includes(runState);
  const palette = { running: "blue", queued: "gray", success: "green", failed: "red" }[runState] ?? "gray";
  const replay = useCallback(() => {
    setFollow(false);
    setIndex(0);
    setPlaying(true);
  }, []);

  return h(
    Box,
    { p: 4 },
    h(
      HStack,
      { justify: "space-between", mb: 3, flexWrap: "wrap", gap: 2 },
      h(HStack, { gap: 3 }, h(Heading, { size: "md" }, "GTA2 Dashcam"), runState && h(Badge, { colorPalette: palette }, runState)),
      frames.length > 1 &&
        h(
          HStack,
          { gap: 2 },
          h(Button, { size: "xs", variant: "outline", onClick: replay }, "⏮ Replay"),
          h(Button, { size: "xs", variant: "outline", onClick: () => { setFollow(false); setPlaying((p) => !p); } }, playing ? "⏸ Pause" : "▶ Play"),
          live && h(Button, { size: "xs", variant: follow ? "solid" : "outline", onClick: () => { setPlaying(false); setFollow(true); } }, "● Live"),
        ),
    ),
    !frame && h(Text, null, runState && !live ? "This run has no game in it." : "Waiting for Airflow to get a car..."),
    frame &&
      h(
        Stack,
        { gap: 4 },
        frames.length > 1 &&
          h("input", {
            type: "range", min: 0, max: frames.length - 1, value: index, style: { width: "100%" },
            onChange: (e) => { setFollow(false); setPlaying(false); setIndex(Number(e.target.value)); },
          }),
        h(
          HStack,
          { align: "stretch", gap: 4, flexWrap: "wrap" },
          h(
            Stack,
            { flex: "1 1 560px", gap: 4, minW: "320px" },
            h(Dashcam, { frame }),
            h(
              HStack,
              { gap: 6, flexWrap: "wrap" },
              h(Stat, { label: "Move", value: frame.move }),
              h(Stat, { label: "Packages", value: `📦 ${frame.deliveries}/${frame.goal ?? 3}`, color: "green.400" }),
              h(Stat, { label: "Cars", value: "🚗".repeat(Math.max(0, frame.cars)) || "0", color: "red.400" }),
              h(Stat, { label: "To go", value: frame.route_distance ?? "?", color: "yellow.400" }),
              h(Stat, { label: "Moved", value: frame.moved ?? 0 }),
            ),
            h(Panel, { title: "Blocks to the drop-off" }, h(Sparkline, { frames, index })),
          ),
          h(
            Stack,
            { flex: "0 1 380px", gap: 4 },
            h(
              Panel,
              { title: frame.move === 0 ? "First decision coming up" : `Move ${frame.move}: nimble picked ${frame.last_action}` },
              h(Wheel, { options: (before ?? frame).options, chosen: before ? frame.last_action : null }),
              h(Text, { fontSize: "xs", color: "fg.muted", textAlign: "center", mt: 1 }, "green: gets closer · red: crash · glow: picked"),
            ),
            h(Panel, { title: "City map" }, h(CityMap, { grid, frames, index })),
          ),
        ),
        h(
          HStack,
          { align: "stretch", gap: 4, flexWrap: "wrap" },
          h(
            Panel,
            { title: "What nimble saw", flex: "1 1 480px" },
            h(
              "pre",
              { style: { whiteSpace: "pre-wrap", fontSize: 12, lineHeight: 1.5, margin: 0, fontFamily: "ui-monospace, monospace" } },
              (before ?? frame).look || "(no decision for this frame)",
            ),
          ),
          h(Panel, { title: "Driving habits", flex: "0 1 380px" }, h(Habits, { frames, index })),
        ),
      ),
    error && h(Text, { color: "red.500", fontSize: "sm", mt: 2 }, error),
  );
};

globalThis.AirflowPlugin = Gta2Dashcam;
