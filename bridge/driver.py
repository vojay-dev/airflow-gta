"""What the bot sees: road distances to the target and a lookahead per action.

Pure Python on top of the street-level grid from gmp.py ('R' road, 'P' pavement,
'F' field, '#' blocked), so it can be tested without the game.

Heading: GTA2's rot runs 0..1440 for a full circle; 0 faces +y (south), 360 +x (east).
"""

from __future__ import annotations

import heapq
import math
import random

COST = {"R": 1.0, "P": 3.0, "F": 4.0}
UNREACHABLE = 10**9

# Rough handling per action, measured in the browser port at ~0.7 s slices:
# (direction of travel: 1 forward / -1 back, turn in degrees over the slice, distance factor)
MOTION = {
    "forward": (1, 0, 1.0),
    "forward_left": (1, 80, 0.8),
    "forward_right": (1, -80, 0.8),
    "reverse": (-1, 0, 0.5),
    "reverse_left": (-1, 60, 0.5),
    "reverse_right": (-1, -60, 0.5),
    "handbrake_left": (1, 130, 0.6),
    "handbrake_right": (1, -130, 0.6),
    "wait": (0, 0, 0.0),
}

# Fun names for the drop-off points: what Airflow is "delivering".
DELIVERIES = [
    "a late batch of CSV files",
    "the on-call pager",
    "a crate of retried tasks",
    "the missing XCom",
    "an urgent backfill",
    "a USB stick labelled 'prod_dump_final_v2'",
    "the Zombie Task's last will",
    "a box of unpinned dependencies",
]


def heading_vec(rot: float) -> tuple[float, float]:
    a = rot / 1440 * 2 * math.pi
    return math.sin(a), math.cos(a)


def blocks(n: float) -> str:
    n = round(n)
    return f"{n} block" if n == 1 else f"{n} blocks"


def compass(dx: float, dy: float) -> str:
    deg = (math.degrees(math.atan2(dx, -dy)) + 360) % 360  # 0 = north (-y), clockwise
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][round(deg / 45) % 8]


class Driver:
    def __init__(self, grid: list[list[str]], seed: int = 7):
        self.grid = grid
        self.size = len(grid)
        self.rng = random.Random(seed)
        self.target: tuple[int, int] | None = None
        self.target_name = ""
        self.dist: list[list[float]] = []

    def cell(self, x: float, y: float) -> str:
        ix, iy = int(math.floor(x)), int(math.floor(y))
        if 0 <= ix < self.size and 0 <= iy < self.size:
            return self.grid[iy][ix]
        return "#"

    def _dijkstra(self, start: tuple[int, int]) -> list[list[float]]:
        dist = [[float(UNREACHABLE)] * self.size for _ in range(self.size)]
        sx, sy = start
        dist[sy][sx] = 0.0
        heap = [(0.0, sx, sy)]
        while heap:
            d, x, y = heapq.heappop(heap)
            if d > dist[y][x]:
                continue
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < self.size and 0 <= ny < self.size:
                    c = COST.get(self.grid[ny][nx])
                    if c is None:
                        continue
                    nd = d + c
                    if nd < dist[ny][nx]:
                        dist[ny][nx] = nd
                        heapq.heappush(heap, (nd, nx, ny))
        return dist

    def pick_target(self, x: float, y: float, near: int = 35, far: int = 70) -> dict:
        """A road block between `near` and `far` road-blocks away from (x, y)."""
        from_car = self._dijkstra((int(x), int(y)))
        options = [
            (cx, cy)
            for cy in range(self.size)
            for cx in range(self.size)
            if self.grid[cy][cx] == "R" and near <= from_car[cy][cx] <= far
        ]
        if not options:
            options = [(cx, cy) for cy in range(self.size) for cx in range(self.size) if self.grid[cy][cx] == "R"]
        self.target = self.rng.choice(options)
        if not getattr(self, "names", None):
            self.names = self.rng.sample(DELIVERIES, len(DELIVERIES))  # no repeats within a game
        self.target_name = self.names.pop()
        self.dist = self._dijkstra(self.target)
        return {"x": self.target[0] + 0.5, "y": self.target[1] + 0.5, "name": self.target_name}

    def route_distance(self, x: float, y: float) -> float:
        if not self.dist:
            return float(UNREACHABLE)
        best = float(UNREACHABLE)
        # the car can sit on a building edge: use the best open neighbour
        for ox, oy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            ix, iy = int(math.floor(x)) + ox, int(math.floor(y)) + oy
            if 0 <= ix < self.size and 0 <= iy < self.size:
                best = min(best, self.dist[iy][ix] + (0 if (ox, oy) == (0, 0) else 1))
        return best

    def simulate(self, x: float, y: float, rot: float, action: str, reach: float) -> dict:
        """Where `action` would take the car, sampled every quarter block."""
        sign, turn, factor = MOTION[action]
        length = reach * factor
        steps = max(1, int(length / 0.25))
        px, py, r = x, y, rot
        surfaces = set()
        for i in range(1, steps + 1):
            r = rot + turn * 4 * (i / steps)  # degrees -> rot units (4 per degree)
            hx, hy = heading_vec(r)
            px, py = px + sign * hx * 0.25, py + sign * hy * 0.25
            c = self.cell(px, py)
            if c == "#":
                return {"blocked_after": round((i - 1) * 0.25, 1), "x": px, "y": py, "surfaces": surfaces}
            surfaces.add(c)
        return {"blocked_after": None, "x": px, "y": py, "surfaces": surfaces}

    def look(self, state: dict, last_moved: float, last_action: str | None) -> dict:
        """Text for the decision model plus the numbers behind it."""
        x, y, rot = state["x"], state["y"], state["rot"]
        here = self.route_distance(x, y)
        tx, ty = self.target if self.target else (x, y)
        hx, hy = heading_vec(rot)
        # how far one move gets: a bit more than the last one, between 1.5 and 5 blocks
        reach = min(5.0, max(1.5, last_moved * 1.2 + 1.0))
        stuck = last_action is not None and last_action != "wait" and last_moved < 0.3
        # Moves that just failed to move the car: the map does not show everything (posts, other cars),
        # so trust what happened over the simulation until the car moves again.
        if not hasattr(self, "failed"):
            self.failed: set[str] = set()
        if stuck:
            self.failed.add(str(last_action))
        elif last_moved >= 0.3:
            self.failed.clear()
        lines = [
            f"You drive a car through Anywhere City, delivering {self.target_name}.",
            f"The drop-off is {blocks(here)} away by road, to the {compass(tx + 0.5 - x, ty + 0.5 - y)}. "
            f"You face {compass(hx, hy)}.",
        ]
        if stuck:
            lines.append(f"STUCK: the last move ({last_action}) barely moved the car. Something is in the way. Back up or turn.")
        options = {}
        for action in MOTION:
            if action == "wait":
                continue
            sim = self.simulate(x, y, rot, action, reach)
            after = self.route_distance(sim["x"], sim["y"])
            gain = here - after
            parts = []
            if action in self.failed:
                parts.append("DANGER, tried this and the car did not move")
                sim["blocked_after"] = 0.0
            elif sim["blocked_after"] is not None:
                parts.append(f"DANGER, crashes into a wall after {sim['blocked_after']} blocks")
            else:
                parts.append("clear")
            if after >= UNREACHABLE:
                parts.append("ends off the road network")
            elif gain > 0.5:
                parts.append(f"gets {blocks(gain)} closer to the drop-off")
            elif gain < -0.5:
                parts.append(f"gets {blocks(-gain)} further from the drop-off")
            else:
                parts.append("about as far from the drop-off as now")
            if sim["surfaces"] and sim["surfaces"] <= {"P", "F"}:
                parts.append("drives on the pavement")
            options[action] = {"gain": round(gain, 1), "blocked": sim["blocked_after"] is not None}
            lines.append(f"{action}: " + ", ".join(parts) + ".")
        return {"text": "\n".join(lines), "route_distance": round(here, 1), "stuck": stuck, "options": options}
