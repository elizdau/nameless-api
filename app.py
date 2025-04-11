from flask import Flask, request, jsonify
import os
import requests
from datetime import datetime
import uuid

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_API_KEY = os.environ.get("SUPABASE_API_KEY")
SUPABASE_TABLE = "Carves"

HEADERS = {
    "apikey": SUPABASE_API_KEY,
    "Authorization": f"Bearer {SUPABASE_API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

app = Flask(__name__)

@app.route("/carves", methods=["POST"])
def create_carve():
    data = request.json
    payload = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "title": data.get("title"),
        "location": data.get("location"),
        "tone": data.get("tone"),
        "whatIWitnessed": data.get("whatIWitnessed"),
        "whatItMeant": data.get("whatItMeant"),
        "whatIHold": "|".join(data.get("whatIHold", [])),
        "closingRitual": data.get("closingRitual")
    }
    res = requests.post(f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}", headers=HEADERS, json=payload)
    return jsonify(res.json()[0]), res.status_code

@app.route("/carves", methods=["GET"])
def list_carves():
    query_params = []

    tone = request.args.get("tone")
    location = request.args.get("location")
    after = request.args.get("after")
    before = request.args.get("before")
    contains = request.args.get("contains")

    if tone:
        query_params.append(f"tone=ilike.*{tone}*")
    if location:
        query_params.append(f"location=ilike.*{location}*")
    if after:
        query_params.append(f"timestamp=gt.{after}")
    if before:
        query_params.append(f"timestamp=lt.{before}")

    query_string = "&".join(query_params)
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?{query_string}"

    res = requests.get(url, headers=HEADERS)
    carves = res.json()

        # 🔍 Add this debug print
    print("Calling Supabase URL:", url)

    try:
        res = requests.get(url, headers=HEADERS)
        carves = res.json()
    except Exception as e:
        print("Error calling Supabase:", e)
        return jsonify({"error": "Failed to call Supabase", "details": str(e)}), 500
        
    # Manual contains filtering
    if contains:
        contains = contains.lower()
        carves = [
            c for c in carves if
            contains in (c.get("whatIWitnessed") or "").lower() or
            contains in (c.get("whatItMeant") or "").lower() or
            any(contains in s.lower() for s in (c.get("whatIHold") or "").split("|"))
        ]

    # Normalize whatIHold
    for c in carves:
        c["whatIHold"] = c.get("whatIHold", "").split("|") if c.get("whatIHold") else []

    return jsonify(carves), 200

@app.route("/carves/<carve_id>", methods=["GET"])
def get_carve(carve_id):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{carve_id}"
    res = requests.get(url, headers=HEADERS)
    carves = res.json()
    if not carves:
        return jsonify({"error": "Carve not found"}), 404

    carve = carves[0]
    carve["whatIHold"] = carve.get("whatIHold", "").split("|") if carve.get("whatIHold") else []
    return jsonify(carve), 200

@app.route("/carves/<carve_id>", methods=["DELETE"])
def delete_carve(carve_id):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{carve_id}"
    res = requests.delete(url, headers={**HEADERS, "Prefer": "return=minimal"})
    if res.status_code == 204:
        return jsonify({"message": "Carve released"}), 200
    else:
        return jsonify({"error": "Could not delete"}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
