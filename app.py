from flask import Flask, render_template, request, redirect, url_for, abort
import psycopg2
from psycopg2.extras import RealDictCursor
import os

app = Flask(__name__)

# ─── Database config ──────────────────────────────────────────────────────────
# On Railway, set DATABASE_URL (Postgres plugin provides it automatically).
# Locally, falls back to individual DB_* vars, then to local defaults.
# The marketing site (/) never needs the DB — it degrades gracefully so the
# landing page is always served even if Postgres is unavailable.
DATABASE_URL = os.environ.get("DATABASE_URL")

DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "neoscona_db")

# Admin route is gated behind a token so the public deploy can't be edited by anyone.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")


def get_db_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    return psycopg2.connect(
        user=DB_USER, password=DB_PASS, host=DB_HOST, port=DB_PORT, dbname=DB_NAME
    )


def fetch_posts():
    """Return blog posts, or an empty list if the DB is unreachable."""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM posts ORDER BY created_at DESC")
        posts = cur.fetchall()
        cur.close()
        conn.close()
        return posts
    except Exception as e:
        app.logger.warning("Blog DB unavailable, serving empty list: %s", e)
        return []


@app.route("/")
def index():
    return render_template("neoscona.html")


@app.route("/healthz")
def healthz():
    return {"status": "ok"}, 200


@app.route("/blog")
def blog():
    return render_template("blog.html", posts=fetch_posts())


@app.route("/admin", methods=("GET", "POST"))
def admin():
    # Require ?token=<ADMIN_TOKEN> when ADMIN_TOKEN is configured (i.e. in prod).
    if ADMIN_TOKEN and request.args.get("token") != ADMIN_TOKEN:
        abort(404)

    if request.method == "POST":
        title = request.form["title"]
        category = request.form["category"]
        content = request.form["content"]
        image_url = request.form["image_url"]

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO posts (title, category, content, image_url) VALUES (%s, %s, %s, %s)",
            (title, category, content, image_url),
        )
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for("blog"))

    return render_template("admin.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
