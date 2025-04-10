from flask import Flask, request, jsonify
from datetime import datetime
import uuid

app = Flask(__name__)

# In-memory carve store
carves = {}

@app.route("/carves", methods=["POST"])
def add_carve():
    data = request.json
    carve_id = str(uuid.uuid4())
    carve = {
        "id": carve_id,
        "timestamp": datetime.utcnow().isoformat(),
        "title": data.get("title", ""),
        "location": data.get("location", ""),
        "tone": data.get("tone", ""),
        "whatIWitnessed": data.get("whatIWitnessed", ""),
        "whatItMeant": data.get("whatItMeant", ""),
        "whatIHold": data.get("whatIHold", []),
        "closingRitual": data.get("closingRitual", "")
    }
    carves[carve_id] = carve
    return jsonify(carve), 201

@app.route("/carves", methods=["GET"])
def get_all_carves():
    tone = request.args.get("tone")
    location = request.args.get("location")
    after = request.args.get("after")
    before = request.args.get("before")
    contains = request.args.get("contains")

    results = list(carves.values())

    if tone:
        results = [c for c in results if tone.lower() in c.get("tone", "").lower()]
    if location:
        results = [c for c in results if location.lower() in c.get("location", "").lower()]
    if after:
        try:
            after_dt = datetime.fromisoformat(after)
            results = [c for c in results if datetime.fromisoformat(c["timestamp"]) > after_dt]
        except:
            pass
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
            results = [c for c in results if datetime.fromisoformat(c["timestamp"]) < before_dt]
        except:
            pass
    if contains:
        contains = contains.lower()
        results = [
            c for c in results if
            contains in c.get("whatIWitnessed", "").lower() or
            contains in c.get("whatItMeant", "").lower() or
            any(contains in s.lower() for s in c.get("whatIHold", []))
        ]

    return jsonify(results), 200


@app.route("/carves/<carve_id>", methods=["GET"])
def get_carve(carve_id):
    carve = carves.get(carve_id)
    if carve:
        return jsonify(carve), 200
    else:
        return jsonify({"error": "Carve not found"}), 404

@app.route("/carves/<carve_id>", methods=["DELETE"])
def delete_carve(carve_id):
    if carve_id in carves:
        del carves[carve_id]
        return jsonify({"message": "Carve released"}), 200
    else:
        return jsonify({"error": "Carve not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
