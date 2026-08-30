"""The local web app. Binds to localhost only; there is nothing to secure here
because there is nothing listening beyond this machine.
"""

from __future__ import annotations

import re
import traceback

from flask import Flask, jsonify, redirect, render_template, request, url_for

from . import service, store

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)


def _config_or_error():
    try:
        return store.load_config(), None
    except (FileNotFoundError, ValueError) as exc:
        return None, str(exc)


@app.route("/")
def index():
    config, err = _config_or_error()
    if err:
        return render_template("plan.html", plan=None, error=err, cache=None)

    plan = store.load_plan()
    cache = store.load_cache()
    error = None
    # Recomputed on every read, not read back from the saved plan. Prices change
    # without the week changing -- a --grocer-refresh, a receipt, one --price at
    # the checkout -- and a money block frozen at plan time meant none of that
    # reached the screen until you re-planned. The page cheerfully reported "0 of
    # 206 prices verified" with thirteen real ones in the file. It costs 25ms.
    if plan.get("days"):
        try:
            plan["money"] = service.money_summary(plan, config)
        except Exception:  # noqa: BLE001 - costing is a nicety, the plan is not
            plan["money"] = plan.get("money") or {}
    if not plan.get("days"):
        try:
            plan = service.generate(config)
        except Exception as exc:  # noqa: BLE001 - the page is the error report
            app.logger.error("generate failed: %s", traceback.format_exc())
            plan, error = None, str(exc)
    return render_template("plan.html", plan=plan, error=error, cache=cache,
                           undo=store.load_undo())


@app.post("/sync")
def sync():
    store.clear_undo()
    try:
        service.sync_recipes()
    except Exception as exc:  # noqa: BLE001
        app.logger.error("sync failed: %s", traceback.format_exc())
        return render_template("plan.html", plan=store.load_plan(),
                               error=f"Sync failed: {exc}",
                               cache=store.load_cache()), 502
    return redirect(url_for("index"))


@app.post("/regenerate")
def regenerate():
    store.clear_undo()
    config, err = _config_or_error()
    if err:
        return render_template("plan.html", plan=None, error=err, cache=None), 400
    try:
        service.generate(config)
    except Exception as exc:  # noqa: BLE001
        app.logger.error("regenerate failed: %s", traceback.format_exc())
        return render_template("plan.html", plan=store.load_plan(),
                               error=str(exc), cache=store.load_cache()), 500
    return redirect(url_for("index"))


@app.post("/reroll/<iso_day>")
def reroll(iso_day: str):
    store.clear_undo()
    config, err = _config_or_error()
    if err:
        return jsonify({"error": err}), 400
    try:
        plan = service.reroll(config, iso_day)
    except Exception as exc:  # noqa: BLE001
        app.logger.error("reroll failed: %s", traceback.format_exc())
        return jsonify({"error": str(exc)}), 500
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(plan)
    return redirect(url_for("index"))


@app.post("/office/<iso_day>")
def office(iso_day: str):
    store.clear_undo()
    config, err = _config_or_error()
    if err:
        return jsonify({"error": err}), 400
    try:
        service.toggle_office_day(config, iso_day)
    except Exception as exc:  # noqa: BLE001
        app.logger.error("office toggle failed: %s", traceback.format_exc())
        return render_template("plan.html", plan=store.load_plan(),
                               error=str(exc), cache=store.load_cache()), 500
    return redirect(url_for("index"))


@app.route("/shopping")
def shopping():
    config, err = _config_or_error()
    plan = store.load_plan()
    people = int((config or {}).get("household_size", 0) or 0)
    return render_template("shopping.html",
                           groups=service.shopping_list(plan, people),
                           people=people, plan=plan, error=err)


@app.post("/preferences/undo")
def undo_prefs():
    config, err = _config_or_error()
    if err:
        return render_template("plan.html", plan=None, error=err, cache=None), 400
    try:
        service.undo_preferences(config)
    except Exception as exc:  # noqa: BLE001
        app.logger.error("undo failed: %s", traceback.format_exc())
        return render_template("plan.html", plan=store.load_plan(),
                               error=str(exc), cache=store.load_cache(),
                               undo=store.load_undo()), 500
    return redirect(url_for("index"))


@app.post("/preferences/dismiss")
def dismiss_prefs():
    store.clear_undo()
    return redirect(url_for("index"))


@app.route("/preferences")
def prefs():
    config, err = _config_or_error()
    if err:
        return render_template("preferences.html", options=None, error=err), 400
    try:
        options = service.preference_options(config)
    except Exception as exc:  # noqa: BLE001
        app.logger.error("preferences failed: %s", traceback.format_exc())
        return render_template("preferences.html", options=None, error=str(exc))
    return render_template("preferences.html", options=options, error=None)


@app.post("/preferences")
def save_prefs():
    config, err = _config_or_error()
    if err:
        return render_template("preferences.html", options=None, error=err), 400

    ingredients = request.form.getlist("ingredient")
    # Free text, comma separated, for anything the curated list does not cover.
    extra = request.form.get("extra", "")
    ingredients += [t for t in re.split(r"[,\n]", extra) if t.strip()]
    methods = request.form.getlist("method")
    people = request.form.get("people", type=int) or config.get("household_size", 2)

    try:
        store_id = request.form.get("store_id", type=int)
        config = service.save_preferences(config, ingredients, methods,
                                          people, store_id)
        # Preferences change which recipes exist, so the standing plan is stale
        # the moment they are saved. Re-planning here rather than leaving a
        # stale week on screen is the whole point of the page.
        service.generate(config)
    except service.NoRecipes as exc:
        # Everything was excluded. Say so on the page rather than 500ing, and
        # leave the preferences saved so they can be loosened rather than lost.
        return render_template("preferences.html",
                               options=service.preference_options(config),
                               error=str(exc)), 200
    except Exception as exc:  # noqa: BLE001
        app.logger.error("save prefs failed: %s", traceback.format_exc())
        return render_template("preferences.html",
                               options=service.preference_options(config),
                               error=str(exc)), 500
    return redirect(url_for("index"))


@app.route("/api/plan")
def api_plan():
    return jsonify(store.load_plan())


def run(host: str = "127.0.0.1", port: int = 5173, debug: bool = False) -> None:
    app.run(host=host, port=port, debug=debug)
