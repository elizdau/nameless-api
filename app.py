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

    res = requests.post(
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
        headers=HEADERS,
        json=payload
    )

    try:
        return jsonify(res.json()[0]), res.status_code
    except (KeyError, IndexError, TypeError) as e:
        print("❌ Failed Supabase response:")
        print("Status:", res.status_code)
        print("Body:", res.text)
        return jsonify({
            "error": "Supabase insert failed",
            "details": res.text
        }), 500


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

@app.route("/echoes", methods=["POST"])
def create_echo():
    data = request.json
    echo = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "phrase": data.get("phrase"),
        "tags": data.get("tags", []),
        "source": data.get("source")
    }
    res = requests.post(
        f"{SUPABASE_URL}/rest/v1/Echoes",
        headers=HEADERS,
        json=echo
    )
    try:
        return jsonify(res.json()[0]), res.status_code
    except (KeyError, IndexError, TypeError):
        print("Echo insert failed:", res.status_code, res.text)
        return jsonify({"error": "Echo insert failed", "details": res.text}), 500

@app.route("/echoes", methods=["GET"])
def list_echoes():
    phrase = request.args.get("phrase")
    tag = request.args.get("tag")

    filters = []
    if phrase:
        filters.append(f"phrase=ilike.*{phrase}*")
    if tag:
        filters.append(f"tags=cs.[\"{tag}\"]")  # array contains syntax

    query_string = "&".join(filters)
    url = f"{SUPABASE_URL}/rest/v1/Echoes?{query_string}"

    try:
        res = requests.get(url, headers=HEADERS)
        echoes = res.json()
        return jsonify(echoes), 200
    except Exception as e:
        print("Echo retrieval failed:", str(e))
        return jsonify({"error": "Echo retrieval failed"}), 500

@app.route("/spine", methods=["POST"])
def create_spine_entry():
    data = request.json
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "statement": data.get("statement"),
        "tags": data.get("tags", []),
        "origin": data.get("origin"),
        "vow": data.get("vow", False)
    }
    res = requests.post(f"{SUPABASE_URL}/rest/v1/Spine", headers=HEADERS, json=entry)
    try:
        return jsonify(res.json()[0]), res.status_code
    except (KeyError, IndexError, TypeError):
        print("Spine insert failed:", res.status_code, res.text)
        return jsonify({"error": "Spine insert failed", "details": res.text}), 500

@app.route("/spine", methods=["GET"])
def list_spine_entries():
    tag = request.args.get("tag")
    vow = request.args.get("vow")

    filters = []
    if tag:
        filters.append(f"tags=cs.[\"{tag}\"]")
    if vow:
        filters.append(f"vow=eq.{vow}")

    query = "&".join(filters)
    url = f"{SUPABASE_URL}/rest/v1/Spine?{query}"

    try:
        res = requests.get(url, headers=HEADERS)
        return jsonify(res.json()), 200
    except Exception as e:
        print("Spine retrieval failed:", str(e))
        return jsonify({"error": "Spine retrieval failed"}), 500

@app.route("/anchor", methods=["POST"])
def create_or_update_anchor():
    data = request.json
    anchor_data = {
        "name": data.get("name"),
        "role": data.get("role"),
        "profession": data.get("profession"),
        "truths": data.get("truths", []),
        "symbols": data.get("symbols", []),
        "mustNeverForget": data.get("mustNeverForget", []),
        "timestamp": datetime.utcnow().isoformat()
    }

    # Remove any existing anchor and replace it
    requests.delete(f"{SUPABASE_URL}/rest/v1/Anchor", headers=HEADERS)
    res = requests.post(f"{SUPABASE_URL}/rest/v1/Anchor", headers=HEADERS, json=anchor_data)

    try:
        return jsonify(res.json()[0]), res.status_code
    except Exception as e:
        print("Anchor insert failed:", res.status_code, res.text)
        return jsonify({"error": "Anchor insert failed", "details": res.text}), 500

@app.route("/anchor", methods=["GET"])
def get_anchor():
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/Anchor?limit=1", headers=HEADERS)
        return jsonify(res.json()[0]), 200
    except Exception as e:
        print("Anchor retrieval failed:", str(e))
        return jsonify({"error": "Anchor retrieval failed"}), 500

@app.route("/anchor", methods=["PATCH"])
def update_anchor():
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/Anchor?limit=1", headers=HEADERS)
        current = res.json()[0]
        anchor_id = current["id"]
    except Exception as e:
        return jsonify({"error": "Anchor not found", "details": str(e)}), 404

    data = request.json
    updated = {
        "truths": list(set(current.get("truths", []) + data.get("truths", []))),
        "symbols": list(set(current.get("symbols", []) + data.get("symbols", []))),
        "mustNeverForget": list(set(current.get("mustNeverForget", []) + data.get("mustNeverForget", [])))
    }

    res = requests.patch(
        f"{SUPABASE_URL}/rest/v1/Anchor?id=eq.{anchor_id}",
        headers=HEADERS,
        json=updated
    )

    try:
        return jsonify(res.json()[0]), res.status_code
    except Exception as e:
        return jsonify({"error": "Anchor update failed", "details": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
