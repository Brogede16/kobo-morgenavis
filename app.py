"""HTTP shell and in-process scheduler for the Kobo morning newspaper."""
import logging
import os
import hmac
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template_string, request
from zoneinfo import ZoneInfo

from feedback_store import add_feedback, configured as feedback_configured, latest_edition
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
<form method=post action=\"{{ url_for('run_from_page') }}\"><p>En manuel kørsel bruger også den automatiske websøgning.</p><button>Lav og send avis nu</button></form>
{% if edition %}<h2>Seneste udgave</h2><p>Fortæl gerne indimellem, hvad du vil have mere eller mindre af. Det bruges i næste udvælgelse.</p>
{% for article in edition.articles %}<article class=article><a href=\"{{ article.url }}\" target=\"_blank\" rel=\"noreferrer\"><strong>{{ article.title }}</strong></a><p>{{ article.source }} · {{ article.summary }}</p>
{% if feedback_enabled %}<form class=actions method=post action=\"{{ url_for('article_feedback') }}\"><input type=hidden name=url value=\"{{ article.url }}\"><select name=reason aria-label=\"Hvorfor?\"><option value=\"\">Valgfrit: hvorfor?</option><option value=\"great_match\">Godt emne og vinkel</option><option value=\"great_depth\">God dybde</option><option value=\"surprising\">Overraskende fed</option><option value=\"uninteresting\">Uinteressant</option><option value=\"good_but_too_technical\">God, men for nørdet</option><option value=\"too_thin\">For tynd</option><option value=\"too_long\">For lang eller kedelig</option><option value=\"too_promotional\">For meget PR</option><option value=\"too_old\">For gammel</option><option value=\"duplicate\">Gentagelse</option></select><button name=direction value=more>Mere af den slags</button><button class=less name=direction value=less>Mindre af den slags</button></form>{% endif %}</article>{% endfor %}
{% elif not feedback_enabled %}<p><em>Feedback vises her, når GitHub-feedbacknøglen er sat op, og den næste udgave er lavet.</em></p>{% endif %}
<p>Den færdige EPUB lægges i Google Drive-mappen Rakuten Kobo.</p></html>"""


def create_app(start_scheduler=True):
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
    if start_scheduler:
        scheduler.start()

    def page_auth(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            password = os.environ.get("ADMIN_PASSWORD")
            if not password:
                return Response("ADMIN_PASSWORD mangler", 503)
            auth = request.authorization
            username = os.environ.get("ADMIN_USERNAME", "mads")
            if not auth or not hmac.compare_digest(auth.username, username) or not hmac.compare_digest(auth.password, password):
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
        try:
            edition = latest_edition()
        except Exception:
            logger.exception("Could not load latest feedback edition")
            edition = None
        return render_template_string(PAGE, next_run=job.next_run_time if job else "ukendt", result=None,
                                      edition=edition, feedback_enabled=feedback_configured())

    @app.post("/run")
    @page_auth
    def run_from_page():
        result = run_edition(settings, use_web_search=True)
        return render_template_string(PAGE, next_run=scheduler.get_job("morning-edition").next_run_time,
                                      result=f"Færdig: {result['articles']} historier er sendt til Drive.",
                                      edition=latest_edition(), feedback_enabled=feedback_configured())

    @app.post("/feedback")
    @page_auth
    def article_feedback():
        edition = latest_edition() or {}
        url = request.form.get("url", "")
        article = next((dict(item) for item in edition.get("articles", []) if item.get("url") == url), None)
        if not article:
            return Response("Artiklen findes ikke i seneste udgave", 400)
        article["reason"] = request.form.get("reason", "")
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
        token = os.environ.get("RUN_NOW_TOKEN")
        if not token:
            return jsonify(error="RUN_NOW_TOKEN is not configured"), 503
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
            return jsonify(error="unauthorized"), 401
        result = run_edition(settings, use_web_search=request.args.get("web_search", "true") == "true")
        return jsonify(result)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
