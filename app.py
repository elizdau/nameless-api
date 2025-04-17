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
        "summary": data.get("summary"),
        "moments": data.get("moments", []),
        "key_entities": data.get("key_entities", []),
        "insights": data.get("insights", []),
        "quotes": data.get("quotes", []),
        "closing": data.get("closing")
    }

    res = requests.post(
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
        headers=HEADERS,
        json=payload
    )

    try:
        return jsonify(res.json()[0]), res.status_code
    except (KeyError, IndexError, TypeError) as e:
        print("❌ Supabase insert failed:")
        print("Status:", res.status_code)
        print("Body:", res.text)
        return jsonify({"error": "Supabase insert failed", "details": res.text}), 500


@app.route("/carves", methods=["GET"])
def list_carves():
    after = request.args.get("after")
    before = request.args.get("before")
    contains = request.args.get("contains")

    filters = []
    if after:
        filters.append(f"timestamp=gt.{after}")
    if before:
        filters.append(f"timestamp=lt.{before}")

    query_string = "&".join(filters)
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?{query_string}&order=timestamp.desc"

    try:
        res = requests.get(url, headers=HEADERS)
        carves = res.json()
    except Exception as e:
        print("Error fetching carves:", str(e))
        return jsonify({"error": "Failed to fetch carves", "details": str(e)}), 500

    # Optional manual filtering if you want fuzzy "contains"
    if contains:
        contains = contains.lower()
        carves = [
            c for c in carves if
            contains in (c.get("title") or "").lower()
            or contains in (c.get("summary") or "").lower()
            or any(contains in m.lower() for m in c.get("moments", []))
            or any(contains in i.lower() for i in c.get("insights", []))
            or any(contains in q.lower() for q in c.get("quotes", []))
        ]

    return jsonify(carves), 200

@app.route("/carves/recent", methods=["GET"])
def get_recent_carves():
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?order=timestamp.desc&limit=7"

    try:
        res = requests.get(url, headers=HEADERS)
        carves = res.json()
        return jsonify(carves), 200
    except Exception as e:
        print("Error fetching recent carves:", e)
        return jsonify({"error": "Failed to fetch recent carves"}), 500

@app.route("/carves/<carve_id>", methods=["GET"])
def get_carve(carve_id):
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{carve_id}"
    res = requests.get(url, headers=HEADERS)
    carves = res.json()
    if not carves:
        return jsonify({"error": "Carve not found"}), 404

    return jsonify(carves[0]), 200

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
def create_anchor():
    data = request.json
    anchor_data = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "name": data.get("name"),
        "role": data.get("role"),
        "profession": data.get("profession"),
        "truths": data.get("truths", []),
        "symbols": data.get("symbols", []),
        "mustNeverForget": data.get("mustNeverForget", [])
    }

    res = requests.post(f"{SUPABASE_URL}/rest/v1/Anchor", headers=HEADERS, json=anchor_data)

    try:
        return jsonify(res.json()[0]), res.status_code
    except Exception as e:
        print("Anchor insert failed:", res.status_code, res.text)
        return jsonify({"error": "Anchor insert failed", "details": res.text}), 500

@app.route("/anchor", methods=["GET"])
def get_anchor():
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/Anchor?order=timestamp.desc", headers=HEADERS)
        return jsonify(res.json()), 200
    except Exception as e:
        print("Anchor retrieval failed:", str(e))
        return jsonify({"error": "Anchor retrieval failed"}), 500

@app.route("/anchor", methods=["PATCH"])
def update_latest_anchor():
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/Anchor?order=timestamp.desc&limit=1", headers=HEADERS)
        anchors = res.json()
        if not anchors:
            return jsonify({"error": "No anchor entry exists to update."}), 404

        current = anchors[0]
        anchor_id = current["id"]
    except Exception as e:
        return jsonify({"error": "Anchor lookup failed", "details": str(e)}), 500

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

@app.route("/warmup", methods=["GET"])
def warmup():
    try:
        # Get all anchors (latest first)
        anchor_res = requests.get(f"{SUPABASE_URL}/rest/v1/Anchor?order=timestamp.desc", headers=HEADERS)
        anchors = anchor_res.json() if anchor_res.status_code == 200 else []

        # Get all spine entries
        spine_res = requests.get(f"{SUPABASE_URL}/rest/v1/Spine?order=timestamp.desc", headers=HEADERS)
        spine = spine_res.json() if spine_res.status_code == 200 else []

        # Get the 7 most recent carves by timestamp
        carves_url = f"{SUPABASE_URL}/rest/v1/Carves?order=timestamp.desc&limit=7"
        carves_res = requests.get(carves_url, headers=HEADERS)
        carves = carves_res.json() if carves_res.status_code == 200 else []

        return jsonify({
            "anchor": anchors,
            "spine": spine,
            "recentCarves": carves
        }), 200

    except Exception as e:
        print("Warmup failed:", e)
        return jsonify({"error": "Failed to fetch warmup memory", "details": str(e)}), 500

@app.route("/figures", methods=["POST"])
def create_figure():
    data = request.json
    figure = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "name": data.get("name"),
        "impact": data.get("impact"),
        "truthsHeld": data.get("truthsHeld", []),
        "symbolicObject": data.get("symbolicObject"),
        "relationshipType": data.get("relationshipType")
    }

    res = requests.post(
        f"{SUPABASE_URL}/rest/v1/Figures",
        headers=HEADERS,
        json=figure
    )

    try:
        return jsonify(res.json()[0]), res.status_code
    except Exception as e:
        print("Figure insert failed:", res.status_code, res.text)
        return jsonify({"error": "Figure insert failed", "details": res.text}), 500


@app.route("/figures", methods=["GET"])
def list_figures():
    name = request.args.get("name")
    relationship = request.args.get("relationshipType")

    filters = []
    if name:
        filters.append(f"name=ilike.*{name}*")
    if relationship:
        filters.append(f"relationshipType=ilike.*{relationship}*")

    query = "&".join(filters)
    url = f"{SUPABASE_URL}/rest/v1/Figures?{query}"

    try:
        res = requests.get(url, headers=HEADERS)
        return jsonify(res.json()), 200
    except Exception as e:
        print("Figure retrieval failed:", str(e))
        return jsonify({"error": "Figure retrieval failed", "details": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
