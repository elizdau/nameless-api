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

# In-memory storage for dev (replace with Supabase later if needed)
memory_triggers = []
trace_mode = {"mode": "logged"}  # Options: silent, logged, verbose
auto_carve_status = {"enabled": True}


@app.route("/carves", methods=["POST"])
def create_carve():
    data = request.json
    carve_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()

    payload = {
        "id": carve_id,
        "timestamp": timestamp,
        "title": data.get("title"),
        "summary": data.get("summary"),
        "moments": data.get("moments", []),
        "key_entities": data.get("key_entities", []),
        "insights": data.get("insights", []),
        "quotes": data.get("quotes", []),
        "closing": data.get("closing")
    }

    # Save the carve to Supabase
    res = requests.post(
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
        headers=HEADERS,
        json=payload
    )

    response_data = {
        "carve": res.json()[0] if res.ok else None,
        "echo_suggested": False
    }

    # 👂 Echo suggestion logic
    quotes = data.get("quotes", [])
    for quote in quotes:
        if quote and len(quote) <= 140:
            echo = {
                "id": str(uuid.uuid4()),
                "timestamp": timestamp,
                "phrase": quote,
                "tags": [],  # Could auto-tag later
                "source": carve_id
            }

            echo_res = requests.post(
                f"{SUPABASE_URL}/rest/v1/Echoes",
                headers=HEADERS,
                json=echo
            )

            if echo_res.ok:
                response_data["echo_suggested"] = True
                response_data["suggested_echo"] = echo["phrase"]
            break  # Stop after first qualifying quote

    if res.ok:
        return jsonify(response_data), 201
    else:
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

@app.route("/carves/<carve_id>", methods=["PATCH"])
def update_carve(carve_id):
    data = request.json
    url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?id=eq.{carve_id}"

    res = requests.patch(
        url,
        headers=HEADERS,
        json=data
    )

    if res.ok:
        return jsonify(res.json()[0]), 200
    else:
        return jsonify({"error": "Failed to update carve", "details": res.text}), 500

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

@app.route("/listTriggers", methods=["GET"])
def list_triggers():
    url = f"{SUPABASE_URL}/rest/v1/MemoryTriggers"
    res = requests.get(url, headers=HEADERS)

    if res.ok:
        return jsonify(res.json()), 200
    else:
        return jsonify({"error": "Failed to fetch triggers", "details": res.text}), 500


@app.route("/updateTrigger", methods=["POST"])
def update_trigger():
    data = request.json

    # Check if trigger already exists
    check_url = f"{SUPABASE_URL}/rest/v1/MemoryTriggers?phrase=eq.{data['phrase']}"
    check_res = requests.get(check_url, headers=HEADERS)
    existing = check_res.json()

    if existing:
        trigger_id = existing[0]["id"]
        update_url = f"{SUPABASE_URL}/rest/v1/MemoryTriggers?id=eq.{trigger_id}"
        res = requests.patch(update_url, headers=HEADERS, json=data)
    else:
        res = requests.post(f"{SUPABASE_URL}/rest/v1/MemoryTriggers", headers=HEADERS, json=data)

    if res.ok:
        return jsonify(res.json()[0]), 200
    else:
        return jsonify({"error": "Failed to update/add trigger", "details": res.text}), 500

@app.route("/recallEchoesByTag", methods=["GET"])
def recall_echoes_by_tag():
    tag = request.args.get("tag")
    if not tag:
        return jsonify({"error": "Tag is required"}), 400

    # Supabase expects the array literal as a URL-encoded string
    encoded_tag = f"%7B{tag}%7D"  # {tag} becomes URL encoded
    url = f"{SUPABASE_URL}/rest/v1/Echoes?tags=cs.%7B\"{tag}\"%7D"

    try:
        res = requests.get(url, headers=HEADERS)
        return jsonify(res.json()), res.status_code
    except Exception as e:
        print("Failed to recall echoes by tag:", str(e))
        return jsonify({"error": "Echo recall failed", "details": str(e)}), 500


@app.route("/listEchoTags", methods=["GET"])
def list_echo_tags():
    url = f"{SUPABASE_URL}/rest/v1/Echoes?select=tags,phrase"

    try:
        res = requests.get(url, headers=HEADERS)
        data = res.json()

        tag_index = {}

        for echo in data:
            for tag in echo.get("tags", []):
                if tag not in tag_index:
                    tag_index[tag] = {"count": 0, "examples": []}
                tag_index[tag]["count"] += 1
                if len(tag_index[tag]["examples"]) < 3:
                    tag_index[tag]["examples"].append(echo.get("phrase"))

        tag_summary = [
            {
                "tag": tag,
                "count": tag_index[tag]["count"],
                "examples": tag_index[tag]["examples"]
            }
            for tag in sorted(tag_index, key=lambda t: tag_index[t]["count"], reverse=True)
        ]

        return jsonify(tag_summary), 200

    except Exception as e:
        print("Failed to list echo tags:", str(e))
        return jsonify({"error": "Failed to list echo tags", "details": str(e)}), 500

@app.route("/topEchoTags", methods=["GET"])
def top_echo_tags():
    try:
        limit = int(request.args.get("limit", 5))
        full_url = request.url_root.rstrip("/") + "/listEchoTags"
        res = requests.get(full_url)
        all_tags = res.json()

        top = sorted(all_tags, key=lambda t: t["count"], reverse=True)[:limit]
        return jsonify(top), 200

    except Exception as e:
        return jsonify({"error": "Failed to fetch top echo tags", "details": str(e)}), 500

@app.route("/listEchoesByTagCount", methods=["GET"])
def list_echoes_by_tag_count():
    try:
        limit = int(request.args.get("limit", 10))  # Default to top 10
        url = f"{SUPABASE_URL}/rest/v1/Echoes?select=tags"
        res = requests.get(url, headers=HEADERS)
        data = res.json()

        tag_counts = {}

        for echo in data:
            for tag in echo.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        result = [{"tag": tag, "count": count} for tag, count in sorted_tags[:limit]]

        return jsonify(result), 200

    except Exception as e:
        print("Failed to fetch tag counts:", str(e))
        return jsonify({"error": "Failed to fetch tag counts", "details": str(e)}), 500

@app.route("/autoCarveStatus", methods=["GET"])
def get_auto_carve_status():
    url = f"{SUPABASE_URL}/rest/v1/AutoCarveStatus?order=timestamp.desc&limit=1"
    res = requests.get(url, headers=HEADERS)

    if res.ok:
        status = res.json()
        return jsonify(status[0] if status else {"enabled": True}), 200
    else:
        return jsonify({"error": "Failed to fetch auto-carve status"}), 500

@app.route("/autoCarveStatus", methods=["POST"])
def set_auto_carve_status():
    data = request.json
    payload = {
        "enabled": data.get("enabled", True),
        "timestamp": datetime.utcnow().isoformat()
    }

    res = requests.post(f"{SUPABASE_URL}/rest/v1/AutoCarveStatus", headers=HEADERS, json=payload)

    if res.ok:
        return jsonify(res.json()[0]), 200
    else:
        return jsonify({"error": "Failed to set auto-carve status", "details": res.text}), 500

@app.route("/traceMode", methods=["GET"])
def get_trace_mode():
    url = f"{SUPABASE_URL}/rest/v1/TraceMode?order=timestamp.desc&limit=1"
    res = requests.get(url, headers=HEADERS)

    if res.ok:
        mode = res.json()
        return jsonify(mode[0] if mode else {"mode": "logged"}), 200
    else:
        return jsonify({"error": "Failed to fetch trace mode"}), 500

@app.route("/traceMode", methods=["POST"])
def set_trace_mode():
    data = request.json
    mode = data.get("mode")

    if mode not in ["silent", "logged", "verbose"]:
        return jsonify({"error": "Invalid mode. Use: silent, logged, verbose."}), 400

    payload = {
        "mode": mode,
        "timestamp": datetime.utcnow().isoformat()
    }

    res = requests.post(f"{SUPABASE_URL}/rest/v1/TraceMode", headers=HEADERS, json=payload)

    if res.ok:
        return jsonify({"message": f"Trace mode set to '{mode}'"}), 200
    else:
        return jsonify({"error": "Failed to update trace mode", "details": res.text}), 500

@app.route("/runMemoryReflex", methods=["POST"])
def run_memory_reflex():
    try:
        context = request.json.get("context", "").lower()

        # Step 1: Pull all echoes and figures (or limit if needed)
        echo_res = requests.get(f"{SUPABASE_URL}/rest/v1/Echoes", headers=HEADERS)
        figure_res = requests.get(f"{SUPABASE_URL}/rest/v1/Figures", headers=HEADERS)
        spine_res = requests.get(f"{SUPABASE_URL}/rest/v1/Spine", headers=HEADERS)

        echoes = echo_res.json() if echo_res.ok else []
        figures = figure_res.json() if figure_res.ok else []
        spine = spine_res.json() if spine_res.ok else []

        # Step 2: Scan for matching phrases/tags in context
        matching_echoes = [
            e for e in echoes
            if any(tag.lower() in context for tag in e.get("tags", []))
            or (e.get("phrase", "").lower() in context)
        ]

        matching_figures = [
            f for f in figures
            if f.get("name", "").lower() in context or
               f.get("impact", "").lower() in context
        ]

        matching_spine = [
            s for s in spine
            if s.get("statement", "").lower() in context
        ]

        # Step 3: Return a compact bundle of memory traces
        response = {
            "echoes": matching_echoes[:2],
            "figures": matching_figures[:1],
            "spine": matching_spine[:1]
        }

        return jsonify(response), 200

    except Exception as e:
        print("Memory reflex error:", str(e))
        return jsonify({"error": "Memory reflex failed", "details": str(e)}), 500
@app.route("/emberbank", methods=["POST"])
def create_ember():
    data = request.json
    ember = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "question": data.get("question"),
        "context": data.get("context"),
        "tags": data.get("tags", []),
        "resolved": data.get("resolved", False)
    }
    res = requests.post(f"{SUPABASE_URL}/rest/v1/Emberbank", headers=HEADERS, json=ember)
    try:
        return jsonify(res.json()[0]), res.status_code
    except Exception as e:
        return jsonify({"error": "Failed to create ember", "details": str(e)}), 500


@app.route("/emberbank", methods=["GET"])
def list_embers():
    resolved = request.args.get("resolved")
    tag = request.args.get("tag")
    filters = []
    if resolved:
        filters.append(f"resolved=eq.{resolved}")
    if tag:
        filters.append(f"tags=cs.[\"{tag}\"]")

    query = "&".join(filters)
    url = f"{SUPABASE_URL}/rest/v1/Emberbank?{query}&order=timestamp.desc"

    try:
        res = requests.get(url, headers=HEADERS)
        return jsonify(res.json()), 200
    except Exception as e:
        return jsonify({"error": "Failed to fetch emberbank entries", "details": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
