#!/bin/sh
# Dane trwałe: /config (w HA widoczne jako addon_configs/<slug>_diet_planner).
export DATA_DIR=/config
exec python3 /app/server.py --addon --port 8099
