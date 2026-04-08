from flask import Flask, render_template, request, redirect, session, jsonify
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
import sqlite3

app = Flask(__name__)
app.secret_key = "super_secret_key"
socketio = SocketIO(app, async_mode="eventlet")

online_users = {}
last_seen = {}

# ---------- DATABASE ----------
def db():
    return sqlite3.connect("chat.db", check_same_thread=False)

def init_db():
    con = db()
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT
    )""")
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

@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        u = request.form["username"]
        p = generate_password_hash(request.form["password"])
        try:
            con = db(); cur = con.cursor()
            cur.execute("INSERT INTO users VALUES(NULL,?,?)", (u,p))
            con.commit(); con.close()
            return redirect("/login")
        except:
            pass
    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/chat")
@login_required
def chat():
    con = db(); cur = con.cursor()
    cur.execute("SELECT username FROM users WHERE username!=?", (session["user"],))
    users = [u[0] for u in cur.fetchall()]
    con.close()
    return render_template("chat.html", users=users, user=session["user"])

# ---------- STATUS ----------
@app.route("/status/<u>")
@login_required
def status(u):
    if u in online_users:
        return jsonify({"status": "Online"})
    return jsonify({"status": f"Last seen: {last_seen.get(u,'Never')}"})

# ---------- ADMIN (HIDDEN URL) ----------
@app.route("/admin-9x_typ0")
@login_required
def admin():
    if session["user"] != "admin":
        return "Forbidden", 403
    con = db(); cur = con.cursor()
    cur.execute("SELECT sender,receiver,content,time FROM messages")
    msgs = cur.fetchall()
    con.close()
    return render_template("admin.html", msgs=msgs)

# ---------- SOCKET.IO ----------
def room(a,b):
    return "_".join(sorted([a,b]))

@socketio.on("connect")
def connect():
    if "user" in session:
        online_users[session["user"]] = True

@socketio.on("disconnect")
def disconnect():
    if "user" in session:
        online_users.pop(session["user"], None)
        last_seen[session["user"]] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@socketio.on("join")
def join(data):
    join_room(room(data["me"], data["you"]))
    con = db(); cur = con.cursor()
    cur.execute("""SELECT sender,content,time FROM messages
        WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)
        ORDER BY id""",
        (data["me"],data["you"],data["you"],data["me"]))
    emit("load", cur.fetchall())
    con.close()

@socketio.on("send")
def send(data):
    t = datetime.now().strftime("%H:%M")
    con = db(); cur = con.cursor()
    cur.execute("INSERT INTO messages VALUES(NULL,?,?,?,?)",
        (data["me"], data["you"], data["msg"], t))
    con.commit(); con.close()
    emit("msg", {"s":data["me"],"m":data["msg"],"t":t},
         room=room(data["me"], data["you"]))

# ---------- RUN ----------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=8000)
