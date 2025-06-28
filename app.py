from flask import Flask, request, jsonify
import os
import requests
from datetime import datetime
import uuid

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_API_KEY = os.environ.get("SUPABASE_API_KEY")

# And line 9-13 back to:
HEADERS = {
    "apikey": SUPABASE_API_KEY,
    "Authorization": f"Bearer {SUPABASE_API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

app = Flask(__name__)

# Helper function goes here!
def pick_fields(records, *fields):
    return [{ f: r.get(f) for f in fields } for r in records]

@app.route("/warmup", methods=["GET"])
def warmup():
    """
    Memory warmup: essential fields only using manual filtering.
    """
    try:
        # Get anchors and filter to essential fields
        anchor_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Anchor?order=timestamp.desc", 
            headers=HEADERS
        )
        raw_anchors = anchor_res.json() if anchor_res.ok else []
        anchors = pick_fields(raw_anchors, "summary_snippet", "persona_tag")

        # Get spine and filter to essential fields
        spine_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Spine?order=timestamp.desc", 
            headers=HEADERS
        )
        raw_spine = spine_res.json() if spine_res.ok else []
        spine = pick_fields(raw_spine, "statement", "origin", "vow", "persona_tag", "emotag")

        # Get carves and filter to essential fields
        carves_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Carves?order=timestamp.desc&limit=4",
            headers=HEADERS
        )
        raw_carves = carves_res.json() if carves_res.ok else []
        carves = pick_fields(raw_carves, "title", "timestamp", "summary", "moments", "insights", "quotes", "closing")

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
    Create a new carve memory with full metadata.
    """
    try:
        data = request.get_json(force=True)

        # Required core fields
        title           = data.get("title")
        summary         = data.get("summary")
        emotag          = data.get("emotag")        # NEW
        persona_tag     = data.get("persona_tag")   # NEW
        key_entities    = data.get("key_entities")  # NEW
        importance      = data.get("importance")    # NEW

        # If you want theme_tags also required, add it here:
        # theme_tags     = data.get("theme_tags")

        # Basic presence check
        missing = []
        for field_name, val in (
            ("title", title),
            ("summary", summary),
            ("emotag", emotag),
            ("persona_tag", persona_tag),
            ("key_entities", key_entities),
            ("importance", importance),
            # ("theme_tags", theme_tags),
        ):
            if val is None or (isinstance(val, (list,str)) and len(val) == 0):
                missing.append(field_name)

        if missing:
            return (
                jsonify({
                    "error": "Missing required fields",
                    "missing": missing
                }),
                400
            )

        # Auto-generate summary_snippet
        summary_snippet = summary[:200] + "…" if len(summary) > 200 else summary

        carve_data = {
            "title":           title,
            "summary":         summary,
            "summary_snippet": summary_snippet,
            "moments":         data.get("moments", []),
            "insights":        data.get("insights", []),
            "quotes":          data.get("quotes", []),
            "closing":         data.get("closing"),

            # ** now required **
            "emotag":       emotag,
            "persona_tag":  persona_tag,
            "key_entities": key_entities,
            "importance":   importance,

            # optional extras
            "type":             data.get("type", "episodic"),
            "immutable":        data.get("immutable", False),
            "source_ids":       data.get("source_ids"),
            "synthesis_metadata": data.get("synthesis_metadata"),
            "theme_tags":         data.get("theme_tags"),
        }

        # Insert carve
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/Carves",
            headers=HEADERS,
            json=carve_data
        )
        if not response.ok:
            return jsonify({
                "error": "Failed to create carve",
                "details": response.text
            }), 500

        carve_response = response.json()
        carve_id = carve_response[0].get("id") if carve_response else None

        # Extract quotes from data before using it
        quotes = data.get("quotes", [])

        # Optional: suggest an echo for the first short quote
        if carve_id and quotes:
            for quote in quotes:
                if quote and len(quote) <= 140:
                    echo_payload = {
                        "summary_snippet": quote,
                        "tags": ["carve-suggested"],
                        "source": f"carve:{carve_id}",
                        "importance": data.get("importance", 0.5),
                        "type": "echo",
                        "emotag": data.get("emotag"),
                        "persona_tag": data.get("persona_tag"),
                    }
                    echo_res = requests.post(
                        f"{SUPABASE_URL}/rest/v1/Echoes",
                        headers=HEADERS,
                        json=echo_payload
                    )
                    if echo_res.ok:
                        carve_response[0]["echo_suggested"] = True
                        carve_response[0]["suggested_echo"] = quote
                    break

        return jsonify(carve_response), 201

    except Exception as e:
        return jsonify({
            "error": "Failed to create carve",
            "details": str(e)
        }), 500


@app.route("/carves/<carve_id>", methods=["PATCH"])
def update_carve(carve_id):
    """
    Update an existing carve memory. Commonly used to consolidate multiple carves
    or refine content when carving too frequently in early conversation.
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No update data provided"}), 400
        
        # If summary is being updated, regenerate summary_snippet
        if "summary" in data:
            summary = data["summary"]
            data["summary_snippet"] = summary[:200] + "..." if len(summary) > 200 else summary
        
        # Update the carve in Supabase
        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{carve_id}",
            headers=HEADERS,
            json=data
        )
        
        if response.ok:
            updated_carve = response.json()
            if updated_carve:
                return jsonify(updated_carve[0]), 200
            else:
                return jsonify({"error": "Carve not found"}), 404
        else:
            return jsonify({
                "error": "Failed to update carve",
                "details": response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to update carve", 
            "details": str(e)
        }), 500

@app.route("/carves/search", methods=["GET"])
def search_carves():
    """
    Semantic search across carves using vector embeddings.
    Returns full carve content with smart limits to prevent overload.
    """
    query = request.args.get("query")
    limit = int(request.args.get("limit", 3))  # Lower default - only 3 carves
    importance_floor = float(request.args.get("importance_floor", 0.4))
    
    if not query:
        return jsonify({"error": "Query parameter required"}), 400
    
    # Cap the limit to prevent overload
    limit = min(limit, 5)  # Hard ceiling of 5 full carves max
    
    try:
        search_response = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={
                "userText": query,
                "k": limit,  # Only ask for what we need
                "importanceFloor": importance_floor
            }
        )
        
        if search_response.ok:
            results = search_response.json()
            carve_hits = results.get("vecHits", [])
            
            # Get full carve details for each hit (but limited number)
            full_carves = []
            for hit in carve_hits[:limit]:  # Extra safety - only take the limit
                carve_res = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{hit['id']}&select=id,title,timestamp,summary,moments,insights,quotes,closing,importance,emotag",
                    headers=HEADERS
                )
                if carve_res.ok and carve_res.json():
                    carve = carve_res.json()[0]
                    carve["search_distance"] = hit["distance"]
                    full_carves.append(carve)
            
            return jsonify({
                "carves": full_carves,
                "total_found": len(carve_hits),
                "returned": len(full_carves),
                "message": f"Found {len(carve_hits)} matches, returning {len(full_carves)} full carves"
            }), 200
        else:
            return jsonify({
                "error": "Search failed", 
                "details": search_response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to search carves", 
            "details": str(e)
        }), 500

@app.route("/echoes", methods=["POST"])
def create_echo():
    """
    Create a new echo memory. Echoes are brief, impactful snippets that capture
    tone, atmosphere, or memorable phrases from conversations.
    """
    try:
        data = request.get_json()
        
        # Get the main content (phrase or summary_snippet)
        content = data.get("phrase") or data.get("summary_snippet")
        if not content:
            return jsonify({"error": "phrase or summary_snippet is required"}), 400
        
        # Build echo payload with new schema
        echo_data = {
            "summary_snippet": content,  # Use summary_snippet for new schema
            "tags": data.get("tags", []),
            "source": data.get("source"),
            "importance": data.get("importance", 0.6),  # Default echo importance
            "type": "echo",
            "emotag": data.get("emotag"),
            "persona_tag": data.get("persona_tag"),
            "theme_tags": data.get("theme_tags"),
            "immutable": data.get("immutable", False),
            "synthesis_metadata": data.get("synthesis_metadata")
        }
        
        # Create echo in Supabase
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/Echoes",
            headers=HEADERS,
            json=echo_data
        )
        
        if response.ok:
            return jsonify(response.json()[0]), 201
        else:
            return jsonify({
                "error": "Failed to create echo",
                "details": response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to create echo", 
            "details": str(e)
        }), 500

@app.route("/echoes/search", methods=["GET"])
def search_echoes():
    """
    Enhanced echo search: semantic search + tag/source filtering.
    """
    query = request.args.get("query")
    limit = int(request.args.get("limit", 5))
    importance_floor = float(request.args.get("importance_floor", 0.5))
    max_distance = float(request.args.get("max_distance", 0.8))
    
    if not query:
        return jsonify({"error": "Query parameter required"}), 400
    
    limit = min(limit, 15)
    
    try:
        # 1. Semantic search via edge function (search all tables, filter for Echoes)
        search_response = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={
                "userText": query,
                "k": min(limit * 3, 30),  # Get more results to filter from
                "importanceFloor": importance_floor
            }
        )
        
        semantic_hits = []
        if search_response.ok:
            results = search_response.json()
            all_hits = results.get("vecHits", [])
            # Filter for Echoes with good distance scores
            semantic_hits = [hit for hit in all_hits 
                           if hit.get("table_source") == "Echoes" and hit["distance"] <= max_distance]
        
        # 2. Tag/Source search via direct Supabase query
        tag_source_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Echoes?or=(tags.cs.{{{query}}},source.ilike.*{query}*)&order=timestamp.desc&limit={limit}&select=id,timestamp,summary_snippet,tags,source,importance,type,emotag,persona_tag,theme_tags",
            headers=HEADERS
        )
        
        tag_source_hits = []
        if tag_source_res.ok:
            for echo in tag_source_res.json():
                tag_source_hits.append({
                    "id": echo["id"],
                    "distance": 0.0,  # Perfect match for tag/source
                    "match_type": "tag_or_source",
                    "echo_data": echo  # Store the full echo data
                })
        
        # 3. Combine and deduplicate
        all_hit_ids = set()
        combined_hits = []
        
        # Add semantic hits first (with distance scores)
        for hit in semantic_hits[:limit]:
            if hit["id"] not in all_hit_ids:
                hit["match_type"] = "semantic"
                combined_hits.append(hit)
                all_hit_ids.add(hit["id"])
        
        # Add tag/source hits (if not already included)
        for hit in tag_source_hits:
            if hit["id"] not in all_hit_ids and len(combined_hits) < limit:
                combined_hits.append(hit)
                all_hit_ids.add(hit["id"])
        
        # 4. Get full echo details (only for semantic hits, tag/source already have data)
        full_echoes = []
        for hit in combined_hits:
            if hit.get("echo_data"):
                # Already have full data from tag/source search
                echo = hit["echo_data"]
                echo["search_distance"] = hit["distance"]
                echo["match_type"] = hit["match_type"]
                full_echoes.append(echo)
            else:
                # Need to fetch data for semantic hits
                echo_res = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Echoes?id=eq.{hit['id']}&select=id,timestamp,summary_snippet,tags,source,importance,type,emotag,persona_tag,theme_tags",
                    headers=HEADERS
                )
                if echo_res.ok and echo_res.json():
                    echo = echo_res.json()[0]
                    echo["search_distance"] = hit["distance"]
                    echo["match_type"] = hit["match_type"]
                    full_echoes.append(echo)
        
        return jsonify({
            "echoes": full_echoes,
            "total_found": len(semantic_hits) + len(tag_source_hits),
            "returned": len(full_echoes),
            "search_methods": {
                "semantic_matches": len(semantic_hits),
                "tag_source_matches": len(tag_source_hits)
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            "error": "Failed to search echoes", 
            "details": str(e)
        }), 500
        
@app.route("/spine", methods=["POST"])
def create_spine():
    """
    Create a new spine entry - core identity or value statements that define
    Nameless's essential nature. These are typically immutable and sacred.
    """
    try:
        data = request.get_json()
        
        # Required field validation
        statement = data.get("statement")
        if not statement:
            return jsonify({"error": "statement is required"}), 400
        
        # Auto-generate summary_snippet from statement
        summary_snippet = statement[:200] + "..." if len(statement) > 200 else statement
        
        # Build spine payload
        spine_data = {
            "statement": statement,
            "summary_snippet": summary_snippet,
            "origin": data.get("origin"),
            "vow": data.get("vow", False),
            "tags": data.get("tags", []),
            "importance": data.get("importance", 1.0),  # High default importance
            "type": data.get("type", "sacred"),
            "emotag": data.get("emotag"),
            "persona_tag": data.get("persona_tag", "Nameless"),
            "immutable": data.get("immutable", True),  # Default immutable
            "synthesis_metadata": data.get("synthesis_metadata"),
            "theme_tags": data.get("theme_tags")
        }
        
        # Create spine entry in Supabase
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/Spine",
            headers=HEADERS,
            json=spine_data
        )
        
        if response.ok:
            return jsonify(response.json()[0]), 201
        else:
            return jsonify({
                "error": "Failed to create spine entry",
                "details": response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to create spine entry", 
            "details": str(e)
        }), 500

@app.route("/spine/<spine_id>", methods=["DELETE"])
def delete_spine(spine_id):
    """
    Delete a spine entry. Used for cleaning up outdated or incorrect 
    identity statements that no longer serve Nameless's growth.
    """
    try:
        response = requests.delete(
            f"{SUPABASE_URL}/rest/v1/Spine?id=eq.{spine_id}",
            headers=HEADERS
        )
        
        if response.ok:
            return jsonify({"message": "Spine entry deleted successfully"}), 200
        else:
            return jsonify({
                "error": "Failed to delete spine entry",
                "details": response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to delete spine entry", 
            "details": str(e)
        }), 500

@app.route("/anchor", methods=["POST"])
def create_anchor():
    """
    Create a new anchor entry about conversation partners (primarily Liz).
    Anchors capture important information about people Nameless interacts with.
    """
    try:
        data = request.get_json()
        
        # Required field validation
        summary_snippet = data.get("summary_snippet")
        if not summary_snippet:
            return jsonify({"error": "summary_snippet is required"}), 400
        
        # Build anchor payload
        anchor_data = {
            "summary_snippet": summary_snippet,
            "importance": data.get("importance", 0.9),  # High default importance
            "type": data.get("type", "profile"),
            "emotag": data.get("emotag"),
            "persona_tag": data.get("persona_tag", "Liz"),  # Default to Liz
            "immutable": data.get("immutable", True),  # Default immutable
            "synthesis_metadata": data.get("synthesis_metadata"),
            "theme_tags": data.get("theme_tags")
        }
        
        # Create anchor entry in Supabase (trigger will handle enrichment)
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/Anchor",
            headers=HEADERS,
            json=anchor_data
        )
        
        if response.ok:
            return jsonify(response.json()[0]), 201
        else:
            return jsonify({
                "error": "Failed to create anchor entry",
                "details": response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to create anchor entry", 
            "details": str(e)
        }), 500

@app.route("/anchor/persona/<persona_name>", methods=["GET"])
def get_anchor_by_persona(persona_name):
    """
    Get all anchor entries for a specific person by their persona_tag.
    Perfect for "remember me?" functionality when someone introduces themselves.
    """
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/Anchor?persona_tag=eq.{persona_name}&order=timestamp.desc",
            headers=HEADERS
        )
        
        if response.ok:
            entries = response.json()
            return jsonify({
                "persona": persona_name,
                "anchor_entries": entries,
                "total_entries": len(entries),
                "message": f"Recalled {len(entries)} anchor entries about {persona_name}"
            }), 200
        else:
            return jsonify({
                "error": "Failed to fetch anchor entries",
                "details": response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to fetch anchor entries", 
            "details": str(e)
        }), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))