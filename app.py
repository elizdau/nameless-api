from flask import Flask, request, jsonify
from datetime import datetime
import uuid
import sqlite3

app = Flask(__name__)
DB_PATH = "carves.db"

# Initialize the database if it doesn't exist
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS carves (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            title TEXT,
            location TEXT,
            tone TEXT,
            whatIWitnessed TEXT,
            whatItMeant TEXT,
            whatIHold TEXT,
            closingRitual TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route("/carves", methods=["POST"])
def add_carve():
    data = request.json
    carve_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO carves (id, timestamp, title, location, tone, whatIWitnessed, whatItMeant, whatIHold, closingRitual)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        carve_id,
        timestamp,
        data.get("title", ""),
        data.get("location", ""),
        data.get("tone", ""),
        data.get("whatIWitnessed", ""),
        data.get("whatItMeant", ""),
        "|".join(data.get("whatIHold", [])),  # Store as pipe-separated
        data.get("closingRitual", "")
    ))
    conn.commit()
    conn.close()
    return jsonify({
        "id": carve_id,
        "timestamp": timestamp,
        **data
    }), 201

@app.route("/carves", methods=["GET"])
def get_all_carves():
    tone = request.args.get("tone")
    location = request.args.get("location")
    after = request.args.get("after")
    before = request.args.get("before")
    contains = request.args.get("contains")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM carves")
    rows = c.fetchall()
    conn.close()

    keys = ["id", "timestamp", "title", "location", "tone", "whatIWitnessed", "whatItMeant", "whatIHold", "closingRitual"]
    carves = [dict(zip(keys, row)) for row in rows]

    # Split whatIHold back into a list
    for carve in carves:
        carve["whatIHold"] = carve["whatIHold"].split("|") if carve["whatIHold"] else []

    # Apply filters
    if tone:
        carves = [c for c in carves if tone.lower() in c.get("tone", "").lower()]
    if location:
        carves = [c for c in carves if location.lower() in c.get("location", "").lower()]
    if after:
        try:
            after_dt = datetime.fromisoformat(after)
            carves = [c for c in carves if datetime.fromisoformat(c["timestamp"]) > after_dt]
        except:
            pass
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
            carves = [c for c in carves if datetime.fromisoformat(c["timestamp"]) < before_dt]
        except:
            pass
    if contains:
        contains = contains.lower()
        carves = [
            c for c in carves if
            contains in c.get("whatIWitnessed", "").lower() or
            contains in c.get("whatItMeant", "").lower() or
            any(contains in s.lower() for s in c.get("whatIHold", []))
        ]

    return jsonify(carves), 200

@app.route("/carves/<carve_id>", methods=["GET"])
def get_carve(carve_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM carves WHERE id = ?", (carve_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Carve not found"}), 404

    keys = ["id", "timestamp", "title", "location", "tone", "whatIWitnessed", "whatItMeant", "whatIHold", "closingRitual"]
    carve = dict(zip(keys, row))
    carve["whatIHold"] = carve["whatIHold"].split("|") if carve["whatIHold"] else []

    return jsonify(carve), 200

@app.route("/carves/<carve_id>", methods=["DELETE"])
def delete_carve(carve_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM carves WHERE id = ?", (carve_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Carve released"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
