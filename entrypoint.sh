#!/bin/bash
if [ "$ENV" = "dev" ]; then
    echo "Starting in debug mode..."
    python -m debugpy --wait-for-client --listen 0.0.0.0:12345 -m notion_automation --reload $@
else
    python -m notion_automation $@
fi
