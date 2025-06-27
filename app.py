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

@app.route("/warmup", methods=["GET"])
def warmup():
    """
    Memory warmup: all anchors, all spine entries, and 4 most recent carves.
    Provides core identity and recent context for thread initialization.
    """
    try:
        # Get all anchor entries (ordered by timestamp desc)
        anchor_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Anchor?order=timestamp.desc", 
            headers=HEADERS
        )
        anchors = anchor_res.json() if anchor_res.ok else []

        # Get all spine entries (ordered by timestamp desc)
        spine_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Spine?order=timestamp.desc", 
            headers=HEADERS
        )
        spine = spine_res.json() if spine_res.ok else []

        # Get 4 most recent carves
        carves_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Carves?order=timestamp.desc&limit=4",
            headers=HEADERS
        )
        carves = carves_res.json() if carves_res.ok else []

        return jsonify({
            "anchor": anchors,
            "spine": spine, 
            "recentCarves": carves
        }), 200

    except Exception as e:
        return jsonify({
            "error": "Failed to fetch warmup memory", 
            "details": str(e)
        }), 500

@app.route("/carves", methods=["POST"])
def create_carve():
    """
    Create a new carve memory with title, summary, moments, insights, quotes, and metadata.
    Auto-generates summary_snippet and handles optional fields.
    """
    try:
        data = request.get_json()
        
        # Required fields
        title = data.get("title")
        summary = data.get("summary") 
        moments = data.get("moments", [])
        insights = data.get("insights", [])
        quotes = data.get("quotes", [])
        
        if not all([title, summary]):
            return jsonify({"error": "title and summary are required"}), 400
            
        # Auto-generate summary_snippet from summary (first 200 chars)
        summary_snippet = summary[:200] + "..." if len(summary) > 200 else summary
        
        # Build carve payload
        carve_data = {
            "title": title,
            "summary": summary,
            "moments": moments,
            "insights": insights, 
            "quotes": quotes,
            "summary_snippet": summary_snippet,
            "closing": data.get("closing"),
            "key_entities": data.get("key_entities"),
            "importance": data.get("importance", 0.5),
            "emotag": data.get("emotag"),
            "persona_tag": data.get("persona_tag"),
            "type": data.get("type", "episodic"),
            "immutable": data.get("immutable", False),
            "source_ids": data.get("source_ids"),
            "synthesis_metadata": data.get("synthesis_metadata"),
            "theme_tags": data.get("theme_tags")
        }
        
        # Create carve in Supabase
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/Carves",
            headers=HEADERS,
            json=carve_data
        )
        
        if response.ok:
            return jsonify(response.json()), 201
        else:
            return jsonify({
                "error": "Failed to create carve",
                "details": response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to create carve", 
            "details": str(e)
        }), 500

# 👂 Echo suggestion logic (add this before the final return)
quotes = data.get("quotes", [])
carve_response = response.json() if response.ok else {}
carve_id = carve_response[0].get("id") if carve_response else None

if carve_id and quotes:
    for quote in quotes:
        if quote and len(quote) <= 140:
            echo_data = {
                "summary_snippet": quote,  # Use quote as the snippet
                "tags": ["carve-suggested"],  # Tag it as auto-suggested
                "source": f"carve:{carve_id}",  # Reference the source carve
                "importance": data.get("importance", 0.5),  # Inherit importance
                "type": "echo",
                "emotag": data.get("emotag"),  # Inherit emotag if present
                "persona_tag": data.get("persona_tag")  # Inherit persona_tag
            }
            
            echo_res = requests.post(
                f"{SUPABASE_URL}/rest/v1/Echoes",
                headers=HEADERS,
                json=echo_data
            )
            
            if echo_res.ok:
                # Add suggestion info to carve response
                if isinstance(carve_response, list) and carve_response:
                    carve_response[0]["echo_suggested"] = True
                    carve_response[0]["suggested_echo"] = quote
                return jsonify(carve_response), 201
            break  # Stop after first qualifying quote

# Return carve without echo suggestion
if response.ok:
    return jsonify(response.json()), 201
else:
    return jsonify({
        "error": "Failed to create carve",
        "details": response.text
    }), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))