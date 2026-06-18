import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import sys
import os

# Default local postgres connection
# We connect to default 'postgres' db first to create 'neoscona_db'
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASS = os.environ.get("DB_PASS", "IGOSDP2022")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "neoscona_db")


def init_db():
    try:
        # Connect to default database
        conn = psycopg2.connect(
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT,
            dbname='postgres'
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()

        # Check if database exists
        cur.execute(f"SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{DB_NAME}'")
        exists = cur.fetchone()
        if not exists:
            print(f"Creating database {DB_NAME}...")
            cur.execute(f"CREATE DATABASE {DB_NAME}")
        else:
            print(f"Database {DB_NAME} already exists.")

        cur.close()
        conn.close()

        # Connect to the new database and create tables
        conn = psycopg2.connect(
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME
        )
        cur = conn.cursor()

        print("Creating posts table if it does not exist...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                category VARCHAR(100) NOT NULL,
                content TEXT,
                image_url VARCHAR(500),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        print("Creating organizations table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS organizations (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                slug VARCHAR(255) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        print("Creating plans table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                description TEXT,
                price_monthly DECIMAL(12, 2) NOT NULL,
                price_yearly DECIMAL(12, 2),
                message_volume INTEGER NOT NULL,
                max_channels INTEGER NOT NULL,
                max_agents INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        print("Creating users table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        print("Creating subscriptions table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                plan_id INTEGER NOT NULL REFERENCES plans(id),
                paystack_subscription_code VARCHAR(255),
                paystack_customer_code VARCHAR(255),
                status VARCHAR(50) NOT NULL DEFAULT 'active',
                current_period_start TIMESTAMP,
                current_period_end TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        print("Creating channels table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                type VARCHAR(50) NOT NULL, -- 'whatsapp', 'telegram', 'instagram', 'voice'
                name VARCHAR(255) NOT NULL,
                credentials JSONB NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        print("Creating inventory table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                type VARCHAR(50) NOT NULL, -- 'property', 'product', 'service'
                name VARCHAR(255) NOT NULL,
                description TEXT,
                price DECIMAL(12, 2),
                metadata JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        print("Creating usage_metrics table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_metrics (
                id SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                month DATE NOT NULL,
                messages_handled INTEGER DEFAULT 0,
                ai_replies INTEGER DEFAULT 0,
                bookings INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(organization_id, month)
            )
        """)

        print("Creating onboarding_states table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS onboarding_states (
                id SERIAL PRIMARY KEY,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE UNIQUE,
                current_step VARCHAR(50) NOT NULL DEFAULT 'org_created',
                is_complete BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Insert default plans
        print("Inserting default plans...")
        cur.execute("""
            INSERT INTO plans (name, description, price_monthly, price_yearly, message_volume, max_channels, max_agents)
            VALUES
                ('Starter', 'Perfect for small businesses getting started with AI automation', 5000.00, 50000.00, 1000, 1, 1),
                ('Growth', 'For growing businesses that need more channels and agents', 15000.00, 150000.00, 5000, 3, 5),
                ('Enterprise', 'Custom solutions for large organizations', 50000.00, 500000.00, 100000, 10, 50)
            ON CONFLICT DO NOTHING
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("Database initialization successful!")

    except Exception as e:
        print(f"Error initializing database: {e}")
        sys.exit(1)


if __name__ == "__main__":
    init_db()