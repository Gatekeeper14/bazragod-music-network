import os
import psycopg2
from psycopg2 import pool

_pool = None

def init_pool():
    global _pool
    db_url = (
        os.environ.get("DATABASE_URL") or
        os.environ.get("DATABASE_PUBLIC_URL") or
        os.environ.get("Postgres.DATABASE_PUBLIC_URL")
    )
    if not db_url:
        raise Exception("DATABASE_URL not set")
    _pool = pool.SimpleConnectionPool(1, 10, db_url, sslmode="require")

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
            logged_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            referrer_id BIGINT,
            referred_id BIGINT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)
        conn.commit()
        print("DATABASE READY — BAZRAGOD Music Network")
    finally:
        release_db(conn)
