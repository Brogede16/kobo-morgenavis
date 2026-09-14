"""HTTP shell and in-process scheduler for the Kobo morning newspaper."""
import logging
import os
import hmac
import threading
from datetime import datetime, timedelta
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for
from zoneinfo import ZoneInfo

from feedback_store import add_feedback, configured as feedback_configured, latest_edition
from morning_news import cron_fields, load_settings, run_edition

load_dotenv()
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PAGE = """<!doctype html><html lang=\"da\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Mads Morgen</title>
<style>body{font:17px system-ui;max-width:48rem;margin:3rem auto;padding:0 1rem;color:#16201c;background:#f7f5ef;line-height:1.5}button,select{padding:.6rem .8rem;font-size:1rem;border-radius:.35rem}.result,.note{padding:1rem;background:#e7f1eb;border-radius:.4rem}.note{border-left:4px solid #5e897d}.article{background:#fff;border:1px solid #dedbd2;border-radius:.55rem;padding:1rem;margin:1rem 0}.article p{margin:.35rem 0}.article a{color:#123f38}.why{color:#31584d}.actions{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.8rem}.actions button{background:#315f55;color:#fff;border:1px solid #315f55}.actions .less{background:#fff;color:#263630;border:1px solid #888}details{margin:1.5rem 0;font-size:.9rem}.ok{color:#17663c}.error{color:#9d2831}</style>
<h1>Mads Morgen</h1><p>Næste planlagte udgave: {{ next_run }}</p>
{% if result %}<p class=result>{{ result }}</p>{% endif %}
{% if state.running %}<p class=note><strong>Kørsel i gang</strong> siden {{ state.started_at }}. Genindlæs siden om et par minutter.</p>
{% elif state.error %}<p class=\"note error\"><strong>Seneste kørsel fejlede</strong> {{ state.finished_at }}<br>{{ state.error }}</p>
{% elif state.finished_at %}<p class=note><strong>Seneste kørsel</strong> {{ state.finished_at }}: {{ state.articles }} historier{% if state.drive %}, sendt til Drive{% endif %}.</p>{% endif %}
<form method=post action=\"{{ url_for('run_from_page') }}\"><p>Avisen sendes automatisk hver morgen. Knappen her er til test.</p><button>Lav og send avis nu</button></form>
{% if edition %}<h2>Seneste udgave</h2><p>Fortæl gerne indimellem, hvad du vil have mere eller mindre af. Det bruges i næste udvælgelse.</p>
{% if edition.report and edition.report.editor_note %}<p class=note><strong>Redaktørens note</strong><br>{{ edition.report.editor_note }}</p>{% endif %}
{% if not feedback_enabled %}<p class=note><strong>Feedback er ikke aktiv endnu.</strong><br>Tilføj eller kontrollér GitHub-feedbackforbindelsen i Render, så knapperne kan gemme dine valg.</p>{% endif %}
{% for article in edition.articles %}<article class=article id=\"article-{{ loop.index }}\"><p><small>{{ article.section }} · {{ article.reading_minutes }} min.</small></p><a href=\"{{ article.url }}\" target=\"_blank\" rel=\"noreferrer\"><strong>{{ article.title }}</strong></a><p>{{ article.source }} · {{ article.summary }}</p><p class=why><strong>Kort fortalt:</strong> {{ article.why }}</p>
{% if feedback_enabled %}<form class=actions method=post action=\"{{ url_for('article_feedback') }}\"><input type=hidden name=url value=\"{{ article.url }}\"><input type=hidden name=article_index value=\"{{ loop.index }}\"><select name=reason aria-label=\"Hvorfor?\"><option value=\"\">Valgfrit: hvorfor?</option><option value=\"great_match\">Godt emne og vinkel</option><option value=\"great_depth\">God dybde</option><option value=\"surprising\">Overraskende fed</option><option value=\"uninteresting\">Uinteressant</option><option value=\"too_generic\">For generisk verdensnyhed</option><option value=\"unclear\">For svært eller uklart skrevet</option><option value=\"good_but_too_technical\">God, men for nørdet</option><option value=\"too_thin\">For tynd</option><option value=\"too_long\">For lang eller kedelig</option><option value=\"too_promotional\">For meget PR</option><option value=\"too_old\">For gammel</option><option value=\"duplicate\">Gentagelse</option></select><button name=direction value=more>Mere af den slags</button><button class=less name=direction value=less>Mindre af den slags</button></form>{% endif %}</article>
{% endfor %}{% if edition.report and edition.report.source_health %}<details><summary>Kildestatus for denne udgave</summary>
{% if edition.report.extraction %}<p>{{ edition.report.extraction.prepared }} af {{ edition.report.extraction.approved }} udvalgte artikler kunne hentes i fuld længde. Frasorteret: {{ edition.report.extraction.dropped_unreadable }} kunne ikke læses, {{ edition.report.extraction.dropped_diversity }} ramte kildeloftet{% if edition.report.collection and edition.report.collection.repeats_dropped %}, {{ edition.report.collection.repeats_dropped }} var allerede med i en tidligere udgave{% endif %}.{% if edition.report.extraction.budget_exhausted %} Netværksbudgettet blev opbrugt under artikelhentning.{% elif edition.report.collection and edition.report.collection.preview_budget_stopped %} Prøvelæsningen stoppede tidligt for at reservere budget til de valgte artikler.{% endif %}</p>{% endif %}
<ul>{% for name, state in edition.report.source_health.items() %}<li class=\"{{ 'ok' if state.status == 'ok' else 'error' }}\">{{ name }}: {{ state.status }}{% if state.status == 'ok' %} ({{ state['items'] }} fund){% endif %}</li>{% endfor %}</ul></details>{% endif %}
{% elif not feedback_enabled %}<p><em>Feedback vises her, når GitHub-feedbacknøglen er sat op, og den næste udgave er lavet.</em></p>{% endif %}
<p>Den færdige EPUB lægges i Google Drive-mappen Rakuten Kobo.</p></html>"""

PUBLIC_PAGE_STYLE = """<style>body{font:17px system-ui;max-width:46rem;margin:4rem auto;padding:0 1rem;color:#16201c;line-height:1.55}a{color:#31584d}</style>"""
PRIVACY_PAGE = f"""<!doctype html><html lang=\"da\"><meta charset=\"utf-8\"><title>Privatliv – Mads Morgen</title>{PUBLIC_PAGE_STYLE}
<h1>Privatliv</h1><p>Mads Morgen er en personlig, privat morgenavis til én bruger. Den indsamler offentligt tilgængelige nyhedslinks og RSS-data, skaber en EPUB og lægger den i brugerens valgte Google Drive-mappe.</p><p>Google Drive-adgangen bruges kun til at oprette, læse og slette de EPUB-filer, som Mads Morgen selv har oprettet. Appen læser ikke andre Drive-filer.</p><p>Artikel-feedback gemmes i et privat GitHub-repository for at forbedre fremtidige udvælgelser. Data sælges ikke og deles ikke med andre.</p><p>Spørgsmål: <a href=\"mailto:madsbh@me.com\">madsbh@me.com</a></p>"""
TERMS_PAGE = f"""<!doctype html><html lang=\"da\"><meta charset=\"utf-8\"><title>Vilkår – Mads Morgen</title>{PUBLIC_PAGE_STYLE}
<h1>Vilkår</h1><p>Mads Morgen er en privat, personlig automatisering. Den bruges på ejerens eget ansvar og er ikke en offentlig nyhedstjeneste.</p><p>Artikler og billeder tilhører deres respektive udgivere. EPUB’en er alene til personlig læsning; den må ikke videredistribueres.</p><p>Brugeren kan til enhver tid tilbagekalde Google Drive-adgangen fra sin Google-konto.</p><p>Spørgsmål: <a href=\"mailto:madsbh@me.com\">madsbh@me.com</a></p>"""


CATCH_UP_WINDOW_HOURS = 4

STATUS_MESSAGES = {
    "started": "Kørslen er startet. Den tager typisk 3-8 minutter; genindlæs siden om lidt.",
    "busy": "Der kører allerede en udgave. Vent til den er færdig.",
    "feedback": "Gemte din feedback i GitHub.",
    "feedback-off": "GitHub-feedback er ikke sat op endnu.",
}


def create_app(start_scheduler=True):
    app = Flask(__name__)
    settings = load_settings()
    timezone = ZoneInfo(settings["edition"]["timezone"])
    scheduler = BackgroundScheduler(timezone=timezone)
    # One edition at a time. The scheduled run is the real product; manual runs are
    # for testing, and must never collide with it or with a double-clicked button.
    run_lock = threading.Lock()
    state = {"running": False, "started_at": None, "finished_at": None,
             "articles": 0, "error": None, "drive": None}

    def execute(use_web_search=None, deliver_failure=True):
        """Run an edition in the background. Returns False when one is already running."""
        if not run_lock.acquire(blocking=False):
            return False
        state.update(running=True, started_at=datetime.now(timezone).isoformat(timespec="seconds"), error=None)

        def work():
            try:
                result = run_edition(settings, use_web_search=use_web_search,
                                     deliver_failure=deliver_failure)
                state.update(articles=result["articles"], drive=bool(result.get("drive_file")), error=None)
            except Exception as exc:
                state.update(error=f"{type(exc).__name__}: {exc}"[:400], articles=0)
                logger.exception("Morning edition failed")
            finally:
                state.update(running=False, finished_at=datetime.now(timezone).isoformat(timespec="seconds"))
                run_lock.release()

        threading.Thread(target=work, name="edition", daemon=True).start()
        return True

    def catch_up():
        """Run today's edition if a restart or deploy made the service miss it.

        Only safe when the GitHub archive is configured, because that archive is
        the one place that survives a Render restart and can answer 'did today's
        paper already go out?'.
        """
        if not feedback_configured():
            return
        try:
            edition = latest_edition() or {}
        except Exception:
            logger.exception("Catch-up check failed")
            return
        now = datetime.now(timezone)
        if str(edition.get("created_at", ""))[:10] == now.date().isoformat():
            return
        fields = cron_fields(settings["edition"]["schedule"])
        try:
            due = now.replace(hour=int(fields["hour"]), minute=int(fields["minute"]), second=0, microsecond=0)
        except ValueError:
            return  # A wildcard or list schedule has no single daily time to catch up to.
        # Only inside a window after the planned time. Without it, every afternoon
        # deploy — autoDeploy is on — would start a full paid edition 90s later,
        # and a day whose edition legitimately failed would retry on every restart.
        if not due <= now <= due + timedelta(hours=CATCH_UP_WINDOW_HOURS):
            return
        logger.warning("No edition recorded for %s; running catch-up", now.date().isoformat())
        execute()

    scheduler.add_job(
        execute,
        trigger="cron",
        id="morning-edition",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # A restart or a slow deploy at 05:29 used to skip the day entirely.
        misfire_grace_time=3600,
        **cron_fields(settings["edition"]["schedule"]),
    )
    # Ninety seconds after boot, so the health check answers first.
    scheduler.add_job(catch_up, trigger="date", id="catch-up", replace_existing=True,
                      run_date=datetime.now(timezone) + timedelta(seconds=90))
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
            # Compare bytes: compare_digest raises TypeError on non-ASCII str, which
            # turned a wrong username containing æøå into a 500 instead of a 401.
            def same(left, right):
                return hmac.compare_digest(str(left or "").encode("utf-8"), str(right or "").encode("utf-8"))

            if not auth or not same(auth.username, username) or not same(auth.password, password):
                return Response("Login påkrævet", 401, {"WWW-Authenticate": 'Basic realm="Mads Morgen"'})
            return handler(*args, **kwargs)
        return wrapped

    def next_run():
        job = scheduler.get_job("morning-edition")
        return getattr(job, "next_run_time", None) or "ukendt"

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", next_run=str(next_run()), last_run=dict(state))

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
        return render_template_string(PAGE, next_run=next_run(),
                                      result=STATUS_MESSAGES.get(request.args.get("status", "")),
                                      state=state, edition=edition,
                                      feedback_enabled=feedback_configured())

    @app.post("/run")
    @page_auth
    def run_from_page():
        # Start in the background and redirect, so the single worker keeps answering
        # the health check and a page refresh cannot trigger a second paid run.
        return redirect(url_for("home", status="started" if execute(use_web_search=True, deliver_failure=False) else "busy"), code=303)

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
        index = request.form.get("article_index", "")
        anchor = f"article-{index}" if index.isdigit() else None
        return redirect(url_for("home", status="feedback" if saved else "feedback-off", _anchor=anchor), code=303)

    @app.post("/run-now")
    def run_now():
        # Intended for Render manual testing. Protect it when RUN_NOW_TOKEN is configured.
        token = os.environ.get("RUN_NOW_TOKEN")
        if not token:
            return jsonify(error="RUN_NOW_TOKEN is not configured"), 503
        if not hmac.compare_digest(request.headers.get("Authorization", "").encode("utf-8"),
                                   f"Bearer {token}".encode("utf-8")):
            return jsonify(error="unauthorized"), 401
        if request.args.get("wait") == "true":
            return jsonify(run_edition(settings, use_web_search=request.args.get("web_search", "true") == "true",
                                       deliver_failure=False))
        started = execute(use_web_search=request.args.get("web_search", "true") == "true", deliver_failure=False)
        return jsonify(started=started, state=dict(state)), 202 if started else 409

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
