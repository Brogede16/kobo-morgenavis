"""HTTP shell and in-process scheduler for the Kobo morning newspaper."""
import logging
import os
from functools import wraps
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for
from zoneinfo import ZoneInfo

from feedback_store import add_feedback, configured as feedback_configured, latest_edition, record_edition
from morning_news import load_settings, run_edition

load_dotenv()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PAGE = """<!doctype html><html lang=\"da\"><meta charset=\"utf-8\"><title>Mads Morgen</title>
<style>body{font:17px system-ui;max-width:46rem;margin:4rem auto;padding:0 1rem}button,select{padding:.55rem .8rem;font-size:1rem}label{display:block;margin:1rem 0}.result{padding:1rem;background:#eef8f0}.article{border-top:1px solid #ddd;padding:1rem 0}.article p{margin:.35rem 0}.actions{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.7rem}.less{background:#fff;border:1px solid #999}</style>
<h1>Mads Morgen</h1><p>Næste planlagte udgave: {{ next_run }}</p>
{% if result %}<p class=result>{{ result }}</p>{% endif %}
<form method=post action=\"{{ url_for('run_from_page') }}\"><label><input type=checkbox name=web_search> Søg også på nettet denne ene gang</label><button>Lav og send avis nu</button></form>
{% if edition %}<h2>Seneste udgave</h2><p>Fortæl gerne indimellem, hvad du vil have mere eller mindre af. Det bruges i næste udvælgelse.</p>
{% for article in edition.articles %}<article class=article><a href=\"{{ article.url }}\" target=\"_blank\" rel=\"noreferrer\"><strong>{{ article.title }}</strong></a><p>{{ article.source }} · {{ article.summary }}</p>
{% if feedback_enabled %}<form class=actions method=post action=\"{{ url_for('article_feedback') }}\"><input type=hidden name=title value=\"{{ article.title }}\"><input type=hidden name=source value=\"{{ article.source }}\"><input type=hidden name=url value=\"{{ article.url }}\"><input type=hidden name=summary value=\"{{ article.summary }}\"><select name=reason aria-label=\"Hvorfor?\"><option value=\"\">Valgfrit: hvorfor?</option><option value=\"great_topic\">Fedt emne</option><option value=\"great_angle\">God vinkel</option><option value=\"great_depth\">God dybde</option><option value=\"uninteresting\">Uinteressant</option><option value=\"good_but_too_technical\">God, men for nørdet</option><option value=\"too_thin\">For tynd</option><option value=\"wrong_angle\">Forkert vinkel</option></select><button name=direction value=more>Mere af den slags</button><button class=less name=direction value=less>Mindre af den slags</button></form>{% endif %}</article>{% endfor %}
{% elif not feedback_enabled %}<p><em>Feedback vises her, når GitHub-feedbacknøglen er sat op, og den næste udgave er lavet.</em></p>{% endif %}
<p>Den færdige EPUB lægges i Google Drive-mappen Rakuten Kobo.</p></html>"""


def create_app():
    app = Flask(__name__)
    settings = load_settings()
    timezone = ZoneInfo(settings["edition"]["timezone"])
    scheduler = BackgroundScheduler(timezone=timezone)

    def save_feedback_edition(result):
        """A GitHub feedback outage must never block delivery to Kobo."""
        try:
            record_edition(result["article_list"])
        except Exception:
            logger.exception("Could not record edition for feedback")

    def scheduled_run():
        try:
            result = run_edition(settings)
            save_feedback_edition(result)
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
        return render_template_string(PAGE, next_run=job.next_run_time if job else "ukendt", result=None,
                                      edition=latest_edition(), feedback_enabled=feedback_configured())

    @app.post("/run")
    @page_auth
    def run_from_page():
        result = run_edition(settings, use_web_search=bool(request.form.get("web_search")))
        save_feedback_edition(result)
        return render_template_string(PAGE, next_run=scheduler.get_job("morning-edition").next_run_time,
                                      result=f"Færdig: {result['articles']} historier er sendt til Drive.",
                                      edition=latest_edition(), feedback_enabled=feedback_configured())

    @app.post("/feedback")
    @page_auth
    def article_feedback():
        article = {key: request.form.get(key, "") for key in ("title", "source", "url", "summary", "reason")}
        try:
            saved = add_feedback(article, request.form.get("direction", ""))
        except ValueError:
            return Response("Ugyldig feedback", 400)
        job = scheduler.get_job("morning-edition")
        message = "Gemte din feedback i GitHub." if saved else "GitHub-feedback er ikke sat op endnu."
        return render_template_string(PAGE, next_run=job.next_run_time if job else "ukendt", result=message,
                                      edition=latest_edition(), feedback_enabled=feedback_configured())

    @app.post("/run-now")
    def run_now():
        # Intended for Render manual testing. Protect it when RUN_NOW_TOKEN is configured.
        from flask import request
        token = os.environ.get("RUN_NOW_TOKEN")
        if token and request.headers.get("Authorization") != f"Bearer {token}":
            return jsonify(error="unauthorized"), 401
        result = run_edition(settings, use_web_search=request.args.get("web_search") == "true")
        save_feedback_edition(result)
        return jsonify(result)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
