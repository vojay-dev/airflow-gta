MODEL ?= nimble
SEED ?= 7
AIRFLOW := http://localhost:8080
URL := $(AIRFLOW)/dags/airflow_plays_gta2
BRIDGE := http://127.0.0.1:8765
INSTALLER := game-data/GTA2INSTALLER.exe
INSTALLER_SIZE := 362331961

.DEFAULT_GOAL := help
.PHONY: help start stop status bridge bridge-stop trigger feed play-test logs game-data clean-frames

## help: this list
help:
	@grep -E '^## ' Makefile | sed 's/^## /  make /'

## start: everything: Ollama + model, the GTA2 bridge (opens Chrome), Airflow
start: bridge
	@if ! curl -sf localhost:11434/api/version >/dev/null; then \
		echo "Starting Ollama"; \
		nohup ollama serve > .ollama.log 2>&1 & echo $$! > .ollama.pid; \
		for i in $$(seq 1 30); do curl -sf localhost:11434/api/version >/dev/null && break; sleep 1; done; \
	fi
	ollama pull $(MODEL)
	@test -f airflow/.env || { cp airflow/.env.example airflow/.env; echo "Created airflow/.env from airflow/.env.example"; }
	cd airflow && astro dev start --no-browser
	@echo "\nReady: $(URL)  (admin / admin; use localhost:8080, not the proxy URL)"
	@echo "Trigger with 'make trigger', watch Chrome, open the run's GTA2 Dashcam tab."

## stop: everything: Airflow, the bridge and its Chrome, and Ollama if `make start` started it
stop: bridge-stop
	cd airflow && astro dev stop
	@if [ -f .ollama.pid ]; then kill $$(cat .ollama.pid) 2>/dev/null || true; rm -f .ollama.pid .ollama.log; echo "Stopped Ollama"; fi

## status: what is running
status:
	@curl -sf $(BRIDGE)/api/actions >/dev/null && echo "bridge:  up ($(BRIDGE))" || echo "bridge:  down"
	@curl -sf localhost:11434/api/version >/dev/null && echo "ollama:  up" || echo "ollama:  down"
	@curl -sf $(AIRFLOW)/api/v2/monitor/health >/dev/null && echo "airflow: up ($(URL))" || echo "airflow: down"

## bridge: only the GTA2 bridge with its Chrome window (log in .bridge.log)
bridge:
	@test -f $(INSTALLER) || { echo "Missing $(INSTALLER). Run 'make game-data' first (see README)."; exit 1; }
	@if curl -sf $(BRIDGE)/api/actions >/dev/null; then echo "Bridge already running"; else \
		echo "Starting the GTA2 bridge"; \
		nohup uv run bridge/bridge.py > .bridge.log 2>&1 & \
		for i in $$(seq 1 60); do curl -sf $(BRIDGE)/api/actions >/dev/null && break; sleep 1; done; \
	fi

## bridge-stop: close the bridge and its Chrome window (force-kills if it hangs)
bridge-stop:
	@# [b] keeps the patterns from matching this recipe's own shell
	@pkill -f '[b]ridge/bridge.py' 2>/dev/null && echo "Stopping the bridge" || true
	@for i in $$(seq 1 20); do pgrep -f '[b]ridge/bridge.py' >/dev/null || break; sleep 0.5; done
	@pkill -9 -f '[b]ridge/bridge.py' 2>/dev/null || true
	@pkill -f '[a]irflow-gta/.chrome-profile' 2>/dev/null || true

## trigger: start a run (SEED=7 by default, picks the drop-offs)
trigger:
	@TOK=$$(curl -sf -X POST $(AIRFLOW)/auth/token -H 'Content-Type: application/json' \
		-d '{"username":"admin","password":"admin"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])') && \
	curl -sf -H "Authorization: Bearer $$TOK" -X PATCH $(AIRFLOW)/api/v2/dags/airflow_plays_gta2 \
		-H 'Content-Type: application/json' -d '{"is_paused":false}' >/dev/null && \
	curl -sf -H "Authorization: Bearer $$TOK" -X POST $(AIRFLOW)/api/v2/dags/airflow_plays_gta2/dagRuns \
		-H 'Content-Type: application/json' -d '{"logical_date":null,"conf":{"seed":$(SEED)}}' \
		| python3 -c 'import json,sys;d=json.load(sys.stdin);print("Triggered", d["dag_run_id"])'

## feed: one line per move of the latest run, live (Ctrl+C to quit)
feed:
	@python3 scripts/feed.py

## play-test: no Airflow: a scripted player drives 60 moves (checks the bridge)
play-test: bridge
	@python3 scripts/play_test.py

## logs: the bridge log
logs:
	@tail -n 40 -f .bridge.log

## game-data: download the GTA2 freeware installer (Internet Archive upload, see README) and check its size
game-data:
	@mkdir -p game-data
	@if [ -f $(INSTALLER) ]; then echo "$(INSTALLER) already there"; else \
		curl -L --fail -o $(INSTALLER).part https://archive.org/download/gta2_rockstar-classics/GTA2INSTALLER.exe && \
		mv $(INSTALLER).part $(INSTALLER); fi
	@test "$$(stat -f %z $(INSTALLER))" = "$(INSTALLER_SIZE)" && echo "Size ok: $(INSTALLER_SIZE) bytes" || { echo "Wrong size, delete $(INSTALLER) and retry"; exit 1; }

## clean-frames: delete saved screenshots (old runs lose their dashcam images)
clean-frames:
	rm -rf frames/2*
