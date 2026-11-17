"""Checks the bridge without Airflow: starts a game and drives 60 moves with a simple scripted player.

The player takes the move with the biggest gain that is not marked DANGER, and backs up when stuck.
"""

import json
import urllib.request

BRIDGE = "http://127.0.0.1:8765"


def post(path: str, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(f"{BRIDGE}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


st = post("/api/start", {"seed": 7}, timeout=420)
print(st["event"])
for i in range(60):
    opts = st["options"]
    ok = {a: o for a, o in opts.items() if not o["blocked"]} or opts
    action = max(ok, key=lambda a: ok[a]["gain"])
    if "STUCK" in st["look"]:
        action = "reverse_left" if i % 2 else "reverse_right"
    st = post("/api/act", {"action": action})
    print(f"{st['move']:3} {action:16} moved={st['moved']:5} to go={st['route_distance']} "
          f"cars={st['cars']} delivered={st['deliveries']} {st['event']}")
    if st["status"] != "playing":
        print("STATUS", st["status"])
        break
