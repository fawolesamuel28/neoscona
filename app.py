from flask import Flask, render_template, request, redirect, url_for, abort, session, flash
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "neoscona-dev-key")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

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


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/products/reva")
def product_reva():
    return render_template("reva_landing.html")


@app.route("/products/reva/dashboard")
@login_required
def product_reva_dashboard():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT balance FROM users WHERE id = %s", (session["user_id"],))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return render_template("reva_dashboard.html", user=user)


@app.route("/docs")
def docs():
    return render_template("docs.html")


@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    # Placeholder for no-code knowledge base upload
    return {"status": "success", "message": "File received"}, 200


# ─── Auth Routes ──────────────────────────────────────────────────────────────

# Move this logic up


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        first_name = request.form.get("first_name")
        last_name = request.form.get("last_name")
        email = request.form.get("email")
        password = request.form.get("password")
        
        if not email or not password:
            flash("Email and password are required.")
            return redirect(url_for("signup"))
            
        hashed = generate_password_hash(password)
        
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (first_name, last_name, email, password_hash) VALUES (%s, %s, %s, %s)",
                (first_name, last_name, email, hashed)
            )
            conn.commit()
            cur.close()
            conn.close()
            flash("Account created! Please login.")
            return redirect(url_for("login"))
        except Exception as e:
            flash("An error occurred. That email may already be in use.")
            return redirect(url_for("signup"))
            
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            next_url = request.args.get("next")
            return redirect(next_url or url_for("dashboard"))
            
        flash("Invalid email or password.")
        
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
