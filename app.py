"""
Tahssina (تحسينة) - Real-Time Barbershop Queue Management
Flask Backend Application
"""

import sqlite3
import threading
import time
import json
import smtplib
import os
import hashlib
import secrets
import re
import base64
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, Response, g, flash, send_from_directory)
from flask import send_file, abort
from flask import request

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024  # 8MB hard cap (defense in depth for uploads)

DATABASE = os.environ.get('DATABASE_PATH', '/app/data/tahssina.db')
UPLOAD_DIR  = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
ALLOWED_EXT = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
MAX_FILE_MB = 5

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ─── QR Code generator ────────────────────────────────────────────────────────
def generate_qr_svg(data: str) -> str:
    """Return inline SVG QR code for given data string."""
    try:
        import qrcode
        import io
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        size = len(matrix)
        cell = 6
        dim  = size * cell
        rects = []
        for r, row in enumerate(matrix):
            for c, val in enumerate(row):
                if val:
                    rects.append(f'<rect x="{c*cell}" y="{r*cell}" width="{cell}" height="{cell}"/>')
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
               f'style="background:#fff;border-radius:8px">'
               f'<g fill="#121212">{"".join(rects)}</g></svg>')
        return svg
    except Exception:
        # Fallback: Google Charts API img tag
        enc = urllib.parse.quote(data)
        return f'<img src="https://api.qrserver.com/v1/create-qr-code/?data={enc}&size=200x200&bgcolor=ffffff&color=121212&margin=10" style="border-radius:8px" width="200" height="200">'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def safe_filename(filename):
    name = os.path.splitext(filename)[0]
    ext  = os.path.splitext(filename)[1].lower()
    name = re.sub(r'[^\w\-]', '_', name)[:40]
    return f"{name}_{secrets.token_hex(6)}{ext}"

# ─── EMAIL CONFIG (fill in real creds or use a mock) ──────────────────────────
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
EMAIL_FROM = os.environ.get('EMAIL_FROM', 'noreply@tahssina.app')

# ─── SSE subscriber registry ──────────────────────────────────────────────────
_sse_subscribers = {}   # barber_id -> list of queue objects
_sse_lock = threading.Lock()

# ═════════════════════════════════════════════════════════════════════════════
#  DATABASE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        with open(os.path.join(os.path.dirname(__file__), 'schema.sql'), 'r') as f:
            db.executescript(f.read())
        # Migrations for existing databases
        try:
            db.execute("ALTER TABLE barbers ADD COLUMN max_daily_clients INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS waitlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    barber_id INTEGER NOT NULL,
                    requested_date DATE NOT NULL,
                    service_ids TEXT NOT NULL,
                    status TEXT CHECK(status IN ('waiting','notified','booked','expired','cancelled')) DEFAULT 'waiting',
                    notified_at DATETIME DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE,
                    UNIQUE(client_id, barber_id, requested_date)
                )
            """)
        except Exception:
            pass
        db.commit()
        db.close()

from werkzeug.security import generate_password_hash, check_password_hash

def hash_password(password):
    return generate_password_hash(password)

def verify_password(password, password_hash):
    # Support legacy sha256 hashes (64 hex chars) for backward compatibility, else use werkzeug
    if len(password_hash) == 64 and all(c in '0123456789abcdef' for c in password_hash):
        return hashlib.sha256(password.encode()).hexdigest() == password_hash
    return check_password_hash(password_hash, password)

# ═════════════════════════════════════════════════════════════════════════════
#  AUTH DECORATORS
# ═════════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def barber_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'barber':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def client_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'client':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ═════════════════════════════════════════════════════════════════════════════
#  SSE HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def push_sse(barber_id, data):
    with _sse_lock:
        queues = _sse_subscribers.get(barber_id, [])
        dead = []
        for q in queues:
            try:
                q.put_nowait(data)
            except Exception:
                dead.append(q)
        for d in dead:
            queues.remove(d)
        _sse_subscribers[barber_id] = queues

# ═════════════════════════════════════════════════════════════════════════════
#  EMAIL ENGINE
# ═════════════════════════════════════════════════════════════════════════════

def send_email(to_addr, subject, html_body):
    if not SMTP_USER:
        app.logger.info(f"[EMAIL MOCK] To={to_addr} Subject={subject}")
        return True
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = EMAIL_FROM
        msg['To'] = to_addr
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, to_addr, msg.as_string())
        return True
    except Exception as e:
        app.logger.error(f"Email failed: {e}")
        return False

def build_confirmation_email(username, salon_name, address, appt_time, total_price, maps_link):
    return f"""
    <div style="font-family:Arial;background:#121212;color:#F8F9FA;padding:32px;border-radius:12px;max-width:520px;margin:auto">
      <h2 style="color:#D4AF37;margin:0 0 8px">تحسينة · Tahssina</h2>
      <p style="color:#A0A0A0;margin:0 0 24px;font-size:13px">Booking confirmation</p>
      <p>Hello <strong>{username}</strong>,</p>
      <p>Your booking at <strong style="color:#D4AF37">{salon_name}</strong> has been received.</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <tr><td style="color:#A0A0A0;padding:6px 0">Time</td><td style="color:#F8F9FA;text-align:right">{appt_time}</td></tr>
        <tr><td style="color:#A0A0A0;padding:6px 0">Location</td><td style="color:#F8F9FA;text-align:right">{address}</td></tr>
        <tr><td style="color:#A0A0A0;padding:6px 0">Total</td><td style="color:#D4AF37;text-align:right;font-weight:bold">{total_price:.2f} MAD</td></tr>
      </table>
      <a href="{maps_link}" style="display:inline-block;background:#D4AF37;color:#121212;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;margin-top:8px">Get Directions →</a>
      <p style="color:#A0A0A0;font-size:12px;margin-top:24px">You will receive a reminder email 2 hours before your appointment.</p>
    </div>"""

def build_reminder_email(username, salon_name, address, appt_time, total_price, maps_link):
    return f"""
    <div style="font-family:Arial;background:#121212;color:#F8F9FA;padding:32px;border-radius:12px;max-width:520px;margin:auto">
      <h2 style="color:#D4AF37;margin:0 0 8px">تحسينة · Tahssina</h2>
      <p style="color:#A0A0A0;margin:0 0 24px;font-size:13px">Your appointment reminder</p>
      <p>Hello <strong>{username}</strong>,</p>
      <p>Your appointment at <strong style="color:#D4AF37">{salon_name}</strong> is in approximately <strong>2 hours</strong>.</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <tr><td style="color:#A0A0A0;padding:6px 0">Time</td><td style="color:#F8F9FA;text-align:right">{appt_time}</td></tr>
        <tr><td style="color:#A0A0A0;padding:6px 0">Location</td><td style="color:#F8F9FA;text-align:right">{address}</td></tr>
        <tr><td style="color:#A0A0A0;padding:6px 0">Total</td><td style="color:#D4AF37;text-align:right;font-weight:bold">{total_price:.2f} MAD</td></tr>
      </table>
      <a href="{maps_link}" style="display:inline-block;background:#D4AF37;color:#121212;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;margin-top:8px">Get Directions →</a>
    </div>"""

def email_worker():
    """Background thread: fires reminder emails 2 hours before appointment."""
    while True:
        try:
            conn = sqlite3.connect(DATABASE)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            now = datetime.now()
            window_start = now + timedelta(hours=1, minutes=55)
            window_end   = now + timedelta(hours=2, minutes=5)
            rows = conn.execute("""
                SELECT q.id, q.appointment_time, q.total_price,
                       u.email, u.username,
                       b.salon_name, b.address
                FROM queue q
                JOIN users u ON u.id = q.client_id
                JOIN barbers b ON b.id = q.barber_id
                WHERE q.status = 'waiting'
                  AND q.email_sent = 0
                  AND datetime(q.appointment_time) BETWEEN ? AND ?
            """, (window_start.strftime('%Y-%m-%d %H:%M:%S'),
                  window_end.strftime('%Y-%m-%d %H:%M:%S'))).fetchall()

            for row in rows:
                maps_link = f"https://www.google.com/maps/search/?api=1&query={row['address'].replace(' ', '+')}"
                html = build_reminder_email(row['username'], row['salon_name'], row['address'],
                                             row['appointment_time'], row['total_price'], maps_link)
                if send_email(row['email'], f"Reminder: Your appointment at {row['salon_name']}", html):
                    conn.execute("UPDATE queue SET email_sent=1 WHERE id=?", (row['id'],))
                    conn.commit()
            conn.close()
        except Exception as e:
            app.logger.error(f"Email worker error: {e}")
        time.sleep(60)

# ═════════════════════════════════════════════════════════════════════════════
#  QUEUE MATH
# ═════════════════════════════════════════════════════════════════════════════

def compute_wait_minutes(barber_id, queue_id, db):
    """
    Total wait for client X =
      remaining minutes of current 'ongoing' client
      + sum of assigned_duration of all 'waiting' clients with id <= X
    """
    ongoing = db.execute("""
        SELECT appointment_time, assigned_duration FROM queue
        WHERE barber_id=? AND status='ongoing'
        ORDER BY id LIMIT 1
    """, (barber_id,)).fetchone()

    ongoing_remaining = 0
    if ongoing:
        start = datetime.strptime(ongoing['appointment_time'], '%Y-%m-%d %H:%M:%S')
        elapsed = (datetime.now() - start).total_seconds() / 60
        ongoing_remaining = max(0, ongoing['assigned_duration'] - elapsed)

    waiting_sum = db.execute("""
        SELECT COALESCE(SUM(assigned_duration), 0) AS s FROM queue
        WHERE barber_id=? AND status='waiting' AND id <= ?
    """, (barber_id, queue_id)).fetchone()['s']

    return int(ongoing_remaining + waiting_sum)


def is_day_full(barber_id, date_str, db):
    """
    Returns True if the barber has reached their max_daily_clients limit for date_str.
    If max_daily_clients is 0 (not set), never full.
    """
    barber = db.execute("SELECT max_daily_clients FROM barbers WHERE id=?", (barber_id,)).fetchone()
    if not barber or not barber['max_daily_clients']:
        return False
    count = db.execute("""
        SELECT COUNT(*) as c FROM queue
        WHERE barber_id=? AND DATE(appointment_time)=?
          AND status IN ('waiting','ongoing','pending','done')
    """, (barber_id, date_str)).fetchone()['c']
    return count >= barber['max_daily_clients']

# ═════════════════════════════════════════════════════════════════════════════
#  ROUTES — AUTH
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'barber':
            return redirect(url_for('barber_dashboard'))
        return redirect(url_for('client_home'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        db = get_db()
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip().lower()
        phone    = request.form.get('phone', '').strip()
        role     = request.form.get('role', '')
        password = request.form.get('password', '')

        if not all([username, email, phone, role, password]):
            flash('All fields are required.', 'error')
            return render_template('register.html')

        existing = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            flash('Email already registered.', 'error')
            return render_template('register.html')

        pw_hash = hash_password(password)
        cur = db.execute(
            "INSERT INTO users (username, email, phone, role, password_hash) VALUES (?,?,?,?,?)",
            (username, email, phone, role, pw_hash)
        )
        user_id = cur.lastrowid

        if role == 'barber':
            salon_name = request.form.get('salon_name', '').strip()
            address    = request.form.get('address', '').strip()
            if not salon_name or not address:
                flash('Salon name and address are required for barbers.', 'error')
                db.execute("DELETE FROM users WHERE id=?", (user_id,))
                db.commit()
                return render_template('register.html')
            db.execute(
                "INSERT INTO barbers (user_id, salon_name, address) VALUES (?,?,?)",
                (user_id, salon_name, address)
            )

        db.commit()
        flash('Account created! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

_login_attempts = {}  # ip -> [timestamps]
_LOGIN_MAX_ATTEMPTS = 8
_LOGIN_WINDOW_SECONDS = 300

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        ip  = request.remote_addr or 'unknown'
        now = time.time()
        attempts = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW_SECONDS]
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            flash('Too many login attempts. Please wait a few minutes and try again.', 'error')
            return render_template('login.html')

        db   = get_db()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and verify_password(password, user['password_hash']):
            _login_attempts.pop(ip, None)
            session['user_id']  = user['id']
            session['username'] = user['username']
            session['role']     = user['role']
            if user['role'] == 'barber':
                barber = db.execute("SELECT id FROM barbers WHERE user_id=?", (user['id'],)).fetchone()
                session['barber_id'] = barber['id'] if barber else None
            return redirect(url_for('index'))
        attempts.append(now)
        _login_attempts[ip] = attempts
        flash('Invalid email or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ═════════════════════════════════════════════════════════════════════════════
#  ROUTES — CLIENT
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/client')
@client_required
def client_home():
    db = get_db()
    barbers = db.execute("""
        SELECT b.id, b.salon_name, b.address, b.min_price, b.max_price, b.is_open,
               b.max_daily_clients,
               u.username, u.phone,
               COALESCE(AVG(cr.rating),0) as avg_rating,
               COUNT(cr.id) as review_count
        FROM barbers b
        JOIN users u ON u.id = b.user_id
        LEFT JOIN client_ratings cr ON cr.barber_id = b.id
        GROUP BY b.id
        ORDER BY b.is_open DESC, avg_rating DESC
    """).fetchall()
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_full = {b['id']: is_day_full(b['id'], today_str, db) for b in barbers}
    return render_template('client_home.html', barbers=barbers, today_full=today_full)

@app.route('/client/book/<int:barber_id>', methods=['GET'])
@client_required
def book(barber_id):
    db = get_db()
    barber   = db.execute("SELECT b.*, u.username FROM barbers b JOIN users u ON u.id=b.user_id WHERE b.id=?", (barber_id,)).fetchone()
    services = db.execute("SELECT * FROM services WHERE barber_id=?", (barber_id,)).fetchall()
    if not barber:
        flash('Barber not found.', 'error')
        return redirect(url_for('client_home'))
    closed_dates = db.execute(
        "SELECT closed_date, reason FROM closed_dates WHERE barber_id=? AND closed_date >= DATE('now')",
        (barber_id,)
    ).fetchall()
    return render_template('book.html', barber=barber, services=services,
        closed_dates=[dict(c) for c in closed_dates])

@app.route('/client/book', methods=['POST'])
@client_required
def book_submit():
    db         = get_db()
    client_id  = session['user_id']
    barber_id  = int(request.form.get('barber_id', 0))
    service_ids= request.form.getlist('service_ids')
    appt_time  = request.form.get('appointment_time', '')

    if not service_ids:
        flash('Select at least one service.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    # ── Anti-spam: max 1 active session per day ──────────────────────────────
    today_str = datetime.now().strftime('%Y-%m-%d')
    active = db.execute("""
        SELECT id FROM queue
        WHERE client_id=? AND status IN ('pending','waiting','ongoing')
          AND DATE(created_at)=?
    """, (client_id, today_str)).fetchone()
    if active:
        flash('You already have an active booking today. Please complete or cancel it first.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    # ── Validate appointment time ─────────────────────────────────────────────
    try:
        appt_dt = datetime.strptime(appt_time, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('Invalid appointment time.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    if appt_dt < datetime.now():
        flash('Cannot book a time in the past.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    # ── Fetch services ────────────────────────────────────────────────────────
    placeholders = ','.join('?' * len(service_ids))
    svcs = db.execute(
        f"SELECT * FROM services WHERE id IN ({placeholders}) AND barber_id=?",
        service_ids + [barber_id]
    ).fetchall()

    if not svcs:
        flash('Invalid services selected.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    total_price    = sum(s['price'] for s in svcs)
    total_duration = sum(s['duration'] for s in svcs)

    # ── Time overlap check ────────────────────────────────────────────────────
    appt_end = appt_dt + timedelta(minutes=total_duration)
    overlap = db.execute("""
        SELECT id FROM queue
        WHERE barber_id=? AND status IN ('waiting','ongoing')
          AND datetime(appointment_time) < ?
          AND datetime(appointment_time, assigned_duration || ' minutes') > ?
    """, (barber_id,
          appt_end.strftime('%Y-%m-%d %H:%M:%S'),
          appt_dt.strftime('%Y-%m-%d %H:%M:%S'))).fetchone()

    if overlap:
        flash('This time slot overlaps with an existing booking. Please choose another time.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    # ── Barber open check + working hours ────────────────────────────────────
    barber = db.execute("SELECT * FROM barbers WHERE id=?", (barber_id,)).fetchone()
    if not barber or not barber['is_open']:
        flash('This barber is currently closed.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    # Validate appointment is within working hours
    appt_time_str = appt_dt.strftime('%H:%M')
    open_t  = barber['open_time']  or '09:00'
    close_t = barber['close_time'] or '20:00'
    if appt_time_str < open_t or appt_time_str >= close_t:
        flash(f'This barber is only open between {open_t} and {close_t}. Please choose a time within working hours.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    # ── Closed date check (barber manually disabled this specific day) ────────
    appt_date_str = appt_dt.strftime('%Y-%m-%d')
    closed_row = db.execute(
        "SELECT reason FROM closed_dates WHERE barber_id=? AND closed_date=?",
        (barber_id, appt_date_str)
    ).fetchone()
    if closed_row:
        reason = closed_row['reason'].strip() if closed_row['reason'] else ''
        if reason:
            flash(f'This barber is closed on this day: "{reason}". Please choose another date.', 'error')
        else:
            flash('This barber is closed on this day. Please choose another date.', 'error')
        return redirect(url_for('book', barber_id=barber_id))

    # ── Daily capacity check — offer waitlist if full ─────────────────────────
    if is_day_full(barber_id, appt_date_str, db):
        existing_wl = db.execute(
            "SELECT id FROM waitlist WHERE client_id=? AND barber_id=? AND requested_date=? AND status='waiting'",
            (client_id, barber_id, appt_date_str)
        ).fetchone()
        if existing_wl:
            flash('This day is fully booked. You are already on the waitlist for this date — we will notify you if a slot opens up.', 'warning')
        else:
            db.execute(
                "INSERT OR IGNORE INTO waitlist (client_id, barber_id, requested_date, service_ids) VALUES (?,?,?,?)",
                (client_id, barber_id, appt_date_str, json.dumps([int(s) for s in service_ids]))
            )
            db.commit()
            flash(f'📋 This day is fully booked! You have been added to the waitlist for {appt_dt.strftime("%A, %B %d")}. We will notify you by email if a slot opens up.', 'warning')
        return redirect(url_for('book', barber_id=barber_id))

    # ── No-show / block check ─────────────────────────────────────────────────
    client = db.execute("SELECT no_show_count, blocked_until FROM users WHERE id=?", (client_id,)).fetchone()

    # Check hard block (4+ no-shows, 2 weeks ban)
    if client['blocked_until']:
        blocked_until_dt = datetime.strptime(client['blocked_until'], '%Y-%m-%d %H:%M:%S')
        if datetime.now() < blocked_until_dt:
            days_left = (blocked_until_dt - datetime.now()).days + 1
            flash(f'⛔ Your account is blocked for {days_left} more day(s) due to repeated no-shows. You can book again after {blocked_until_dt.strftime("%d/%m/%Y")}.', 'error')
            return redirect(url_for('client_home'))
        else:
            # Block expired — reset
            db.execute("UPDATE users SET blocked_until=NULL WHERE id=?", (client_id,))
            db.commit()

    no_shows = client['no_show_count']
    # 3 no-shows → manual review. 4+ → never auto-accept (block applied on 4th no-show)
    auto_accept_blocked = no_shows >= 3
    initial_status = 'pending' if auto_accept_blocked else 'waiting'

    appt_str = appt_dt.strftime('%Y-%m-%d %H:%M:%S')
    cur = db.execute("""
        INSERT INTO queue (client_id, barber_id, status, total_price, appointment_time, assigned_duration)
        VALUES (?,?,?,?,?,?)
    """, (client_id, barber_id, initial_status, total_price, appt_str, total_duration))
    queue_id = cur.lastrowid

    for s in svcs:
        db.execute("INSERT INTO queue_services (queue_id, service_id) VALUES (?,?)",
                   (queue_id, s['id']))

    db.commit()
    push_sse(barber_id, json.dumps({'event': 'new_booking', 'queue_id': queue_id}))

    # ── Send instant confirmation email ───────────────────────────────────────
    try:
        client_row = db.execute("SELECT email, username, phone FROM users WHERE id=?", (client_id,)).fetchone()
        barber_row = db.execute("SELECT salon_name, address, favor_service_id FROM barbers b JOIN users u ON u.id=b.user_id WHERE b.id=?", (barber_id,)).fetchone()
        maps_link   = f"https://www.google.com/maps/search/?api=1&query={barber_row['address'].replace(' ', '+')}"

        html = build_confirmation_email(client_row['username'], barber_row['salon_name'],
                                         barber_row['address'], appt_str, total_price, maps_link)
        if send_email(client_row['email'], f"Booking confirmed at {barber_row['salon_name']}", html):
            db.execute("UPDATE queue SET confirmation_sent=1 WHERE id=?", (queue_id,))
            db.commit()

    except Exception as e:
        app.logger.error(f"Post-booking notification error: {e}")

    if auto_accept_blocked:
        flash('Your request has been submitted for manual review due to previous no-shows.', 'warning')
    else:
        flash('Booking confirmed! You are in the queue.', 'success')
    return redirect(url_for('track', queue_id=queue_id))

@app.route('/client/track/<int:queue_id>')
@client_required
def track(queue_id):
    db = get_db()
    row = db.execute("""
        SELECT q.*, u.username as client_name,
               b.salon_name, b.address,
               bu.phone as barber_phone,
               cu.no_show_count, cu.loyalty_visits
        FROM queue q
        JOIN users cu ON cu.id = q.client_id
        JOIN barbers b ON b.id = q.barber_id
        JOIN users bu ON bu.id = b.user_id
        JOIN users u ON u.id = q.client_id
        WHERE q.id=? AND q.client_id=?
    """, (queue_id, session['user_id'])).fetchone()

    if not row:
        flash('Queue entry not found.', 'error')
        return redirect(url_for('client_home'))

    services = db.execute("""
        SELECT s.name, s.price, s.duration FROM queue_services qs
        JOIN services s ON s.id = qs.service_id
        WHERE qs.queue_id=?
    """, (queue_id,)).fetchall()

    wait_mins = compute_wait_minutes(row['barber_id'], queue_id, db)
    maps_url  = f"https://www.google.com/maps/search/?api=1&query={row['address'].replace(' ', '+')}"
    waze_url  = f"https://waze.com/ul?q={row['address'].replace(' ', '+')}"

    # Check if already rated
    already_rated = None
    if row['status'] == 'done':
        already_rated = db.execute(
            "SELECT id FROM client_ratings WHERE barber_id=? AND client_id=?",
            (row['barber_id'], session['user_id'])
        ).fetchone()

    # Client no-show card color
    no_shows = row['no_show_count']
    ns_card = 'red' if no_shows >= 4 else ('yellow' if no_shows >= 3 else None)

    return render_template('track.html',
        entry=row, services=services, wait_mins=wait_mins,
        maps_url=maps_url, waze_url=waze_url, already_rated=already_rated,
        ns_card=ns_card)

@app.route('/client/cancel/<int:queue_id>', methods=['POST'])
@client_required
def cancel_queue(queue_id):
    db = get_db()
    row = db.execute("SELECT * FROM queue WHERE id=? AND client_id=?",
                     (queue_id, session['user_id'])).fetchone()
    if row and row['status'] in ('pending', 'waiting', 'ongoing'):
        freed_date = row['appointment_time'][:10]  # YYYY-MM-DD
        barber_id  = row['barber_id']
        db.execute("UPDATE queue SET status='rejected' WHERE id=?", (queue_id,))
        db.commit()
        push_sse(barber_id, json.dumps({'event': 'queue_update'}))
        # Notify waitlist if a slot opened up
        try:
            notify_waitlist(barber_id, freed_date, db)
        except Exception as e:
            app.logger.error(f"Waitlist notify error: {e}")
        flash('Booking cancelled.', 'success')
    else:
        flash('This booking can no longer be cancelled.', 'error')
    return redirect(url_for('client_home'))

@app.route('/client/loyalty')
@client_required
def loyalty_cards_page():
    db   = get_db()
    cards = db.execute("""
        SELECT lc.*, b.salon_name, b.address, b.favor_service_id,
               fs.name as favor_service_name,
               bu.phone as barber_phone
        FROM loyalty_cards lc
        JOIN barbers b ON b.id = lc.barber_id
        JOIN users bu ON bu.id = b.user_id
        LEFT JOIN services fs ON fs.id = b.favor_service_id
        WHERE lc.client_id = ?
        ORDER BY lc.updated_at DESC
    """, (session['user_id'],)).fetchall()
    return render_template('loyalty.html', cards=cards)

@app.route('/client/bookings')
@client_required
def my_bookings():
    db = get_db()
    rows = db.execute("""
        SELECT q.*, b.salon_name, b.address, bu.phone as barber_phone
        FROM queue q
        JOIN barbers b ON b.id = q.barber_id
        JOIN users bu ON bu.id = b.user_id
        WHERE q.client_id=?
        ORDER BY q.created_at DESC
    """, (session['user_id'],)).fetchall()

    bookings = []
    for r in rows:
        services = db.execute("""
            SELECT s.name, s.price, s.duration FROM queue_services qs
            JOIN services s ON s.id = qs.service_id
            WHERE qs.queue_id=?
        """, (r['id'],)).fetchall()
        bookings.append({**dict(r), 'services': services})

    return render_template('my_bookings.html', bookings=bookings)

@app.route('/client/rate-barber-ajax', methods=['POST'])
@client_required
def rate_barber_ajax():
    db        = get_db()
    barber_id = int(request.form.get('barber_id', 0))
    rating    = int(request.form.get('rating', 0))
    review    = request.form.get('review_text', '').strip()

    if not (1 <= rating <= 5):
        return jsonify({'error': 'Invalid rating'}), 400

    existing = db.execute(
        "SELECT id FROM client_ratings WHERE barber_id=? AND client_id=?",
        (barber_id, session['user_id'])
    ).fetchone()
    if existing:
        db.execute("UPDATE client_ratings SET rating=?, review_text=? WHERE id=?",
                   (rating, review, existing['id']))
    else:
        db.execute(
            "INSERT INTO client_ratings (barber_id, client_id, rating, review_text) VALUES (?,?,?,?)",
            (barber_id, session['user_id'], rating, review)
        )
    db.commit()
    return jsonify({'success': True})

@app.route('/client/rate', methods=['POST'])
@client_required
def rate_barber():
    db        = get_db()
    barber_id = int(request.form.get('barber_id', 0))
    rating    = int(request.form.get('rating', 0))
    review    = request.form.get('review_text', '').strip()
    queue_id  = int(request.form.get('queue_id', 0))

    if not (1 <= rating <= 5):
        flash('Invalid rating.', 'error')
        return redirect(url_for('track', queue_id=queue_id))

    existing = db.execute(
        "SELECT id FROM client_ratings WHERE barber_id=? AND client_id=?",
        (barber_id, session['user_id'])
    ).fetchone()
    if existing:
        flash('You have already rated this barber.', 'error')
        return redirect(url_for('track', queue_id=queue_id))

    db.execute(
        "INSERT INTO client_ratings (barber_id, client_id, rating, review_text) VALUES (?,?,?,?)",
        (barber_id, session['user_id'], rating, review)
    )
    db.commit()
    flash('Thank you for your review!', 'success')
    return redirect(url_for('track', queue_id=queue_id))

# ═════════════════════════════════════════════════════════════════════════════
#  ROUTES — BARBER
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/barber/dashboard')
@barber_required
def barber_dashboard():
    db        = get_db()
    barber_id = session.get('barber_id')
    barber    = db.execute("""
        SELECT b.*, s.name as favor_service_name
        FROM barbers b
        LEFT JOIN services s ON s.id = b.favor_service_id
        WHERE b.id=?
    """, (barber_id,)).fetchone()
    services  = db.execute("SELECT * FROM services WHERE barber_id=?", (barber_id,)).fetchall()

    queue_rows = db.execute("""
        SELECT q.*, u.username, u.email, u.phone, u.no_show_count, u.loyalty_visits,
               (SELECT AVG(rating) FROM barber_ratings WHERE client_id = u.id) as client_avg_rating
        FROM queue q JOIN users u ON u.id = q.client_id
        WHERE q.barber_id=? AND q.status IN ('pending','waiting','ongoing')
        ORDER BY CASE q.status WHEN 'ongoing' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END, q.appointment_time
    """, (barber_id,)).fetchall()

    today = datetime.now().strftime('%Y-%m-%d')
    today_earnings = db.execute("""
        SELECT COALESCE(SUM(total_price),0) as total FROM queue
        WHERE barber_id=? AND status='done' AND DATE(created_at)=?
    """, (barber_id, today)).fetchone()['total']

    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%Y-%m-%d')
    week_earnings = db.execute("""
        SELECT COALESCE(SUM(total_price),0) as total FROM queue
        WHERE barber_id=? AND status='done' AND DATE(created_at)>=?
    """, (barber_id, week_start)).fetchone()['total']

    # Peak hours
    peak_hours = db.execute("""
        SELECT strftime('%H', appointment_time) as hour, COUNT(*) as cnt
        FROM queue WHERE barber_id=?
        GROUP BY hour ORDER BY hour
    """, (barber_id,)).fetchall()

    # Revenue last 7 days
    revenue_days = db.execute("""
        SELECT DATE(created_at) as day, COALESCE(SUM(total_price),0) as total
        FROM queue WHERE barber_id=? AND status='done'
          AND DATE(created_at) >= DATE('now', '-6 days')
        GROUP BY day ORDER BY day
    """, (barber_id,)).fetchall()

    # Gallery photos
    gallery_photos = db.execute(
        "SELECT * FROM gallery WHERE barber_id=? ORDER BY created_at DESC",
        (barber_id,)
    ).fetchall()

    # Closed days
    closed_dates_list = db.execute(
        "SELECT * FROM closed_dates WHERE barber_id=? AND closed_date >= DATE('now') ORDER BY closed_date",
        (barber_id,)
    ).fetchall()

    return render_template('barber_dashboard.html',
        barber=barber, services=services,
        queue_rows=queue_rows,
        today_earnings=today_earnings,
        week_earnings=week_earnings,
        peak_hours=[dict(r) for r in peak_hours],
        revenue_days=[dict(r) for r in revenue_days],
        gallery_photos=gallery_photos,
        closed_dates_list=closed_dates_list,
        today_str=datetime.now().strftime('%Y-%m-%d')
    )

@app.route('/profile/delete', methods=['POST'])
@login_required
def delete_account():
    db       = get_db()
    password = request.form.get('confirm_password', '')
    user     = db.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    if not verify_password(password, user['password_hash']):
        flash('Password incorrect. Account not deleted.', 'error')
        return redirect(url_for('edit_profile'))
    db.execute("DELETE FROM users WHERE id=?", (session['user_id'],))
    db.commit()
    session.clear()
    flash('Your account has been permanently deleted.', 'success')
    return redirect(url_for('index'))


@app.route('/barber/update-settings', methods=['POST'])
@barber_required
def barber_update_settings():
    db        = get_db()
    barber_id = session.get('barber_id')
    open_time        = request.form.get('open_time',  '09:00').strip()
    close_time       = request.form.get('close_time', '20:00').strip()
    favor_service_id = request.form.get('favor_service_id', '') or None
    if favor_service_id:
        favor_service_id = int(favor_service_id)

    if not re.match(r'^\d{2}:\d{2}$', open_time) or not re.match(r'^\d{2}:\d{2}$', close_time):
        flash('Invalid time format.', 'error')
        return redirect(url_for('barber_dashboard'))
    if open_time >= close_time:
        flash('Closing time must be after opening time.', 'error')
        return redirect(url_for('barber_dashboard'))

    # Validate favor_service_id belongs to this barber
    if favor_service_id:
        valid = db.execute("SELECT id FROM services WHERE id=? AND barber_id=?",
                           (favor_service_id, barber_id)).fetchone()
        if not valid:
            favor_service_id = None

    max_daily_clients = int(request.form.get('max_daily_clients', 0) or 0)

    db.execute("""
        UPDATE barbers SET open_time=?, close_time=?, favor_service_id=?, max_daily_clients=?
        WHERE id=?
    """, (open_time, close_time, favor_service_id, max_daily_clients, barber_id))
    db.commit()
    flash('Settings saved.', 'success')
    return redirect(url_for('barber_dashboard'))

# ── Closed Dates (barber manually disables specific days) ───────────────────
@app.route('/barber/closed-dates', methods=['POST'])
@barber_required
def manage_closed_dates():
    db        = get_db()
    barber_id = session.get('barber_id')
    action    = request.form.get('action')

    if action == 'add':
        closed_date = request.form.get('closed_date', '').strip()
        reason      = request.form.get('reason', '').strip()

        if not re.match(r'^\d{4}-\d{2}-\d{2}$', closed_date):
            flash('Invalid date.', 'error')
            return redirect(url_for('barber_dashboard'))

        try:
            db.execute(
                "INSERT INTO closed_dates (barber_id, closed_date, reason) VALUES (?,?,?)",
                (barber_id, closed_date, reason)
            )
            db.commit()
            flash('Day marked as closed.', 'success')
        except sqlite3.IntegrityError:
            flash('This date is already closed.', 'error')

    elif action == 'delete':
        closed_id = int(request.form.get('closed_id', 0))
        db.execute("DELETE FROM closed_dates WHERE id=? AND barber_id=?", (closed_id, barber_id))
        db.commit()
        flash('Day reopened.', 'success')

    return redirect(url_for('barber_dashboard'))

@app.route('/api/closed-dates/<int:barber_id>')
def api_closed_dates(barber_id):
    """Public endpoint — used by the date picker on the booking page."""
    db   = get_db()
    rows = db.execute(
        "SELECT closed_date, reason FROM closed_dates WHERE barber_id=? AND closed_date >= DATE('now')",
        (barber_id,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ── Fully Booked API — used by booking page date picker ──────────────────────
@app.route('/api/fully-booked/<int:barber_id>')
def api_fully_booked(barber_id):
    """Return list of dates (next 30 days) that are fully booked for this barber."""
    db     = get_db()
    barber = db.execute("SELECT max_daily_clients FROM barbers WHERE id=?", (barber_id,)).fetchone()
    if not barber or not barber['max_daily_clients']:
        return jsonify({'full_dates': [], 'max_daily_clients': 0})
    limit = barber['max_daily_clients']
    rows = db.execute("""
        SELECT DATE(appointment_time) as d, COUNT(*) as c
        FROM queue
        WHERE barber_id=?
          AND DATE(appointment_time) >= DATE('now')
          AND DATE(appointment_time) <= DATE('now', '+30 days')
          AND status IN ('waiting','ongoing','pending','done')
        GROUP BY d HAVING c >= ?
    """, (barber_id, limit)).fetchall()
    return jsonify({'full_dates': [r['d'] for r in rows], 'max_daily_clients': limit})


# ── Booked Slots API — returns occupied time ranges for a barber on a date ──
@app.route('/api/booked-slots/<int:barber_id>/<date_str>')
def api_booked_slots(barber_id, date_str):
    """
    Returns all booked time ranges for a barber on a given date (YYYY-MM-DD).
    Each entry: { start: 'HH:MM', end: 'HH:MM', duration: N }
    The client-side slot picker uses this to gray out overlapping slots.
    """
    import re as _re
    if not _re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return jsonify({'error': 'Invalid date'}), 400
    db   = get_db()
    rows = db.execute("""
        SELECT appointment_time, assigned_duration
        FROM queue
        WHERE barber_id=?
          AND DATE(appointment_time) = ?
          AND status IN ('waiting', 'ongoing', 'pending')
        ORDER BY appointment_time
    """, (barber_id, date_str)).fetchall()
    slots = []
    for r in rows:
        try:
            start_dt = datetime.strptime(r['appointment_time'], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        end_dt = start_dt + timedelta(minutes=r['assigned_duration'])
        slots.append({
            'start': start_dt.strftime('%H:%M'),
            'end':   end_dt.strftime('%H:%M'),
            'duration': r['assigned_duration']
        })
    return jsonify({'booked': slots})


# ── Waitlist — client views their waitlist entries ───────────────────────────
@app.route('/client/waitlist')
@client_required
def my_waitlist():
    db        = get_db()
    client_id = session['user_id']
    entries   = db.execute("""
        SELECT w.*, b.salon_name, u.phone as barber_phone
        FROM waitlist w
        JOIN barbers b ON b.id = w.barber_id
        JOIN users u ON u.id = b.user_id
        WHERE w.client_id=? AND w.status IN ('waiting','notified')
        ORDER BY w.requested_date ASC
    """, (client_id,)).fetchall()
    return jsonify([dict(e) for e in entries])


@app.route('/client/waitlist/cancel/<int:wl_id>', methods=['POST'])
@client_required
def cancel_waitlist(wl_id):
    db        = get_db()
    client_id = session['user_id']
    row = db.execute("SELECT id FROM waitlist WHERE id=? AND client_id=?", (wl_id, client_id)).fetchone()
    if not row:
        flash('Waitlist entry not found.', 'error')
    else:
        db.execute("UPDATE waitlist SET status='cancelled' WHERE id=?", (wl_id,))
        db.commit()
        flash('Removed from waitlist.', 'success')
    return redirect(url_for('client_home'))


# ── Barber: notify waitlist when a cancellation frees up a slot ──────────────
def notify_waitlist(barber_id, freed_date, db):
    """Called when a booking is cancelled — email first person on waitlist if day is no longer full."""
    if is_day_full(barber_id, freed_date, db):
        return  # Still full even after cancellation
    next_person = db.execute("""
        SELECT w.*, u.email, u.username FROM waitlist w
        JOIN users u ON u.id = w.client_id
        WHERE w.barber_id=? AND w.requested_date=? AND w.status='waiting'
        ORDER BY w.created_at ASC LIMIT 1
    """, (barber_id, freed_date)).fetchone()
    if not next_person:
        return
    barber_row = db.execute(
        "SELECT salon_name FROM barbers WHERE id=?", (barber_id,)
    ).fetchone()
    subject = f"Good news! A slot opened up at {barber_row['salon_name']}"
    body = f"""<p>Hi {next_person['username']},</p>
    <p>A booking slot has opened up at <strong>{barber_row['salon_name']}</strong> on
    <strong>{next_person['requested_date']}</strong>.</p>
    <p>Log in now to secure your spot before it's taken!</p>
    <p style="margin-top:20px"><a href="http://127.0.0.1:5000/client"
    style="background:#d4af37;color:#000;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:bold">
    Book My Slot →</a></p>"""
    if send_email(next_person['email'], subject, body):
        db.execute(
            "UPDATE waitlist SET status='notified', notified_at=CURRENT_TIMESTAMP WHERE id=?",
            (next_person['id'],)
        )
        db.commit()


# ── Profile Edit (shared: barber + client) ───────────────────────────────────
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()

    # Fetch barber info if applicable
    barber = None
    if session.get('role') == 'barber':
        barber = db.execute("SELECT * FROM barbers WHERE user_id=?", (session['user_id'],)).fetchone()

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        phone    = request.form.get('phone', '').strip()
        email    = request.form.get('email', '').strip().lower()
        new_pass = request.form.get('new_password', '').strip()
        cur_pass = request.form.get('current_password', '').strip()

        if not username or not phone or not email:
            flash('Name, phone and email are required.', 'error')
            return render_template('edit_profile.html', user=user, barber=barber)

        # Verify current password
        if not verify_password(cur_pass, user['password_hash']):
            flash('Current password is incorrect.', 'error')
            return render_template('edit_profile.html', user=user, barber=barber)

        # Check email uniqueness (not self)
        taken = db.execute("SELECT id FROM users WHERE email=? AND id!=?",
                            (email, session['user_id'])).fetchone()
        if taken:
            flash('This email is already used by another account.', 'error')
            return render_template('edit_profile.html', user=user, barber=barber)

        if new_pass:
            if len(new_pass) < 6:
                flash('New password must be at least 6 characters.', 'error')
                return render_template('edit_profile.html', user=user, barber=barber)
            db.execute("UPDATE users SET username=?, phone=?, email=?, password_hash=? WHERE id=?",
                       (username, phone, email, hash_password(new_pass), session['user_id']))
        else:
            db.execute("UPDATE users SET username=?, phone=?, email=? WHERE id=?",
                       (username, phone, email, session['user_id']))

        # Update barber location fields if barber
        if session.get('role') == 'barber' and barber:
            salon_name = request.form.get('salon_name', '').strip()
            address    = request.form.get('address', '').strip()
            maps_link  = request.form.get('maps_link', '').strip()
            if not salon_name or not address:
                flash('Salon name and address are required.', 'error')
                return render_template('edit_profile.html', user=user, barber=barber)
            db.execute(
                "UPDATE barbers SET salon_name=?, address=?, maps_link=? WHERE user_id=?",
                (salon_name, address, maps_link, session['user_id'])
            )

        db.commit()
        session['username'] = username
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('edit_profile'))

    return render_template('edit_profile.html', user=user, barber=barber)



@app.route('/barber/client-history-json/<int:client_id>')
@barber_required
def client_history_json(client_id):
    db        = get_db()
    barber_id = session.get('barber_id')
    client = db.execute("SELECT id,username,email,phone,no_show_count,loyalty_visits FROM users WHERE id=?",
                        (client_id,)).fetchone()
    if not client:
        return jsonify({'error': 'Not found'}), 404
    visits = db.execute("""
        SELECT q.id, q.status, q.total_price, q.appointment_time, q.loyalty_reward,
               GROUP_CONCAT(s.name, ', ') as services_list
        FROM queue q
        LEFT JOIN queue_services qs ON qs.queue_id = q.id
        LEFT JOIN services s ON s.id = qs.service_id
        WHERE q.client_id=? AND q.barber_id=?
        GROUP BY q.id ORDER BY q.appointment_time DESC
    """, (client_id, barber_id)).fetchall()
    loyalty = db.execute("SELECT stamps,total_earned FROM loyalty_cards WHERE client_id=? AND barber_id=?",
                         (client_id, barber_id)).fetchone()
    total_spent = db.execute("""SELECT COALESCE(SUM(total_price),0) as t FROM queue
        WHERE client_id=? AND barber_id=? AND status='done'""",
        (client_id, barber_id)).fetchone()['t']
    return jsonify({
        'client': dict(client),
        'visits': [dict(v) for v in visits],
        'loyalty': dict(loyalty) if loyalty else None,
        'total_spent': total_spent,
        'visit_count': len(visits)
    })

@app.route('/barber/client-history/<int:client_id>')
@barber_required
def client_history(client_id):
    db        = get_db()
    barber_id = session.get('barber_id')

    client = db.execute("SELECT id, username, email, phone, no_show_count, loyalty_visits FROM users WHERE id=?",
                        (client_id,)).fetchone()
    if not client:
        flash('Client not found.', 'error')
        return redirect(url_for('barber_dashboard'))

    visits = db.execute("""
        SELECT q.id, q.status, q.total_price, q.appointment_time, q.assigned_duration, q.loyalty_reward,
               GROUP_CONCAT(s.name, ', ') as services_list
        FROM queue q
        LEFT JOIN queue_services qs ON qs.queue_id = q.id
        LEFT JOIN services s ON s.id = qs.service_id
        WHERE q.client_id=? AND q.barber_id=?
        GROUP BY q.id
        ORDER BY q.appointment_time DESC
    """, (client_id, barber_id)).fetchall()

    loyalty = db.execute(
        "SELECT stamps, total_earned FROM loyalty_cards WHERE client_id=? AND barber_id=?",
        (client_id, barber_id)
    ).fetchone()

    barber_rating = db.execute(
        "SELECT rating, note, created_at FROM barber_ratings WHERE client_id=? AND barber_id=? ORDER BY created_at DESC LIMIT 1",
        (client_id, barber_id)
    ).fetchone()

    total_spent = db.execute("""
        SELECT COALESCE(SUM(total_price),0) as total FROM queue
        WHERE client_id=? AND barber_id=? AND status='done'
    """, (client_id, barber_id)).fetchone()['total']

    return render_template('client_history.html',
        client=client, visits=visits, loyalty=loyalty,
        barber_rating=barber_rating, total_spent=total_spent)

@app.route('/barber/toggle-open', methods=['POST'])
@barber_required
def toggle_open():
    db        = get_db()
    barber_id = session.get('barber_id')
    barber    = db.execute("SELECT is_open FROM barbers WHERE id=?", (barber_id,)).fetchone()
    new_state = 0 if barber['is_open'] else 1
    db.execute("UPDATE barbers SET is_open=? WHERE id=?", (new_state, barber_id))
    db.commit()
    return jsonify({'is_open': new_state})

@app.route('/barber/queue/<int:queue_id>/action', methods=['POST'])
@barber_required
def queue_action(queue_id):
    db        = get_db()
    barber_id = session.get('barber_id')
    action    = request.form.get('action')

    row = db.execute("SELECT * FROM queue WHERE id=? AND barber_id=?",
                     (queue_id, barber_id)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    if action == 'accept' and row['status'] == 'pending':
        db.execute("UPDATE queue SET status='waiting' WHERE id=?", (queue_id,))

    elif action == 'reject' and row['status'] in ('pending', 'waiting'):
        db.execute("UPDATE queue SET status='rejected' WHERE id=?", (queue_id,))

    elif action == 'start' and row['status'] == 'waiting':
        # Only one ongoing at a time
        ongoing = db.execute("SELECT id FROM queue WHERE barber_id=? AND status='ongoing'", (barber_id,)).fetchone()
        if ongoing:
            return jsonify({'error': 'Another client is already ongoing'}), 400
        db.execute("UPDATE queue SET status='ongoing' WHERE id=?", (queue_id,))

    elif action == 'done' and row['status'] == 'ongoing':
        db.execute("UPDATE queue SET status='done' WHERE id=?", (queue_id,))
        # Global loyalty counter (kept for backward compat)
        db.execute("UPDATE users SET loyalty_visits = loyalty_visits + 1 WHERE id=?", (row['client_id'],))
        # Per-barber loyalty card stamp
        db.execute("""
            INSERT INTO loyalty_cards (client_id, barber_id, stamps, total_earned, updated_at)
            VALUES (?, ?, 1, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(client_id, barber_id) DO UPDATE SET
                stamps = stamps + 1,
                updated_at = CURRENT_TIMESTAMP
        """, (row['client_id'], barber_id))
        card = db.execute(
            "SELECT stamps FROM loyalty_cards WHERE client_id=? AND barber_id=?",
            (row['client_id'], barber_id)
        ).fetchone()
        if card['stamps'] % 10 == 0:
            # Reset stamps to 0, increment total_earned (rewards won)
            db.execute("""
                UPDATE loyalty_cards SET stamps=0, total_earned=total_earned+1, updated_at=CURRENT_TIMESTAMP
                WHERE client_id=? AND barber_id=?
            """, (row['client_id'], barber_id))
            db.execute("UPDATE queue SET loyalty_reward=1 WHERE id=?", (queue_id,))
            # Send SSE notification to client (via barber stream which client also listens)
            push_sse(barber_id, json.dumps({
                'event': 'loyalty_reward',
                'client_id': row['client_id'],
                'queue_id': queue_id,
                'barber_name': db.execute("SELECT salon_name FROM barbers WHERE id=?", (barber_id,)).fetchone()['salon_name']
            }))

    elif action == 'noshow' and row['status'] == 'ongoing':
        db.execute("UPDATE queue SET status='noshow' WHERE id=?", (queue_id,))
        db.execute("UPDATE users SET no_show_count = no_show_count + 1 WHERE id=?", (row['client_id'],))
        # Check new count → block on 4th
        updated = db.execute("SELECT no_show_count, phone, username FROM users WHERE id=?", (row['client_id'],)).fetchone()
        if updated['no_show_count'] >= 4:
            block_until = (datetime.now() + timedelta(weeks=2)).strftime('%Y-%m-%d %H:%M:%S')
            db.execute("UPDATE users SET blocked_until=? WHERE id=?", (block_until, row['client_id']))
        # Notify barber dashboard in real-time of the no-show flag
        push_sse(barber_id, json.dumps({
            'event': 'noshow_flagged',
            'client_name': updated['username'],
            'no_show_count': updated['no_show_count']
        }))

    db.commit()
    push_sse(barber_id, json.dumps({'event': 'queue_update', 'queue_id': queue_id, 'action': action}))
    return jsonify({'success': True})

@app.route('/barber/rate-client', methods=['POST'])
@barber_required
def rate_client():
    db        = get_db()
    barber_id = session.get('barber_id')
    queue_id  = int(request.form.get('queue_id', 0))
    rating    = int(request.form.get('rating', 0))
    note      = request.form.get('note', '').strip()

    if not (1 <= rating <= 5):
        return jsonify({'error': 'Invalid rating'}), 400

    row = db.execute("SELECT * FROM queue WHERE id=? AND barber_id=?",
                     (queue_id, barber_id)).fetchone()
    if not row:
        return jsonify({'error': 'Booking not found'}), 404

    existing = db.execute(
        "SELECT id FROM barber_ratings WHERE barber_id=? AND queue_id=?",
        (barber_id, queue_id)
    ).fetchone()
    if existing:
        db.execute("UPDATE barber_ratings SET rating=?, note=? WHERE id=?",
                   (rating, note, existing['id']))
    else:
        db.execute(
            "INSERT INTO barber_ratings (barber_id, client_id, queue_id, rating, note) VALUES (?,?,?,?,?)",
            (barber_id, row['client_id'], queue_id, rating, note)
        )
    db.commit()
    return jsonify({'success': True})

@app.route('/barber/services', methods=['POST'])
@barber_required
def manage_services():
    db        = get_db()
    barber_id = session.get('barber_id')
    action    = request.form.get('action')

    if action == 'add':
        name     = request.form.get('name', '').strip()
        price    = float(request.form.get('price', 0))
        duration = int(request.form.get('duration', 0))
        if name and price >= 0 and duration > 0:
            db.execute("INSERT INTO services (barber_id, name, price, duration) VALUES (?,?,?,?)",
                       (barber_id, name, price, duration))
            # Update price range
            db.execute("""
                UPDATE barbers SET
                  min_price = (SELECT MIN(price) FROM services WHERE barber_id=?),
                  max_price = (SELECT MAX(price) FROM services WHERE barber_id=?)
                WHERE id=?
            """, (barber_id, barber_id, barber_id))
            db.commit()
            flash('Service added.', 'success')

    elif action == 'delete':
        service_id = int(request.form.get('service_id', 0))
        db.execute("DELETE FROM services WHERE id=? AND barber_id=?", (service_id, barber_id))
        db.execute("""
            UPDATE barbers SET
              min_price = COALESCE((SELECT MIN(price) FROM services WHERE barber_id=?), 0),
              max_price = COALESCE((SELECT MAX(price) FROM services WHERE barber_id=?), 0)
            WHERE id=?
        """, (barber_id, barber_id, barber_id))
        db.commit()
        flash('Service removed.', 'success')

    return redirect(url_for('barber_dashboard'))

# ═════════════════════════════════════════════════════════════════════════════
#  ROUTES — SSE
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/stream/<int:barber_id>')
@login_required
def sse_stream(barber_id):
    import queue as qmod

    def event_stream():
        q = qmod.Queue()
        with _sse_lock:
            _sse_subscribers.setdefault(barber_id, []).append(q)
        try:
            # Send initial heartbeat
            yield "data: {\"event\": \"connected\"}\n\n"
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f"data: {data}\n\n"
                except qmod.Empty:
                    yield "data: {\"event\": \"heartbeat\"}\n\n"
        finally:
            with _sse_lock:
                subs = _sse_subscribers.get(barber_id, [])
                if q in subs:
                    subs.remove(q)

    return Response(event_stream(),
                    mimetype='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache',
                        'X-Accel-Buffering': 'no'
                    })

# ═════════════════════════════════════════════════════════════════════════════
#  ROUTES — API (JSON endpoints for live updates)
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/queue-status/<int:queue_id>')
@client_required
def api_queue_status(queue_id):
    db  = get_db()
    row = db.execute("SELECT status, assigned_duration, appointment_time, barber_id FROM queue WHERE id=? AND client_id=?",
                     (queue_id, session['user_id'])).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    wait = compute_wait_minutes(row['barber_id'], queue_id, db)
    return jsonify({
        'status': row['status'],
        'wait_minutes': wait,
        'assigned_duration': row['assigned_duration'],
        'appointment_time': row['appointment_time']
    })

@app.route('/api/barber-queue/<int:barber_id>')
@barber_required
def api_barber_queue(barber_id):
    if session.get('barber_id') != barber_id:
        return jsonify({'error': 'Forbidden'}), 403
    db = get_db()
    rows = db.execute("""
        SELECT q.id, q.status, q.total_price, q.appointment_time,
               q.assigned_duration, q.loyalty_reward,
               u.username, u.phone, u.no_show_count,
               (SELECT AVG(rating) FROM barber_ratings WHERE client_id = u.id) as client_avg_rating
        FROM queue q JOIN users u ON u.id=q.client_id
        WHERE q.barber_id=? AND q.status IN ('pending','waiting','ongoing')
        ORDER BY CASE q.status WHEN 'ongoing' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END, q.appointment_time
    """, (barber_id,)).fetchall()
    return jsonify([dict(r) for r in rows])

# ═════════════════════════════════════════════════════════════════════════════
#  QR CODE — barber profile QR
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/qr/<int:barber_id>')
def barber_qr_page(barber_id):
    """Public page shown after scanning QR — walk-in check-in."""
    db     = get_db()
    barber = db.execute("""
        SELECT b.*, u.username, u.phone as barber_phone,
               s.name as favor_service_name
        FROM barbers b
        JOIN users u ON u.id = b.user_id
        LEFT JOIN services s ON s.id = b.favor_service_id
        WHERE b.id=?
    """, (barber_id,)).fetchone()
    if not barber:
        return "Barber not found", 404

    services  = db.execute("SELECT * FROM services WHERE barber_id=?", (barber_id,)).fetchall()
    gallery   = db.execute("SELECT * FROM gallery WHERE barber_id=? ORDER BY created_at DESC LIMIT 9", (barber_id,)).fetchall()

    # Live queue size
    queue_count = db.execute("""
        SELECT COUNT(*) as c FROM queue
        WHERE barber_id=? AND status IN ('waiting','ongoing')
    """, (barber_id,)).fetchone()['c']
    walkin_count = db.execute("""
        SELECT COUNT(*) as c FROM walkin
        WHERE barber_id=? AND status IN ('waiting','ongoing')
    """, (barber_id,)).fetchone()['c']

    avg_rating = db.execute(
        "SELECT COALESCE(AVG(rating),0) as r, COUNT(*) as c FROM client_ratings WHERE barber_id=?",
        (barber_id,)
    ).fetchone()

    return render_template('barber_public.html',
        barber=barber, services=services, gallery=gallery,
        queue_count=queue_count, walkin_count=walkin_count,
        avg_rating=avg_rating)

@app.route('/barber/qrcode')
@barber_required
def barber_qrcode():
    """Show QR code for the barber's public walk-in page."""
    barber_id = session.get('barber_id')
    base_url  = request.host_url.rstrip('/')
    qr_url    = f"{base_url}/qr/{barber_id}"
    qr_svg    = generate_qr_svg(qr_url)
    return render_template('qrcode_page.html', qr_svg=qr_svg, qr_url=qr_url)

# ═════════════════════════════════════════════════════════════════════════════
#  WALK-IN CHECK-IN
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/walkin/<int:barber_id>', methods=['POST'])
def walkin_checkin(barber_id):
    db   = get_db()
    name = request.form.get('name', '').strip()
    phone= request.form.get('phone', '').strip()

    if not name or not phone:
        flash('Name and phone number are required.', 'error')
        return redirect(url_for('barber_qr_page', barber_id=barber_id))

    barber = db.execute("SELECT is_open, open_time, close_time FROM barbers WHERE id=?", (barber_id,)).fetchone()
    if not barber or not barber['is_open']:
        flash('This salon is currently closed. Please come back later.', 'error')
        return redirect(url_for('barber_qr_page', barber_id=barber_id))

    # Check hours
    now_time = datetime.now().strftime('%H:%M')
    if now_time < barber['open_time'] or now_time >= barber['close_time']:
        flash(f"This salon is only open between {barber['open_time']} and {barber['close_time']}.", 'error')
        return redirect(url_for('barber_qr_page', barber_id=barber_id))

    cur = db.execute(
        "INSERT INTO walkin (barber_id, client_name, client_phone) VALUES (?,?,?)",
        (barber_id, name, phone)
    )
    walkin_id = cur.lastrowid

    # Compute wait
    ongoing = db.execute("""
        SELECT assigned_duration FROM walkin WHERE barber_id=? AND status='ongoing'
    """, (barber_id,)).fetchone()
    waiting_sum = db.execute("""
        SELECT COALESCE(SUM(assigned_duration),0) as s FROM walkin
        WHERE barber_id=? AND status='waiting' AND id < ?
    """, (barber_id, walkin_id)).fetchone()['s']
    wait_mins = (ongoing['assigned_duration'] if ongoing else 0) + waiting_sum

    db.commit()
    push_sse(barber_id, json.dumps({'event': 'walkin', 'walkin_id': walkin_id}))

    return render_template('walkin_confirm.html',
        name=name, phone=phone, wait_mins=wait_mins,
        walkin_id=walkin_id, barber_id=barber_id)

@app.route('/walkin/status/<int:walkin_id>')
def walkin_status(walkin_id):
    db  = get_db()
    row = db.execute("SELECT * FROM walkin WHERE id=?", (walkin_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    ongoing = db.execute("SELECT assigned_duration FROM walkin WHERE barber_id=? AND status='ongoing'",
                         (row['barber_id'],)).fetchone()
    waiting_sum = db.execute("""
        SELECT COALESCE(SUM(assigned_duration),0) as s FROM walkin
        WHERE barber_id=? AND status='waiting' AND id <= ?
    """, (row['barber_id'], walkin_id)).fetchone()['s']
    wait = (ongoing['assigned_duration'] if ongoing else 0) + waiting_sum
    return jsonify({'status': row['status'], 'wait_minutes': wait, 'name': row['client_name']})

@app.route('/barber/walkin/<int:walkin_id>/action', methods=['POST'])
@barber_required
def walkin_action(walkin_id):
    db        = get_db()
    barber_id = session.get('barber_id')
    action    = request.form.get('action')
    row = db.execute("SELECT * FROM walkin WHERE id=? AND barber_id=?", (walkin_id, barber_id)).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    if action == 'start' and row['status'] == 'waiting':
        db.execute("UPDATE walkin SET status='ongoing' WHERE id=?", (walkin_id,))
    elif action == 'done' and row['status'] == 'ongoing':
        db.execute("UPDATE walkin SET status='done' WHERE id=?", (walkin_id,))
    elif action == 'cancel':
        db.execute("UPDATE walkin SET status='cancelled' WHERE id=?", (walkin_id,))
    db.commit()
    push_sse(barber_id, json.dumps({'event': 'walkin_update'}))
    return jsonify({'success': True})

@app.route('/api/walkin-queue/<int:barber_id>')
@barber_required
def api_walkin_queue(barber_id):
    if session.get('barber_id') != barber_id:
        return jsonify({'error': 'Forbidden'}), 403
    db   = get_db()
    rows = db.execute("""
        SELECT * FROM walkin WHERE barber_id=? AND status IN ('waiting','ongoing')
        ORDER BY CASE status WHEN 'ongoing' THEN 0 ELSE 1 END, created_at
    """, (barber_id,)).fetchall()
    return jsonify([dict(r) for r in rows])

# ═════════════════════════════════════════════════════════════════════════════
#  GALLERY
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/barber/gallery', methods=['POST'])
@barber_required
def manage_gallery():
    db        = get_db()
    barber_id = session.get('barber_id')
    action    = request.form.get('action')

    if action == 'add_url':
        url     = request.form.get('image_url', '').strip()
        caption = request.form.get('caption', '').strip()
        if url:
            db.execute("INSERT INTO gallery (barber_id, image_url, caption, source) VALUES (?,?,?,'url')",
                       (barber_id, url, caption))
            db.commit()
            flash('Image added.', 'success')
        else:
            flash('URL is required.', 'error')

    elif action == 'upload':
        caption = request.form.get('caption', '').strip()
        file    = request.files.get('image_file')
        if not file or not file.filename:
            flash('No file selected.', 'error')
        elif not allowed_file(file.filename):
            flash('File type not allowed. Use JPG, PNG or WEBP.', 'error')
        elif file.content_length and file.content_length > MAX_FILE_MB * 1024 * 1024:
            flash(f'File too large. Max {MAX_FILE_MB}MB.', 'error')
        else:
            fname = safe_filename(file.filename)
            fpath = os.path.join(UPLOAD_DIR, fname)
            file.save(fpath)
            url = f"/static/uploads/{fname}"
            db.execute("INSERT INTO gallery (barber_id, image_url, caption, source) VALUES (?,?,?,'upload')",
                       (barber_id, url, caption))
            db.commit()
            flash('Photo uploaded.', 'success')

    elif action == 'delete':
        photo_id = int(request.form.get('photo_id', 0))
        row = db.execute("SELECT * FROM gallery WHERE id=? AND barber_id=?", (photo_id, barber_id)).fetchone()
        if row:
            if row['source'] == 'upload':
                fpath = os.path.join(os.path.dirname(__file__), row['image_url'].lstrip('/'))
                if os.path.exists(fpath):
                    os.remove(fpath)
            db.execute("DELETE FROM gallery WHERE id=?", (photo_id,))
            db.commit()
            flash('Photo deleted.', 'success')

    return redirect(url_for('barber_dashboard'))

@app.route('/gallery/<int:barber_id>')
def public_gallery(barber_id):
    db     = get_db()
    photos = db.execute("SELECT * FROM gallery WHERE barber_id=? ORDER BY created_at DESC", (barber_id,)).fetchall()
    barber = db.execute("SELECT salon_name FROM barbers WHERE id=?", (barber_id,)).fetchone()
    return jsonify({'photos': [dict(p) for p in photos], 'salon': barber['salon_name'] if barber else ''})

# ═════════════════════════════════════════════════════════════════════════════
#  CLIENT LIST — barber sees all clients who visited
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/barber/clients')
@barber_required
def barber_clients():
    db        = get_db()
    barber_id = session.get('barber_id')
    clients   = db.execute("""
        SELECT u.id, u.username, u.phone, u.email, u.no_show_count,
               COUNT(q.id) as total_visits,
               COALESCE(SUM(CASE WHEN q.status='done' THEN q.total_price ELSE 0 END), 0) as total_spent,
               MAX(q.appointment_time) as last_visit,
               lc.stamps, lc.total_earned,
               COALESCE(AVG(br.rating), 0) as my_rating
        FROM queue q
        JOIN users u ON u.id = q.client_id
        LEFT JOIN loyalty_cards lc ON lc.client_id = u.id AND lc.barber_id = q.barber_id
        LEFT JOIN barber_ratings br ON br.client_id = u.id AND br.barber_id = q.barber_id
        WHERE q.barber_id=?
        GROUP BY u.id
        ORDER BY total_visits DESC
    """, (barber_id,)).fetchall()
    return jsonify([dict(c) for c in clients])

# ═════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ═════════════════════════════════════════════════════════════════════════════

# Initialize DB and background worker on startup (works for both gunicorn and direct run)
init_db()
_email_thread = threading.Thread(target=email_worker, daemon=True)
_email_thread.start()

# Route 1: Kat-lister chno kayn f volume
@app.route('/admin/list-files')
def list_files():
    try:
        files = os.listdir('/app/data')
        return {"files_in_volume": files}
    except Exception as e:
        return f"Error listing files: {str(e)}"

# Route 2: Kat-downloadi biha tahssina.db direct
@app.route('/admin/download/tahssina.db')
def download_file():
    try:
        file_path = '/app/data/tahssina.db'
        if not os.path.exists(file_path):
            return "Error: Le fichier tahssina.db introuvable!"
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        return f"Error downloading file: {str(e)}"



@app.route('/admin/upload-db', methods=['POST'])
def upload_db():
    try:
        # T-t2ked bli l-file "db_file" t-sift f l-request
        file = request.files.get('db_file')
        if not file:
            return "Error: No file uploaded"
        
        # Save-i l-file f blastek f l-production volume
        file.save('/app/data/tahssina.db')
        return "Database updated successfully! Restart the app on Railway to take effect."
    except Exception as e:
        return f"Error: {str(e)}"
        

if __name__ == '__main__':
    app.run(debug=True, threaded=True, port=5000)
