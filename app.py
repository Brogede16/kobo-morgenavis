"""HTTP shell and in-process scheduler for the Kobo morning newspaper."""
import logging
import os
from functools import wraps
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for
from zoneinfo import ZoneInfo

from morning_news import load_settings, run_edition

load_dotenv()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PAGE = """<!doctype html><html lang=\"da\"><meta charset=\"utf-8\"><title>Mads Morgen</title>
<style>body{font:17px system-ui;max-width:42rem;margin:4rem auto;padding:0 1rem}button{padding:.7rem 1rem;font-size:1rem}label{display:block;margin:1rem 0}.result{padding:1rem;background:#eef8f0}</style>
<h1>Mads Morgen</h1><p>Næste planlagte udgave: {{ next_run }}</p>
{% if result %}<p class=result>{{ result }}</p>{% endif %}
<form method=post action=\"{{ url_for('run_from_page') }}\"><label><input type=checkbox name=web_search> Søg også på nettet denne ene gang</label><button>Lav og send avis nu</button></form>
<p>Den færdige EPUB lægges i Google Drive-mappen Rakuten Kobo.</p></html>"""


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

    def page_auth(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            password = os.environ.get("ADMIN_PASSWORD")
            if not password:
                return Response("ADMIN_PASSWORD mangler", 503)
            auth = request.authorization
            username = os.environ.get("ADMIN_USERNAME", "mads")
            if not auth or auth.username != username or auth.password != password:
                return Response("Login påkrævet", 401, {"WWW-Authenticate": 'Basic realm="Mads Morgen"'})
            return handler(*args, **kwargs)
        return wrapped

    @app.get("/healthz")
    def healthz():
        job = scheduler.get_job("morning-edition")
        return jsonify(status="ok", next_run=str(job.next_run_time) if job else None)

    @app.get("/")
    @page_auth
    def home():
        job = scheduler.get_job("morning-edition")
        return render_template_string(PAGE, next_run=job.next_run_time if job else "ukendt", result=None)

    @app.post("/run")
    @page_auth
    def run_from_page():
        result = run_edition(settings, use_web_search=bool(request.form.get("web_search")))
        return render_template_string(PAGE, next_run=scheduler.get_job("morning-edition").next_run_time,
                                      result=f"Færdig: {result['articles']} historier er sendt til Drive.")

    @app.post("/run-now")
    def run_now():
        # Intended for Render manual testing. Protect it when RUN_NOW_TOKEN is configured.
        from flask import request
        token = os.environ.get("RUN_NOW_TOKEN")
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            return jsonify(error="unauthorized"), 401
        result = run_edition(settings, use_web_search=request.args.get("web_search") == "true")
        return jsonify(result)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
