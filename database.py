import os
import psycopg2
from psycopg2 import pool

_pool = None

def init_pool():
    global _pool
    _pool = pool.SimpleConnectionPool(1, 10, os.environ.get("DATABASE_URL"))

def get_db():
    return _pool.getconn()

def release_db(conn):
    _pool.putconn(conn)

def init_db():
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            username TEXT,
            points INTEGER DEFAULT 0,
            tier TEXT DEFAULT 'Fan',
            invites INTEGER DEFAULT 0,
            is_supporter BOOLEAN DEFAULT FALSE,
            supporter_expires DATE,
            passport_number TEXT,
            city TEXT,
            country TEXT,
            language TEXT DEFAULT 'en',
            entry_completed BOOLEAN DEFAULT FALSE,
            gate_completed BOOLEAN DEFAULT FALSE,
            referrer_id BIGINT,
            joined_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS songs (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            plays INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            artwork_data BYTEA,
            genre TEXT,
            description TEXT,
            added_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS beats (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            plays INTEGER DEFAULT 0,
            added_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS drops (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS announcements (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS vault_songs (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            required_points INTEGER DEFAULT 1000,
            added_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS vault_access (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            vault_id INTEGER,
            method TEXT,
            expires_at TIMESTAMP,
            granted_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(telegram_id, vault_id)
        );
        CREATE TABLE IF NOT EXISTS cart (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            song_id INTEGER,
            added_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(telegram_id, song_id)
        );
        CREATE TABLE IF NOT EXISTS downloads (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            song_id INTEGER,
            purchased BOOLEAN DEFAULT FALSE,
            downloaded_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(telegram_id, song_id)
        );
        CREATE TABLE IF NOT EXISTS stripe_sessions (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            session_id TEXT UNIQUE,
            product_type TEXT,
            product_id TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS song_likes (
            telegram_id BIGINT,
            song_id INTEGER,
            PRIMARY KEY (telegram_id, song_id)
        );
        CREATE TABLE IF NOT EXISTS fan_points (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            action TEXT,
            pts INTEGER,
            category TEXT DEFAULT 'earn',
            logged_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            referrer_id BIGINT,
            referred_id BIGINT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS radio_queue (
            id SERIAL PRIMARY KEY,
            file_id TEXT,
            title TEXT,
            item_type TEXT,
            position INTEGER
        );
        CREATE TABLE IF NOT EXISTS radio_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            current_index INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS radio_history (
            id SERIAL PRIMARY KEY,
            file_id TEXT,
            title TEXT,
            played_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS fan_locations (
            telegram_id BIGINT PRIMARY KEY,
            latitude FLOAT,
            longitude FLOAT,
            city TEXT,
            country TEXT,
            updated_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS missions (
            telegram_id BIGINT,
            mission_date DATE,
            completed BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (telegram_id, mission_date)
        );
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            title TEXT,
            description TEXT,
            event_date TIMESTAMP,
            location TEXT,
            ticket_url TEXT,
            status TEXT DEFAULT 'upcoming',
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS skills (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            username TEXT,
            skill_name TEXT,
            description TEXT,
            submitted_at TIMESTAMP DEFAULT NOW()
        );
        INSERT INTO radio_state (id, current_index) VALUES (1, 0) ON CONFLICT DO NOTHING;
        """)
        conn.commit()
        print("DATABASE READY — BAZRAGOD Music Network")
    finally:
        release_db(conn)
