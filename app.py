from werkzeug.utils import secure_filename
import uuid
import secrets
from PIL import Image, UnidentifiedImageError
from io import BytesIO
from flask import Flask, render_template, request, redirect, session, jsonify
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
import sqlite3
import os
app = Flask(__name__)

AVATAR_DIR = "pv-chat-data/avatars"
MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5 MB

ALLOWED_IMAGE_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
}

os.makedirs(AVATAR_DIR, exist_ok=True)

app.secret_key = os.environ["PV_CHAT_SECRET_KEY"]
socketio = SocketIO(app)

online_users = {}
last_seen = {}

# ---------- DATABASE ----------
def db():
    con = sqlite3.connect(
        "pv-chat-data/chat.db",
        check_same_thread=False,
        timeout=10
    )

    con.execute("PRAGMA busy_timeout = 10000")

    return con

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("PRAGMA journal_mode=WAL")

    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        display_name TEXT NOT NULL,
        bio TEXT DEFAULT '',
        avatar TEXT DEFAULT '',
        FOREIGN KEY (user_id) REFERENCES users(id)
    );""")

    cur.execute("""CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY,
        sender TEXT,
        receiver TEXT,
        content TEXT,
        time TEXT
    )""")
    con.commit()
    con.close()

init_db()

def ensure_profiles():
    con = db()
    cur = con.cursor()

    cur.execute("SELECT id, username FROM users")

    users = cur.fetchall()

    for user_id, username in users:
        cur.execute(
            """
            INSERT OR IGNORE INTO profiles
            (user_id, display_name, bio, avatar)
            VALUES (?, ?, '', '')
            """,
            (user_id, username)
        )

    con.commit()
    con.close()

ensure_profiles()

# ---------- AUTH ----------
def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if "user" not in session:
            return redirect("/login")
        return f(*a, **kw)
    return wrap

# ---------- ROUTES ----------
@app.route("/")
@login_required
def home():
    return redirect("/chat")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]
        con = db(); cur = con.cursor()
        cur.execute("SELECT password FROM users WHERE username=?", (u,))
        row = cur.fetchone()
        con.close()
        if row and check_password_hash(row[0], p):
            session["user"] = u
            return redirect("/chat")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not u or not password:
            return "Username and password are required", 400

        p = generate_password_hash(password)

        con = db()
        cur = con.cursor()

        try:
            cur.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (u, p)
            )

            con.commit()

            # Create the profile immediately
            user_id = cur.lastrowid

            cur.execute(
                """
                INSERT INTO profiles
                (user_id, display_name, bio, avatar)
                VALUES (?, ?, '', '')
                """,
                (user_id, u)
            )

            con.commit()

        except sqlite3.IntegrityError:
            con.rollback()
            con.close()
            return "Username already exists", 409

        except sqlite3.Error as e:
            con.rollback()
            con.close()
            return f"Database error: {e}", 500

        finally:
            try:
                con.close()
            except:
                pass

        return redirect("/login")

    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/chat")
@login_required
def chat():
    con = db()
    cur = con.cursor()

# Get current user's profile
    cur.execute(
        """
        SELECT
            users.username,
            profiles.display_name,
            profiles.bio,
            profiles.avatar
        FROM users
        JOIN profiles
            ON users.id = profiles.user_id
        WHERE users.username=?
        """,
        (session["user"],)
    )

    me = cur.fetchone()

    if not me:
        con.close()
        return "Profile not found", 404

# Get all other users and their profiles
    cur.execute(
        """
        SELECT
            users.username,
            profiles.display_name,
            profiles.avatar
        FROM users
        JOIN profiles
            ON users.id = profiles.user_id
        WHERE users.username!=?
        ORDER BY profiles.display_name COLLATE NOCASE
        """,
        (session["user"],)
    )

    rows = cur.fetchall()

    con.close()

    users = [
        {
            "username": username,
            "display_name": display_name,
            "avatar": avatar
        }
        for username, display_name, avatar in rows
    ]

    return render_template(
        "chat.html",
        user=session["user"],
        profile={
            "username": me[0],
            "display_name": me[1],
            "bio": me[2],
            "avatar": me[3]
        },
        users=users
    )
@app.route("/avatar/<filename>")
@login_required
def avatar(filename):
    from flask import send_from_directory

    return send_from_directory(
        "pv-chat-data/avatars",
        filename
    )

# ---------- STATUS ----------
@app.route("/status/<u>")
@login_required
def status(u):
    if u in online_users:
        return jsonify({"status": "Online"})
    return jsonify({"status": f"Last seen: {last_seen.get(u,'Never')}"})

# ----------- ADMIN (HIDDEN URL) ----------
@app.route("/admin-h9tq32")
@login_required
def admin():
    if session["user"] != "MSI":
        return "Forbidden", 403
    con = db(); cur = con.cursor()
    cur.execute("SELECT sender,receiver,content,time FROM messages")
    msgs = cur.fetchall()
    con.close()
    return render_template("admin.html", msgs=msgs)

#---------- PROFILE ----------

@app.route("/profile")
@login_required
def profile():
    con = db()
    cur = con.cursor()

    cur.execute(
        """
        SELECT username, display_name, bio, avatar
        FROM users
        JOIN profiles ON users.id = profiles.user_id
        WHERE users.username=?
        """,
        (session["user"],)
    )

    profile_data = cur.fetchone()

    con.close()

    if not profile_data:
        return "Profile not found", 404

    username, display_name, bio, avatar = profile_data

    return render_template(
        "profile.html",
        username=username,
        display_name=display_name,
        bio=bio,
        avatar=avatar
    )

@app.route("/profile/edit", methods=["GET", "POST"])
def edit_profile():
    con = db()
    cur = con.cursor()

    cur.execute(
        """
        SELECT users.id, users.username,
               profiles.display_name,
               profiles.bio,
               profiles.avatar
        FROM users
        JOIN profiles ON users.id = profiles.user_id
        WHERE users.username=?
        """,
        (session["user"],)
    )

    profile_data = cur.fetchone()

    if not profile_data:
        con.close()
        return "Profile not found", 404

    user_id, username, display_name, bio, avatar = profile_data

    if request.method == "POST":
        display_name = request.form.get(
            "display_name", ""
        ).strip()

        bio = request.form.get(
            "bio", ""
        ).strip()

        if not display_name:
            display_name = username

        display_name = display_name[:50]
        bio = bio[:500]

        cur.execute(
            """
            UPDATE profiles
            SET display_name=?, bio=?
            WHERE user_id=?
            """,
            (display_name, bio, user_id)
        )

        con.commit()
        con.close()

        return redirect("/profile")

    con.close()

    return render_template(
        "edit_profile.html",
        username=username,
        display_name=display_name,
        bio=bio,
        avatar=avatar
    )



@app.route("/profile/avatar", methods=["POST"])
@login_required
def upload_avatar():
    file = request.files.get("avatar")

    if not file or not file.filename:
        return "No image selected", 400

# Check uploaded file size.
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)

    if size > MAX_AVATAR_SIZE:
        return "Image is too large. Maximum size is 5 MB.", 400

# Validate the actual image.
    try:
        image = Image.open(file)

    # Force Pillow to decode the image.
        image.verify()

    # verify() consumes the image, so reopen it.
        file.stream.seek(0)
        image = Image.open(file)

        image_format = image.format

        if image_format not in ALLOWED_IMAGE_FORMATS:
            return "Unsupported image format", 400

    except (UnidentifiedImageError, OSError):
        return "Invalid image", 400

# Generate a completely new server-side filename.
    filename = (
        uuid.uuid4().hex +
        ALLOWED_IMAGE_FORMATS[image_format]
    )

    filepath = os.path.join(
        AVATAR_DIR,
        filename
    )

# Save the validated file.
    file.stream.seek(0)

    with open(filepath, "wb") as destination:
        destination.write(file.stream.read())

    username = session["user"]

    con = db()
    cur = con.cursor()

# Find the current user's ID.
    cur.execute(
        "SELECT id FROM users WHERE username=?",
        (username,)
    )

    row = cur.fetchone()

    if not row:
        con.close()

    # Remove the newly uploaded file because the user wasn't found.
        try:
            os.remove(filepath)
        except OSError:
            pass

        return "User not found", 404

    user_id = row[0]

# Get the old avatar.
    cur.execute(
        "SELECT avatar FROM profiles WHERE user_id=?",
        (user_id,)
    )

    old = cur.fetchone()
    old_avatar = old[0] if old else None

# Update profile.
    cur.execute(
        """
        UPDATE profiles
        SET avatar=?
        WHERE user_id=?
        """,
        (filename, user_id)
    )

    con.commit()
    con.close()

# Remove old avatar after successful database update.
    if old_avatar:
        old_path = os.path.join(
        AVATAR_DIR,
        os.path.basename(old_avatar)
    )

    if os.path.isfile(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass

    return redirect("/profile")

# ---------- SOCKET.IO ----------

def room(a, b):
    return "_".join(sorted([a, b]))


def get_current_user():
    return session.get("user")


@socketio.on("connect")
def connect():
    user = get_current_user()

    if not user:
        return False

    online_users[user] = True


@socketio.on("disconnect")
def disconnect():
    user = get_current_user()

    if not user:
        return

    online_users.pop(user, None)

    last_seen[user] = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


@socketio.on("join")
def join(data):
    user = get_current_user()

    if not user:
        return

    target = data.get("you")

    if not target or target == user:
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT username FROM users WHERE username=?",
        (target,)
    )

    if cur.fetchone() is None:
        con.close()
        return

    cur.execute(
        """
        SELECT sender, content, time
        FROM messages
        WHERE
            (sender=? AND receiver=?)
            OR
            (sender=? AND receiver=?)
        ORDER BY id
        """,
        (user, target, target, user)
    )

    messages = cur.fetchall()

    con.close()

    join_room(room(user, target))

    emit("load", messages)

# ---------- TYPING INDICATOR ----------

@socketio.on("typing")
def typing(data):
    user = get_current_user()

    if not user:
        return

    target = data.get("you")

    if not target or target == user:
        return

    emit(
        "typing",
        {
            "user": user
        },
        room=room(user, target),
        include_self=False
    )


@socketio.on("stop_typing")
def stop_typing(data):
    user = get_current_user()

    if not user:
        return

    target = data.get("you")

    if not target or target == user:
        return

    emit(
        "stop_typing",
        {
            "user": user
        },
        room=room(user, target),
        include_self=False
    )

@socketio.on("send")
def send(data):
    user = get_current_user()

    if not user:
        return

    target = data.get("you")
    message = data.get("msg")

    if not target or not message:
        return

    message = message.strip()

    if not message:
        return

    if len(message) > 5000:
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT username FROM users WHERE username=?",
        (target,)
    )

    if cur.fetchone() is None:
        con.close()
        return

    t = datetime.now().strftime("%H:%M")

    cur.execute(
        """
        INSERT INTO messages
        VALUES(NULL, ?, ?, ?, ?)
        """,
        (user, target, message, t)
    )

    con.commit()
    con.close()

    emit(
        "msg",
        {
            "s": user,
            "m": message,
            "t": t
        },
        room=room(user, target)
    )

# ---------- RUN ----------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=8000)
