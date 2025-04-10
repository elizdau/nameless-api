from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import uuid
import os

app = Flask(__name__)

# Replace with your actual Supabase Postgres connection string
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("SUPABASE_DB_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class Carve(db.Model):
    __tablename__ = 'carves'
    id = db.Column(db.String, primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    title = db.Column(db.Text)
    location = db.Column(db.Text)
    tone = db.Column(db.Text)
    whatIWitnessed = db.Column(db.Text)
    whatItMeant = db.Column(db.Text)
    whatIHold = db.Column(db.Text)  # Stored as pipe-delimited
    closingRitual = db.Column(db.Text)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "title": self.title,
            "location": self.location,
            "tone": self.tone,
            "whatIWitnessed": self.whatIWitnessed,
            "whatItMeant": self.whatItMeant,
            "whatIHold": self.whatIHold.split("|") if self.whatIHold else [],
            "closingRitual": self.closingRitual
        }

def create_tables():
    db.create_all()

create_tables()  # <-- Call it at app startup

@app.route("/carves", methods=["POST"])
def create_carve():
    data = request.json
    carve = Carve(
        title=data.get("title"),
        location=data.get("location"),
        tone=data.get("tone"),
        whatIWitnessed=data.get("whatIWitnessed"),
        whatItMeant=data.get("whatItMeant"),
        whatIHold="|".join(data.get("whatIHold", [])),
        closingRitual=data.get("closingRitual")
    )
    db.session.add(carve)
    db.session.commit()
    return jsonify(carve.to_dict()), 201

@app.route("/carves", methods=["GET"])
def list_carves():
    query = Carve.query
    tone = request.args.get("tone")
    location = request.args.get("location")
    after = request.args.get("after")
    before = request.args.get("before")
    contains = request.args.get("contains")

    if tone:
        query = query.filter(Carve.tone.ilike(f"%{tone}%"))
    if location:
        query = query.filter(Carve.location.ilike(f"%{location}%"))
    if after:
        try:
            query = query.filter(Carve.timestamp > datetime.fromisoformat(after))
        except:
            pass
    if before:
        try:
            query = query.filter(Carve.timestamp < datetime.fromisoformat(before))
        except:
            pass

    carves = query.all()

    if contains:
        contains = contains.lower()
        carves = [
            c for c in carves if
            (contains in (c.whatIWitnessed or "").lower()) or
            (contains in (c.whatItMeant or "").lower()) or
            any(contains in s.lower() for s in (c.whatIHold or "").split("|"))
        ]

    return jsonify([c.to_dict() for c in carves]), 200

@app.route("/carves/<carve_id>", methods=["GET"])
def get_carve(carve_id):
    carve = Carve.query.get(carve_id)
    if not carve:
        return jsonify({"error": "Carve not found"}), 404
    return jsonify(carve.to_dict()), 200

@app.route("/carves/<carve_id>", methods=["DELETE"])
def delete_carve(carve_id):
    carve = Carve.query.get(carve_id)
    if not carve:
        return jsonify({"error": "Carve not found"}), 404
    db.session.delete(carve)
    db.session.commit()
    return jsonify({"message": "Carve released"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
