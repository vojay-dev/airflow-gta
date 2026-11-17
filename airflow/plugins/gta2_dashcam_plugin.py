"""Adds a "GTA2 Dashcam" tab to every run of Dags tagged gta2: live while it runs, a replay afterwards.

The tab reads the run's XComs through /api/v2 (with the UI's session cookie). Screenshots and
the road grid come straight from the GTA2 bridge on the host (http://127.0.0.1:8765), which
the browser can reach because it runs on the same machine.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from airflow.plugins_manager import AirflowPlugin

URL_PREFIX = "/gta2-dashcam"

app = FastAPI()
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "gta2_dashcam_static"), name="gta2_dashcam")


class Gta2DashcamPlugin(AirflowPlugin):
    name = "gta2_dashcam"
    fastapi_apps = [{"app": app, "url_prefix": URL_PREFIX, "name": "GTA2 Dashcam static files"}]
    react_apps = [
        {
            "name": "GTA2 Dashcam",
            "bundle_url": f"{URL_PREFIX}/static/gta2_dashcam.js",
            "destination": "dag_run",
            "url_route": "gta2-dashcam",
            "applies_to": {"dag.tags.name": ["gta2"]},
        },
    ]
