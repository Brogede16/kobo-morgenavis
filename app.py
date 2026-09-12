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
<style>body{font:17px system-ui;max-width:46rem;margin:4rem auto;padding:0 1rem;color:#16201c}button,select{padding:.55rem .8rem;font-size:1rem}.result,.note{padding:1rem;background:#eef8f0}.note{border-left:4px solid #5e897d}.article{border-top:1px solid #ddd;padding:1rem 0}.article p{margin:.35rem 0}.why{color:#31584d}.actions{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.7rem}.less{background:#fff;border:1px solid #999}details{margin:1.5rem 0;font-size:.9rem}.ok{color:#17663c}.error{color:#9d2831}</style>
<h1>Mads Morgen</h1><p>Næste planlagte udgave: {{ next_run }}</p>
{% if result %}<p class=result>{{ result }}</p>{% endif %}
<form method=post action=\"{{ url_for('run_from_page') }}\"><p>En manuel kørsel bruger også den automatiske websøgning.</p><button>Lav og send avis nu</button></form>
{% if edition %}<h2>Seneste udgave</h2><p>Fortæl gerne indimellem, hvad du vil have mere eller mindre af. Det bruges i næste udvælgelse.</p>
{% if edition.report and edition.report.editor_note %}<p class=note><strong>Redaktørens note</strong><br>{{ edition.report.editor_note }}</p>{% endif %}
{% if not feedback_enabled %}<p class=note><strong>Feedback er ikke aktiv endnu.</strong><br>Tilføj eller kontrollér GitHub-feedbackforbindelsen i Render, så knapperne kan gemme dine valg.</p>{% endif %}
{% for article in edition.articles %}<article class=article><p><small>{{ article.section }} · {{ article.reading_minutes }} min.</small></p><a href=\"{{ article.url }}\" target=\"_blank\" rel=\"noreferrer\"><strong>{{ article.title }}</strong></a><p>{{ article.source }} · {{ article.summary }}</p><p class=why><strong>Kort fortalt:</strong> {{ article.why }}</p>
{% if feedback_enabled %}<form class=actions method=post action=\"{{ url_for('article_feedback') }}\"><input type=hidden name=url value=\"{{ article.url }}\"><select name=reason aria-label=\"Hvorfor?\"><option value=\"\">Valgfrit: hvorfor?</option><option value=\"great_match\">Godt emne og vinkel</option><option value=\"great_depth\">God dybde</option><option value=\"surprising\">Overraskende fed</option><option value=\"uninteresting\">Uinteressant</option><option value=\"too_generic\">For generisk verdensnyhed</option><option value=\"unclear\">For svært eller uklart skrevet</option><option value=\"good_but_too_technical\">God, men for nørdet</option><option value=\"too_thin\">For tynd</option><option value=\"too_long\">For lang eller kedelig</option><option value=\"too_promotional\">For meget PR</option><option value=\"too_old\">For gammel</option><option value=\"duplicate\">Gentagelse</option></select><button name=direction value=more>Mere af den slags</button><button class=less name=direction value=less>Mindre af den slags</button></form>{% endif %}</article>
{% endfor %}{% if edition.report and edition.report.source_health %}<details><summary>Kildestatus for denne udgave</summary><ul>{% for name, state in edition.report.source_health.items() %}<li class=\"{{ 'ok' if state.status == 'ok' else 'error' }}\">{{ name }}: {{ state.status }}{% if state.status == 'ok' %} ({{ state.items }} fund){% endif %}</li>{% endfor %}</ul></details>{% endif %}
{% elif not feedback_enabled %}<p><em>Feedback vises her, når GitHub-feedbacknøglen er sat op, og den næste udgave er lavet.</em></p>{% endif %}
<p>Den færdige EPUB lægges i Google Drive-mappen Rakuten Kobo.</p></html>"""

PUBLIC_PAGE_STYLE = """<style>body{font:17px system-ui;max-width:46rem;margin:4rem auto;padding:0 1rem;color:#16201c;line-height:1.55}a{color:#31584d}</style>"""
PRIVACY_PAGE = f"""<!doctype html><html lang=\"da\"><meta charset=\"utf-8\"><title>Privatliv – Mads Morgen</title>{PUBLIC_PAGE_STYLE}
<h1>Privatliv</h1><p>Mads Morgen er en personlig, privat morgenavis til én bruger. Den indsamler offentligt tilgængelige nyhedslinks og RSS-data, skaber en EPUB og lægger den i brugerens valgte Google Drive-mappe.</p><p>Google Drive-adgangen bruges kun til at oprette, læse og slette de EPUB-filer, som Mads Morgen selv har oprettet. Appen læser ikke andre Drive-filer.</p><p>Artikel-feedback gemmes i et privat GitHub-repository for at forbedre fremtidige udvælgelser. Data sælges ikke og deles ikke med andre.</p><p>Spørgsmål: <a href=\"mailto:madsbh@me.com\">madsbh@me.com</a></p>"""
TERMS_PAGE = f"""<!doctype html><html lang=\"da\"><meta charset=\"utf-8\"><title>Vilkår – Mads Morgen</title>{PUBLIC_PAGE_STYLE}
<h1>Vilkår</h1><p>Mads Morgen er en privat, personlig automatisering. Den bruges på ejerens eget ansvar og er ikke en offentlig nyhedstjeneste.</p><p>Artikler og billeder tilhører deres respektive udgivere. EPUB’en er alene til personlig læsning; den må ikke videredistribueres.</p><p>Brugeren kan til enhver tid tilbagekalde Google Drive-adgangen fra sin Google-konto.</p><p>Spørgsmål: <a href=\"mailto:madsbh@me.com\">madsbh@me.com</a></p>"""


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

    def next_run():
        job = scheduler.get_job("morning-edition")
        return getattr(job, "next_run_time", None) or "ukendt"

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", next_run=str(next_run()))

    @app.get("/privacy")
    def privacy():
        return PRIVACY_PAGE

    @app.get("/terms")
    def terms():
        return TERMS_PAGE

    @app.get("/")
    @page_auth
    def home():
        try:
            edition = latest_edition()
        except Exception:
            logger.exception("Could not load latest feedback edition")
            edition = None
        return render_template_string(PAGE, next_run=next_run(), result=None,
                                      edition=edition, feedback_enabled=feedback_configured())

    @app.post("/run")
    @page_auth
    def run_from_page():
        result = run_edition(settings, use_web_search=True)
        sync_note = " Feedback er klar." if result.get("github_saved") else " Avisen er sendt, men GitHub-feedback blev ikke gemt; kontrollér GitHub-feedbackforbindelsen i Render."
        return render_template_string(PAGE, next_run=next_run(),
                                      result=f"Færdig: {result['articles']} historier er sendt til Drive." + sync_note,
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
        message = "Gemte din feedback i GitHub." if saved else "GitHub-feedback er ikke sat op endnu."
        return render_template_string(PAGE, next_run=next_run(), result=message,
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
