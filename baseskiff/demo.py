"""A small, coherent fictional commerce dataset stored as ordinary SQLite."""
import random
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


def create_playground(path: Path) -> None:
    """Initialize the sample once; existing user edits are never overwritten."""
    if path.exists():
        return
    rng = random.Random(42)
    names = [
        "Olivia Chen", "Liam Wilson", "Sofia García", "Noah Kim", "Emma Laurent",
        "Oliver Brooks", "Amelia Patel", "Elijah Davis", "Isabella Rossi", "Lucas Meyer",
        "Mia Tanaka", "Ethan Taylor", "Charlotte Wang", "James Anderson", "Harper Singh",
        "Benjamin Lee", "Evelyn Müller", "Henry Martin", "Aria Johnson", "Leo Santos",
        "Luna Park", "Alexander Brown", "Camila Silva", "Daniel Nguyen", "Chloé Dubois",
        "Mateo Ruiz", "Nora Jensen", "Aiden Scott", "Zoe Fischer", "Samuel Ali",
        "Maya Nakamura", "Felix Berg",
    ]
    companies = ["Acme Studio", "Northstar Labs", "Lumina Design", "Orbit Systems", "Cedar & Co.",
                 "Fjord Creative", "Atlas Ventures", "Sage Collective", "Paperplane", "Bloom Works",
                 "Horizon Digital", "Riverstone", "Mosaic Studio", "Parallel Labs", "Evergreen", "Forma"]
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''
            PRAGMA foreign_keys = ON;
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, company TEXT,
                plan TEXT NOT NULL DEFAULT 'Starter', status TEXT NOT NULL DEFAULT 'Active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT);
            CREATE TABLE products (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, category_id INTEGER REFERENCES categories(id),
                price REAL NOT NULL, stock INTEGER NOT NULL, status TEXT DEFAULT 'Active');
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                total REAL NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id),
                product_id INTEGER REFERENCES products(id), quantity INTEGER NOT NULL, unit_price REAL NOT NULL);
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                action TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL);
        ''')
        base = datetime(2026, 11, 10, 16, 45)
        customers = []
        for index in range(128):
            first, last = names[index % len(names)].split(" ", 1)
            cycle = index // len(names)
            name = f"{first} {last}" if not cycle else f"{first} {['Morgan', 'Reed', 'Hayes'][cycle - 1]}"
            email = f"{first.lower()}.{last.lower()}{cycle or ''}@example.com"
            customers.append((index + 1, name, email, companies[index % len(companies)],
                              ["Pro", "Enterprise", "Starter", "Pro", "Pro"][index % 5],
                              ["Active", "Active", "Active", "Trial", "Inactive", "Active"][index % 6],
                              (base - timedelta(days=index * 2, hours=index % 12)).isoformat(sep=" ", timespec="seconds")))
        db.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)", customers)
        db.executemany("INSERT INTO categories VALUES (?, ?, ?)", [
            (1, "Workspace", "Tools for thoughtful everyday work"), (2, "Stationery", "Ideas start here"),
            (3, "Accessories", "Small things, considered"), (4, "Digital", "Templates and resources")])
        products = [
            (1, "Arc Desk Lamp", 1, 89.00, 42), (2, "Studio Notebook", 2, 18.00, 240),
            (3, "Focus Timer", 1, 34.00, 87), (4, "Canvas Tote", 3, 26.00, 128),
            (5, "Weekly Planner", 2, 24.00, 165), (6, "Ceramic Mug", 3, 22.00, 76),
            (7, "Desk Organizer", 1, 48.00, 53), (8, "Design System Kit", 4, 129.00, 999),
            (9, "Brass Pen", 2, 32.00, 94), (10, "Felt Desk Mat", 1, 39.00, 61),
            (11, "Project Templates", 4, 49.00, 999), (12, "Travel Pouch", 3, 28.00, 110)]
        db.executemany("INSERT INTO products(id,name,category_id,price,stock) VALUES (?,?,?,?,?)", products)
        for index in range(1, 193):
            product = products[rng.randrange(len(products))]
            quantity = rng.randint(1, 4)
            created = (base - timedelta(hours=index * 7)).isoformat(sep=" ", timespec="seconds")
            db.execute("INSERT INTO orders VALUES (?,?,?,?,?)", (index, (index * 7) % 128 + 1,
                       round(product[3] * quantity, 2), ["Completed", "Processing", "Shipped", "Completed"][index % 4], created))
            db.execute("INSERT INTO order_items VALUES (?,?,?,?,?)", (index, index, product[0], quantity, product[3]))
        for index in range(1, 65):
            action = ["Signed in", "Updated profile", "Placed order", "Upgraded plan"][index % 4]
            db.execute("INSERT INTO activity_log VALUES (?,?,?,?,?)", (index, index, action,
                       f"{action} from the web application", (base - timedelta(minutes=index * 17)).isoformat(sep=" ", timespec="seconds")))
