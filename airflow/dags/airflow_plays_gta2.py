"""Airflow plays GTA2: a local decision model drives a car through Downtown, one move per loop iteration.

The game runs in a Chrome window on the host (the GTA2 browser port, driven by ../bridge).
The bridge keeps it paused between moves, so the Dag can take its time to think.

Airflow 3.4 features on show:
- Task loops: the `turn` group repeats until `game_over` stops it.
- Common AI `@task.llm` on a decision model (Ollama `nimble` over the System One API).
- Task colors: `ui_color` / `ui_fgcolor` on tasks and the task group.
"""

from typing import Literal

from pydantic import BaseModel, Field

from airflow.sdk import LoopDecision, Param, dag, task, task_group
from include.gta2 import bridge

MAX_MOVES = 300

STRATEGY = (
    "You drive a car through a city and deliver packages. Each line says what one move would do. "
    "Never pick a move marked DANGER. Prefer the move that gets the most blocks closer to the drop-off. "
    "If the car is STUCK, back up or turn instead of pushing forward."
)


class Drive(BaseModel):
    """Get the package to the drop-off without wrecking the car."""

    action: Literal[
        "forward",
        "forward_left",
        "forward_right",
        "reverse",
        "reverse_left",
        "reverse_right",
        "handbrake_left",
        "handbrake_right",
    ] = Field(description="Which move should the car make next?")


def colored(xcom_arg, bg: str, fg: str = "#000000"):
    xcom_arg.operator.ui_color = bg
    xcom_arg.operator.ui_fgcolor = fg
    return xcom_arg


def game_over(*, loop) -> LoopDecision:
    """The loop gate: stop when all packages are delivered, the cars are gone, or time is up."""
    game = loop.result
    if game["status"] == "won":
        return LoopDecision(stop=True, reason=f"All packages delivered in {game['move']} moves.")
    if game["status"] == "lost":
        return LoopDecision(stop=True, reason=f"Wasted after {game['move']} moves, {game['deliveries']} delivered.")
    if game["move"] >= MAX_MOVES - 1:
        return LoopDecision(stop=True, reason=f"Out of time, {game['deliveries']} delivered.")
    return LoopDecision(
        stop=False,
        reason=f"Move {game['move']}: {game['last_action']}, {game['route_distance']} blocks to go. {game['event']}".strip(),
    )


@dag(
    schedule=None,
    max_active_runs=1,
    params={"seed": Param(7, type="integer", description="Seeds the choice of drop-offs.")},
    tags=["gta2"],
)
def airflow_plays_gta2():

    @task
    def insert_coin(*, params):
        game = bridge.start(params["seed"])
        print(game["event"])
        return game

    @task_group(ui_color="#7c2d12", ui_fgcolor="#ffffff")
    def turn(game):

        @task.llm(
            llm_conn_id="ollama_decisions",
            system_prompt=STRATEGY,
            output_type=Drive,
            serialize_output=True,
        )
        def decide(game, *, loop):
            return (loop.previous or game)["look"]

        @task
        def drive(decision):
            state = bridge.drive(decision["action"])
            print(
                f"Move {state['move']}: {decision['action']}, moved {state['moved']} blocks, "
                f"{state['route_distance']} to go. {state['event']}"
            )
            return state

        colored(drive(colored(decide(game), "#a78bfa")), "#facc15")

    @task(trigger_rule="all_done")
    def high_score(frames):
        final = frames[-1]
        print(f"Game {final['status']}: {final['deliveries']} delivered in {final['move']} moves, {final['cars']} cars left.")
        return {"status": final["status"], "moves": final["move"], "deliveries": final["deliveries"]}

    coin = colored(insert_coin(), "#fde047")
    frames = turn.partial(game=coin).loop(max_iterations=MAX_MOVES, until=game_over)
    colored(high_score(frames["drive"].output), "#f472b6")


airflow_plays_gta2()
