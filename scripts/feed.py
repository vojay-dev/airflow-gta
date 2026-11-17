"""Live move feed for Airflow plays GTA2: one colored line per move of the latest run.

Run with `make feed`. Standard library only, talks to the local Airflow API as admin/admin.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("AIRFLOW_URL", "http://localhost:8080")
DAG = "airflow_plays_gta2"
TASK = "turn.drive"
ARROWS = {
    "forward": "↑", "forward_left": "↖", "forward_right": "↗", "reverse": "↓",
    "reverse_left": "↙", "reverse_right": "↘", "handbrake_left": "↺", "handbrake_right": "↻",
}
RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
YELLOW, PURPLE, RED, GREEN, CYAN = "\033[33m", "\033[35m", "\033[31m", "\033[32m", "\033[36m"


def token() -> str:
    req = urllib.request.Request(
        f"{BASE}/auth/token",
        json.dumps({"username": "admin", "password": "admin"}).encode(),
        {"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))["access_token"]


def get(path: str, tok: str):
    req = urllib.request.Request(f"{BASE}/api/v2{path}", headers={"Authorization": f"Bearer {tok}"})
    return json.load(urllib.request.urlopen(req))


def line(frame: dict) -> str:
    action = frame["last_action"] or "?"
    cars = f"{RED}{'🚗' * frame['cars']}{RESET}"
    event = frame["event"] or ""
    color = RED if "Lost" in event or "Wasted" in event else GREEN if "Delivered" in event else DIM
    dist = frame["route_distance"]
    return (
        f"{DIM}move{RESET} {BOLD}{frame['move']:>3}{RESET}  "
        f"{PURPLE}{BOLD}{ARROWS.get(action, '?')} {action:<15}{RESET} "
        f"{DIM}moved{RESET} {frame['moved']:>4.1f}  "
        f"{YELLOW}{BOLD}{dist if dist is not None else '?':>5}{RESET} {DIM}blocks to go{RESET}  "
        f"📦 {frame['deliveries']}  {cars}  {color}{event}{RESET}"
    )


def main() -> None:
    tok = token()
    run_id, printed = None, 0
    print(f"{BOLD}Airflow plays GTA2{RESET} {DIM}(waiting for a run of {DAG}, Ctrl+C to quit){RESET}")
    while True:
        try:
            runs = get(f"/dags/{DAG}/dagRuns?order_by=-run_after&limit=1", tok)["dag_runs"]
            if runs and runs[0]["dag_run_id"] != run_id:
                run_id, printed = runs[0]["dag_run_id"], 0
                print(f"\n{BOLD}🚦 {run_id}{RESET}")
            if run_id:
                run = f"/dags/{DAG}/dagRuns/{urllib.parse.quote(run_id, safe='')}"
                entries = get(
                    f"{run}/taskInstances/{TASK}/xcomEntries?xcom_key=return_value&order_by=region_index&limit=1000",
                    tok,
                )["xcom_entries"]
                for entry in entries[printed:]:
                    frame = get(
                        f"{run}/taskInstances/{TASK}/xcomEntries/return_value?map_index=-1"
                        f"&region_id={entry['region_id']}&region_index={entry['region_index']}",
                        tok,
                    )["value"]
                    print(line(frame), flush=True)
                    printed += 1
                    if frame["status"] != "playing":
                        done = f"{GREEN}ALL DELIVERED" if frame["status"] == "won" else f"{RED}WASTED"
                        print(f"{BOLD}{done}{RESET} {DIM}after {frame['move']} moves{RESET}")
            time.sleep(0.5)
        except urllib.error.HTTPError as err:
            if err.code == 401:
                tok = token()
            else:
                raise
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()
