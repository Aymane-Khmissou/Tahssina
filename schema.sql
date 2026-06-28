-- Tahssina (تحسينة) - Barbershop Queue Management System
-- SQLite Schema

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    phone VARCHAR(20) NOT NULL,
    role TEXT CHECK(role IN ('barber', 'client')) NOT NULL,
    password_hash TEXT NOT NULL,
    no_show_count INTEGER DEFAULT 0,
    loyalty_visits INTEGER DEFAULT 0,
    blocked_until DATETIME DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS barbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    salon_name VARCHAR(100) NOT NULL,
    address TEXT NOT NULL,
    phone TEXT DEFAULT '',
    min_price REAL DEFAULT 0.0,
    max_price REAL DEFAULT 0.0,
    is_open INTEGER DEFAULT 1,
    open_time TEXT DEFAULT '09:00',
    close_time TEXT DEFAULT '20:00',
    favor_service_id INTEGER DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (favor_service_id) REFERENCES services(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barber_id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    price REAL NOT NULL CHECK(price >= 0),
    duration INTEGER NOT NULL CHECK(duration > 0),
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    barber_id INTEGER NOT NULL,
    status TEXT CHECK(status IN ('pending','waiting','ongoing','done','rejected','noshow')) DEFAULT 'pending',
    total_price REAL DEFAULT 0.0,
    appointment_time DATETIME NOT NULL,
    assigned_duration INTEGER DEFAULT 0,
    email_sent INTEGER DEFAULT 0,
    confirmation_sent INTEGER DEFAULT 0,
    loyalty_reward INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS queue_services (
    queue_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    PRIMARY KEY (queue_id, service_id),
    FOREIGN KEY (queue_id) REFERENCES queue(id) ON DELETE CASCADE,
    FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS client_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barber_id INTEGER NOT NULL,
    client_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    review_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE,
    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Barber rates the client (1-5 stars), tied to a specific completed queue entry
CREATE TABLE IF NOT EXISTS barber_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barber_id INTEGER NOT NULL,
    client_id INTEGER NOT NULL,
    queue_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE,
    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (queue_id) REFERENCES queue(id) ON DELETE CASCADE,
    UNIQUE (barber_id, queue_id)
);

CREATE TABLE IF NOT EXISTS loyalty_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    barber_id INTEGER NOT NULL,
    stamps INTEGER DEFAULT 0,
    total_earned INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, barber_id),
    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_loyalty_client ON loyalty_cards(client_id);
CREATE INDEX IF NOT EXISTS idx_queue_client ON queue(client_id);
CREATE INDEX IF NOT EXISTS idx_queue_appointment ON queue(appointment_time);
CREATE INDEX IF NOT EXISTS idx_services_barber ON services(barber_id);

-- Gallery photos per barber
CREATE TABLE IF NOT EXISTS gallery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barber_id INTEGER NOT NULL,
    image_url TEXT NOT NULL,
    caption TEXT DEFAULT '',
    source TEXT DEFAULT 'upload',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE
);

-- Walk-in queue (scan QR → instant spot)
CREATE TABLE IF NOT EXISTS walkin (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barber_id INTEGER NOT NULL,
    client_name TEXT NOT NULL,
    client_phone TEXT NOT NULL,
    status TEXT CHECK(status IN ('waiting','ongoing','done','cancelled')) DEFAULT 'waiting',
    assigned_duration INTEGER DEFAULT 30,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE
);

-- Specific dates the barber manually closes (no recurring pattern, picked freely)
CREATE TABLE IF NOT EXISTS closed_dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barber_id INTEGER NOT NULL,
    closed_date DATE NOT NULL,
    reason TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(barber_id, closed_date),
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_closed_dates_barber ON closed_dates(barber_id, closed_date);

-- Waitlist: clients who request a slot on a fully-booked day
CREATE TABLE IF NOT EXISTS waitlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    barber_id INTEGER NOT NULL,
    requested_date DATE NOT NULL,
    service_ids TEXT NOT NULL,          -- JSON array of service ids
    status TEXT CHECK(status IN ('waiting','notified','booked','expired','cancelled')) DEFAULT 'waiting',
    notified_at DATETIME DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (barber_id) REFERENCES barbers(id) ON DELETE CASCADE,
    UNIQUE(client_id, barber_id, requested_date)
);

CREATE INDEX IF NOT EXISTS idx_waitlist_barber_date ON waitlist(barber_id, requested_date, status);
