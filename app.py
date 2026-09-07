"""HTTP shell and in-process scheduler for the Kobo morning newspaper."""
import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify
from zoneinfo import ZoneInfo

from morning_news import load_settings, run_edition

load_dotenv()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    settings = load_settings()
    timezone = ZoneInfo(settings["edition"]["timezone"])
    scheduler = BackgroundScheduler(timezone=timezone)

    def scheduled_run():
        try:
            run_edition(settings)
        except Exception:
            logger.exception("Morning edition failed")

    scheduler.add_job(
        scheduled_run,
        trigger="cron",
        id="morning-edition",
        replace_existing=True,
        **dict(zip(("minute", "hour", "day", "month", "day_of_week"), settings["edition"]["schedule"].split())),
    )
    # Flask's development reloader starts a second process; Render/Gunicorn does not.
    scheduler.start()

    @app.get("/")
    @app.get("/healthz")
    def healthz():
        job = scheduler.get_job("morning-edition")
        return jsonify(status="ok", next_run=str(job.next_run_time) if job else None)

    @app.post("/run-now")
    def run_now():
        # Intended for Render manual testing. Protect it when RUN_NOW_TOKEN is configured.
        from flask import request
        token = os.environ.get("RUN_NOW_TOKEN")
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            return jsonify(error="unauthorized"), 401
        result = run_edition(settings)
        return jsonify(result)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))

