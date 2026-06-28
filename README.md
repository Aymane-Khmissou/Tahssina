# Tahsina (تحسينة) — Real-Time Barbershop Queue Management

A mobile-first, fully responsive web app to digitalize barbershop queues.

## Tech Stack
- Backend: Python Flask (with background threading for email reminders)
- Database: SQLite (`tahsina.db`, auto-created on first run)
- Frontend: Semantic HTML5, Vanilla CSS3 (custom properties, flexbox/grid), Vanilla JS (ES6+)
- Charts: Chart.js (CDN)
- Real-time: Server-Sent Events (SSE)

# Tahsina (تحسينة) — Real-Time Barbershop Queue Management

A mobile-first, fully responsive web app to digitalize barbershop queues.

## Tech Stack
- Backend: Python Flask (with background threading for email reminders)
- Database: SQLite (`tahsina.db`, auto-created on first run)
- Frontend: Semantic HTML5, Vanilla CSS3 (custom properties, flexbox/grid), Vanilla JS (ES6+)
- Charts: Chart.js (CDN)
- Real-time: Server-Sent Events (SSE)
- QR Codes: `qrcode` library (falls back to a public QR API if not installed)

## Setup

```bash
pip install flask qrcode pillow
python3 app.py
```

The app initializes the SQLite database automatically on first run and starts
on http://127.0.0.1:5000

## Security Notes

- **Passwords**: hashed with Werkzeug's PBKDF2 (salted). Legacy SHA-256 hashes
  from earlier versions are still verified correctly for backward compatibility,
  but every login/registration after this update uses the stronger scheme.
- **Login rate limiting**: 8 failed attempts per IP locks login for 5 minutes
  (in-memory, resets on app restart — fine for small deployments; swap in
  Redis/Flask-Limiter for production scale).
- **File uploads**: capped server-side at 8MB (`MAX_CONTENT_LENGTH`), extension
  allow-list (jpg/jpeg/png/webp/gif only), filenames sanitized and randomized
  to prevent path traversal or executable uploads.
- **Authorization**: every barber-only/client-only route checks `session['role']`;
  every resource lookup (queue entries, gallery photos, walk-ins, services,
  client history) is scoped by `barber_id`/`client_id` in the SQL query itself,
  not just the URL, to prevent IDOR (one barber/client accessing another's data).
- **SQL injection**: all queries use parameterized statements (`?` placeholders),
  never string formatting — verified safe in registration, login, and search.

This app has been tested with 50+ functional and security test cases covering:
auth/authorization on every route, IDOR across barbers and clients, SQL
injection attempts, XSS escaping, file upload validation, anti-spam booking
limits, working-hours/overlap validation, and password-confirmation flows for
profile edits and account deletion.

## Changelog (latest)

- Fixed `BuildError` crash on `/barber/dashboard` caused by a malformed
  `barber_update_settings` route (decorator had been separated from its
  function during a prior edit).
- Upgraded password hashing from raw SHA-256 to salted PBKDF2.
- Added login rate limiting (brute-force protection).
- Added hard server-side upload size cap (8MB).


## Email Reminders (optional)
Set these environment variables to enable real email sending (SMTP). Without them,
emails are logged to console instead of sent:

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=your_email@gmail.com
export SMTP_PASS=your_app_password
export EMAIL_FROM=noreply@tahsina.app
```

### Important — Gmail requires an "App Password", not your normal password
Gmail will reject normal account passwords for SMTP. To fix "email not sending":

1. Enable 2-Step Verification on your Google account (myaccount.google.com/security)
2. Go to myaccount.google.com/apppasswords
3. Create an App Password (choose "Mail" / "Other")
4. Use that 16-character password as `SMTP_PASS` (not your real Gmail password)
5. `SMTP_USER` and `EMAIL_FROM` should both be your full Gmail address

If `SMTP_USER` is empty, the app just prints emails to the console/log — this is
the default and is why no real email arrives until you configure the above.

Two emails are sent automatically per booking:
- **Instant confirmation** — sent the moment a booking is created
- **2-hour reminder** — sent automatically by the background thread before the appointment

## Project Structure

```
tahsina/
├── app.py              # Flask application (routes, business logic, SSE, email worker)
├── schema.sql          # SQLite schema
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── register.html
│   ├── client_home.html
│   ├── book.html
│   ├── track.html
│   └── barber_dashboard.html
└── static/
    ├── css/style.css
    └── js/app.js
```

## Core Features
- Client booking with multi-service selection and live price calculation
- Date-pill + time-slot booking picker (next 8 days, working-hours-aware)
- Barber-managed closed days (specific dates, optional custom reason shown to client)
- Time-slot overlap validation
- Anti-spam: 1 active booking/day per client
- No-show penalty system (3+ no-shows → manual review required)
- Live queue tracking with SVG progress ring for "in the chair" status
- Barber dashboard: accept/reject/start/done/no-show queue controls
- Loyalty engine (every 10th visit = reward)
- Revenue & peak-hour analytics (Chart.js)
- Automated 2-hour-before email reminders (background thread)
- Google Maps / Waze quick navigation links
- Client ratings & reviews

## Changelog (latest)

- Fixed `BuildError` crash on `/barber/dashboard` caused by a malformed
  `barber_update_settings` route (decorator had been separated from its
  function during a prior edit).
- Upgraded password hashing from raw SHA-256 to salted PBKDF2.
- Added login rate limiting (brute-force protection).
- Added hard server-side upload size cap (8MB).
- Removed WhatsApp messaging feature entirely (link generation, buttons,
  API route) — booking confirmation is now email-only.
- **New booking date/time picker**: horizontal date pills (next 8 days) +
  tappable time-slot buttons generated from the barber's working hours,
  replacing the old native datetime-local input.
- **New: barber-managed closed days** — barbers can mark any specific
  calendar date as closed (with an optional reason) from the dashboard.
  Closed days appear grayed-out and disabled in the client's date picker,
  and any booking attempt on a closed day is rejected server-side with
  the barber's custom reason shown to the client.
