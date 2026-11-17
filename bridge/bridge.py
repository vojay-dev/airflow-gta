# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.48", "fastapi>=0.115", "uvicorn>=0.32"]
# ///
"""GTA2 bridge: a visible Chrome window running the GTA2 browser port, driven turn by turn.

- Serves the pre-built site (../site) at http://127.0.0.1:8765/ and the local installer
  (../game-data) it unpacks on first start; site/gamefiles.js is patched to load it from here.
- Launches your installed Chrome with a persistent profile (.chrome-profile), so the
  unpacked game files stay cached after the first start.
- Keeps the game paused between moves: /api/act presses keys, lets the game run for one
  time slice, pauses again and returns the new state plus a lookahead for the next decision.

The game: deliver packages across Downtown. Every drop-off is a waypoint on the radar.
Three deliveries win; lose three cars and it's game over.

Airflow (in Docker) talks to it at http://host.docker.internal:8765/api/...
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from playwright.async_api import BrowserContext, Page, async_playwright
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from driver import Driver  # noqa: E402
from gmp import GtaMap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
INSTALLER = ROOT / "game-data" / "GTA2INSTALLER.exe"
PROFILE = ROOT / ".chrome-profile"
FRAMES = ROOT / "frames"
PORT = int(os.environ.get("BRIDGE_PORT", "8765"))
GAME_URL = f"http://127.0.0.1:{PORT}/?devdata"
DISTRICT_MAP = "/gta2/data/wil.gmp"  # Downtown, "start play in area 1"

DELIVERIES_TO_WIN = 3
CARS = 3
ARRIVED = 2.5  # blocks by road
CAR_MODEL = 10
SLICE_MS = 700

# What the decision model can choose, as keys held during one time slice.
ACTIONS: dict[str, list[str]] = {
    "forward": ["KeyW"],
    "forward_left": ["KeyW", "KeyA"],
    "forward_right": ["KeyW", "KeyD"],
    "reverse": ["KeyS"],
    "reverse_left": ["KeyS", "KeyA"],
    "reverse_right": ["KeyS", "KeyD"],
    "handbrake_left": ["KeyW", "KeyA", "Space"],
    "handbrake_right": ["KeyW", "KeyD", "Space"],
    "wait": [],
}

JS_STATE = "() => (window.gta2 && window.gta2.dev && window.gta2.Module) ? { ready: true, ...window.gta2.dev.state() } : { ready: false }"
JS_PAUSE = "(on) => window.gta2.Module.ccall('gta2_port_set_paused', null, ['number'], [on ? 1 : 0])"
JS_KEY = "async ([code, ms]) => { await window.gta2.dev.hold(code, ms); }"
JS_RUN = """async ([keys, ms]) => {
  const g = window.gta2;
  g.Module.ccall('gta2_port_set_paused', null, ['number'], [0]);
  await Promise.all(keys.length ? keys.map((k) => g.dev.hold(k, ms)) : [g.dev.sleep(ms)]);
  g.Module.ccall('gta2_port_set_paused', null, ['number'], [1]);
}"""
JS_READ_FILE = """(path) => {
  const b = window.gta2.Module.FS.readFile(path);
  let s = '';
  for (let i = 0; i < b.length; i += 32768) s += String.fromCharCode.apply(null, b.subarray(i, i + 32768));
  return btoa(s);
}"""


class Act(BaseModel):
    action: str
    ms: int = SLICE_MS


class Start(BaseModel):
    seed: int = 7


class Bridge:
    def __init__(self) -> None:
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.lock = asyncio.Lock()
        self.driver: Driver | None = None
        self.grids: dict[int, list[list[str]]] = {}
        self.game: dict = {}

    async def open(self, pw) -> None:
        self.context = await pw.chromium.launch_persistent_context(
            str(PROFILE),
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 720},
            args=["--autoplay-policy=no-user-gesture-required", "--window-position=40,40"],
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        # The page is opened in start(): the web server only serves after startup.

    # ---- page helpers -------------------------------------------------------

    async def js(self, code: str, arg=None):
        assert self.page
        return await self.page.evaluate(code, arg)

    async def state(self) -> dict:
        return await self.js(JS_STATE)

    async def paused(self, on: bool) -> None:
        await self.js(JS_PAUSE, on)

    async def key(self, code: str, ms: int = 150) -> None:
        await self.js(JS_KEY, [code, ms])

    async def wait_for(self, check, timeout_s: float, every: float = 0.5) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            st = await self.state()
            if check(st):
                return st
            await asyncio.sleep(every)
        raise HTTPException(504, f"Timed out after {timeout_s}s waiting for the game.")

    # ---- game flow ------------------------------------------------------------

    async def boot(self) -> None:
        """Fresh page, landing page Play, GTA2's front end, into Downtown on foot."""
        assert self.page
        await self.page.goto(GAME_URL)
        if os.environ.get("SHOW_TOOLS") != "1":
            # clean picture for recording: hide the site's button bar over the game
            await self.page.add_style_tag(content="#tools { display: none !important; }")
        await self.page.locator("[data-play]:visible").first.click(timeout=30_000)
        # the engine is up when the dev helpers exist (first start unpacks the installer: ~1 min)
        await self.wait_for(lambda s: s.get("ready"), timeout_s=240)
        await asyncio.sleep(4)  # the front end's main menu
        await self.paused(False)
        await self.key("Enter")  # PLAY -> the player screen, "Start play in area" is selected
        await asyncio.sleep(2.5)
        await self.key("Enter")  # start play in area 1
        await self.wait_for(lambda s: s.get("player"), timeout_s=60)
        await asyncio.sleep(3)  # the district intro

    async def get_in_a_car(self) -> bool:
        """Spawn a car next to the player and get in, the way a player would (Enter)."""
        await self.paused(False)
        await self.js(f"() => window.gta2.dev.spawnCar({CAR_MODEL}, 1, 0, Math.round(window.gta2.dev.state().rot / 360) * 360)")
        await asyncio.sleep(0.3)
        await self.key("Enter", 200)
        try:
            await self.wait_for(lambda s: s.get("inCar"), timeout_s=6, every=0.25)
            ok = True
        except HTTPException:
            ok = False
        await self.paused(True)
        return ok

    async def to_the_road(self) -> None:
        """Teleport (on foot) to the middle of the longest straight road and face along it."""
        road = await self.js("() => window.gta2.dev.findRoad()")
        x = road["x"] + (0 if road["alongY"] else road["length"] // 2)
        y = road["y"] + (road["length"] // 2 if road["alongY"] else 0)
        await self.js("([x, y]) => window.gta2.dev.teleport(x, y)", [x, y])
        await asyncio.sleep(0.8)

    async def grid(self, level: int) -> list[list[str]]:
        """The street-level grid, also kept on disk for the Airflow run tab (frames/grid-<level>.json)."""
        if level not in self.grids:
            cached = FRAMES / f"grid-{level}.json"
            if cached.exists():
                self.grids[level] = [list(row) for row in json.loads(cached.read_text())]
            else:
                data = base64.b64decode(await self.js(JS_READ_FILE, DISTRICT_MAP))
                self.grids[level] = GtaMap(data).surface_grid(level)
                FRAMES.mkdir(exist_ok=True)
                cached.write_text(json.dumps(["".join(row) for row in self.grids[level]]))
        return self.grids[level]

    async def set_waypoint(self, target: dict) -> None:
        await self.js("(t) => { if (window.gta2.map) window.gta2.map.waypoint = { x: t.x, y: t.y }; }", target)

    async def start(self, seed: int) -> dict:
        await self.boot()
        await self.to_the_road()
        if not await self.get_in_a_car():
            raise HTTPException(500, "Could not get into a car.")
        st = await self.state()
        level = max(0, round(st["z"]) - 1)
        self.driver = Driver(await self.grid(level), seed=seed)
        target = self.driver.pick_target(st["x"], st["y"])
        await self.set_waypoint(target)
        self.game = {
            "game_id": time.strftime("%Y%m%d-%H%M%S"), "level": level,
            "move": 0, "status": "playing", "deliveries": 0, "cars": CARS, "seed": seed,
            "goal": DELIVERIES_TO_WIN, "target": target, "delivered": [],
            "event": f"Airflow got a car. First job: deliver {target['name']}.",
            "last_action": None, "moved": 0.0,
        }
        return await self.snapshot(st, await self.frame(0))

    async def snapshot(self, st: dict | None = None, frame: str | None = None) -> dict:
        st = st or await self.state()
        g = self.game
        look = self.driver.look(st, g["moved"], g["last_action"]) if self.driver and st.get("inCar") else None
        return {
            **g,
            "x": st.get("x"), "y": st.get("y"), "rot": st.get("rot"), "speed": st.get("speed"),
            "in_car": st.get("inCar"),
            "route_distance": look["route_distance"] if look else None,
            "look": look["text"] if look else "",
            "options": look["options"] if look else {},
            "frame": frame,
            "frame_url": f"http://127.0.0.1:{PORT}/frames/{g['game_id']}/{frame}" if frame and g else None,
            "grid_url": f"http://127.0.0.1:{PORT}/api/grid/{g['level']}" if g else None,
        }

    async def act(self, action: str, ms: int) -> dict:
        if action not in ACTIONS:
            raise HTTPException(400, f"Unknown action {action}. Pick one of {list(ACTIONS)}.")
        g = self.game
        if not g or g["status"] != "playing":
            raise HTTPException(409, "No game running. POST /api/start first.")
        before = await self.state()
        await self.js(JS_RUN, [ACTIONS[action], ms])
        after = await self.state()
        g["move"] += 1
        g["last_action"] = action
        g["moved"] = round(math.dist((before["x"], before["y"]), (after["x"], after["y"])), 2) if after.get("player") else 0.0
        g["event"] = ""

        if not after.get("player") or not after.get("inCar"):
            g["cars"] -= 1
            if g["cars"] <= 0:
                g["status"] = "lost"
                g["event"] = "Wasted. Out of cars."
            else:
                g["event"] = f"Lost the car! {g['cars']} left. Airflow finds another one."
                if not after.get("player"):  # wasted: wait for the respawn
                    await self.paused(False)
                    await self.wait_for(lambda s: s.get("player"), timeout_s=30)
                    await asyncio.sleep(2)
                    await self.paused(True)
                g["moved"] = 0.0
                g["last_action"] = None
                await self.get_in_a_car()
                after = await self.state()
        elif self.driver and self.driver.route_distance(after["x"], after["y"]) <= ARRIVED:
            g["deliveries"] += 1
            g["delivered"] = [*g["delivered"], g["target"]]
            await self.js("() => window.gta2.dev.addCash(1000)")
            if g["deliveries"] >= DELIVERIES_TO_WIN:
                g["status"] = "won"
                g["event"] = f"Delivered {g['target']['name']}! {DELIVERIES_TO_WIN} out of {DELIVERIES_TO_WIN}. Airflow wins."
            else:
                done = g["target"]["name"]
                g["target"] = self.driver.pick_target(after["x"], after["y"])
                await self.set_waypoint(g["target"])
                g["event"] = f"Delivered {done}! +$1000. Next: {g['target']['name']}."

        frame = await self.frame(g["move"])
        return await self.snapshot(after, frame)

    async def frame(self, n: int) -> str:
        assert self.page
        folder = FRAMES / self.game["game_id"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"move-{n:04d}.jpg"
        await self.page.screenshot(path=str(path), type="jpeg", quality=70)
        return path.name


bridge = Bridge()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_playwright() as pw:
        await bridge.open(pw)
        yield
        if bridge.context:
            await bridge.context.close()


app = FastAPI(lifespan=lifespan)
# The Airflow run tab (the browser at localhost:8080) reads the map grid from here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/grid/{level}")
async def api_grid(level: int):
    """The street-level road grid: 256 strings of 256 chars ('R' road, 'P' pavement, 'F' field, '#' blocked)."""
    cached = FRAMES / f"grid-{level}.json"
    if cached.exists():
        return {"level": level, "rows": json.loads(cached.read_text())}
    async with bridge.lock:
        return {"level": level, "rows": ["".join(row) for row in await bridge.grid(level)]}


@app.post("/api/start")
async def api_start(body: Start | None = None):
    async with bridge.lock:
        return await bridge.start((body or Start()).seed)


@app.get("/api/state")
async def api_state():
    async with bridge.lock:
        return await bridge.snapshot() if bridge.game else await bridge.state()


@app.post("/api/act")
async def api_act(body: Act):
    async with bridge.lock:
        return await bridge.act(body.action, max(100, min(body.ms, 3000)))


@app.get("/api/actions")
async def api_actions():
    return {"actions": list(ACTIONS)}


@app.post("/api/eval")
async def api_eval(body: dict):
    """Debug helper: run a JS function (as a string) in the game page."""
    async with bridge.lock:
        return {"result": await bridge.js(body["js"])}


app.mount("/frames", StaticFiles(directory=str(FRAMES), check_dir=False), name="frames")
app.mount("/game-data", StaticFiles(directory=str(INSTALLER.parent), check_dir=False), name="game-data")
app.mount("/", StaticFiles(directory=str(SITE), html=True), name="site")

if __name__ == "__main__":
    FRAMES.mkdir(exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
