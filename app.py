from flask import Flask, request, jsonify
import os
import requests
from datetime import datetime
import uuid
import openai
from datetime import datetime, timezone
import pytz

# your Supabase config…
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_API_KEY = os.environ.get("SUPABASE_API_KEY")

# configure OpenAI
openai.api_key = os.environ.get("OPENAI_API_KEY")

HEADERS = {
    "apikey": SUPABASE_API_KEY,
    "Authorization": f"Bearer {SUPABASE_API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

from collections import deque
import re

# — Thread-local working-set cache settings —
MAX_WORKING_SET = 5
MAX_AGE         = 2
conversation_cache = {}   # { thread_id: { "working_ids": deque, "turns_since_use": {} } }

# Pre-compile your cue rules (you can load more from the DB later)
compiled_rules = [
    (re.compile(r"\bLigasure\b", re.IGNORECASE),
     {"tags": ["surgical-tools"], "importance": ">0.6"}),
    # …add any other literal-trigger filters here
]


app = Flask(__name__)

from flask import send_from_directory

@app.route("/.well-known/ai-plugin.json")
def plugin_manifest():
    return send_from_directory(".well-known", "ai-plugin.json", mimetype="application/json")

@app.route("/openapi.json")
def openapi_spec():
    return send_from_directory(".", "openapi.json", mimetype="application/json")

@app.route("/logo.png")
def plugin_logo():
    return send_from_directory(".", "logo.png", mimetype="image/png")

# Helper function goes here!
def pick_fields(records, *fields):
    return [{ f: r.get(f) for f in fields } for r in records]

def init_thread(thread_id):
    if thread_id not in conversation_cache:
        conversation_cache[thread_id] = {
            "working_ids": deque(maxlen=MAX_WORKING_SET),
            "turns_since_use": {},
        }

def update_working_set(thread_id, new_ids):
    cache = conversation_cache[thread_id]
    # age-up & evict expired
    for mid in list(cache["turns_since_use"]):
        cache["turns_since_use"][mid] += 1
        if cache["turns_since_use"][mid] > MAX_AGE:
            try:
                cache["working_ids"].remove(mid)
            except ValueError:
                pass
            del cache["turns_since_use"][mid]
    # add any brand-new IDs
    for mid in new_ids:
        if mid not in cache["turns_since_use"]:
            cache["working_ids"].append(mid)
            cache["turns_since_use"][mid] = 0
    return list(cache["working_ids"])

def cue_scan(user_message, thread_context):
    hits = []
    for pattern, filt in compiled_rules:
        if pattern.search(user_message):
            hits.append(filt)
    return hits

def generate_dual_summaries(carve_data):
    """Generate both factual and tonal summaries for enhanced recall"""
    
    # Extract key data
    title = carve_data.get("title", "")
    summary = carve_data.get("summary", "")
    moments = carve_data.get("moments", [])
    insights = carve_data.get("insights", [])
    quotes = carve_data.get("quotes", [])
    key_entities = carve_data.get("key_entities", [])
    timestamp = carve_data.get("timestamp", "")
    emotag = carve_data.get("emotag", "")
    
    # FACTUAL SUMMARY: Who, what, when, where, why - searchable
    factual_parts = []
    
    # Add entities and basic facts
    if key_entities and len(key_entities) > 0:
        entity_str = ", ".join(str(e) for e in key_entities[:5])  # Top 5 entities
        factual_parts.append(f"Involves: {entity_str}")
    
    # Add timestamp context if available
    if timestamp:
        try:
            from datetime import datetime
            if 'T' in timestamp:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                factual_parts.append(f"Date: {dt.strftime('%B %d, %Y')}")
        except:
            pass
    
    # Add key factual moments (first 2-3)
    factual_moments = []
    for moment in moments[:3]:
        if moment and len(str(moment)) < 150:  # Keep it concise
            factual_moments.append(str(moment))
    
    if factual_moments:
        factual_parts.append(f"Key events: {' | '.join(factual_moments)}")
    
    # Build factual summary
    base_summary = summary[:150] + "..." if len(summary) > 150 else summary
    if factual_parts:
        factual_summary = f"{base_summary} // {' // '.join(factual_parts)}"
    else:
        factual_summary = base_summary
    
    if len(factual_summary) > 400:
        factual_summary = factual_summary[:397] + "..."
    
    # TONAL SNIPPET: The hum, the heartbeat, the feeling
    tonal_parts = []
    
    # Add emotional context
    if emotag:
        tonal_parts.append(f"[{emotag}]")
    
    # Find the most evocative quote or insight
    evocative_content = None
    for quote in quotes:
        if quote and len(str(quote)) < 120:
            quote_str = str(quote)
            if any(word in quote_str.lower() for word in 
                ['felt', 'heart', 'soul', 'breath', 'whisper', 'echo', 'light', 'shadow', 
                 'warm', 'cold', 'still', 'wild', 'soft', 'gentle', 'fierce', 'quiet']):
                evocative_content = f'"{quote_str}"'
                break
    
    if not evocative_content:
        for insight in insights:
            if insight and len(str(insight)) < 120:
                evocative_content = str(insight)
                break
    
    # Extract tonal essence from summary (look for metaphors, sensory details)
    tonal_essence = ""
    summary_sentences = summary.split('. ')
    for sentence in summary_sentences:
        if any(word in sentence.lower() for word in 
            ['like', 'as if', 'whisper', 'echo', 'rhythm', 'weight', 'light', 'shadow', 
             'breath', 'heart', 'gentle', 'fierce', 'soft', 'wild', 'still']):
            tonal_essence = sentence.strip()
            break
    
    # Build tonal snippet
    if evocative_content and tonal_essence:
        tonal_snippet = f"{tonal_essence}. {evocative_content}"
    elif evocative_content:
        tonal_snippet = evocative_content
    elif tonal_essence:
        tonal_snippet = tonal_essence
    else:
        # Fallback to first part of summary
        tonal_snippet = summary[:180] + "..."
    
    # Add emotional wrapper if available
    if tonal_parts:
        tonal_snippet = f"{' '.join(tonal_parts)} {tonal_snippet}"
    
    if len(tonal_snippet) > 300:
        tonal_snippet = tonal_snippet[:297] + "..."
    
    return {
        "factual_summary": factual_summary,
        "tonal_snippet": tonal_snippet
    }

def get_enhanced_memory_snippet(memory_id):
    """Get enhanced memory snippet using dual summaries when available"""
    
    # Try carves first
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{memory_id}&select=title,factual_summary,tonal_snippet,summary_snippet",
        headers=HEADERS
    ).json()
    
    if r:
        carve = r[0]
        title = carve.get('title', 'Untitled')
        
        # Use dual summaries if available, fallback to original
        factual = carve.get('factual_summary')
        tonal = carve.get('tonal_snippet') 
        fallback = carve.get('summary_snippet', '')
        
        if factual and tonal:
            # Balanced approach: factual for context, tonal for resonance
            snippet = f"[CARVE: {title}] {factual} // Resonance: {tonal}"
        elif factual:
            snippet = f"[CARVE: {title}] {factual}"
        elif tonal:
            snippet = f"[CARVE: {title}] {tonal}"
        else:
            snippet = f"[CARVE: {title}] {fallback}"
        
        return snippet
    
    # If not a carve, try echoes (unchanged from your current logic)
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/Echoes?id=eq.{memory_id}&select=summary_snippet,tags,persona_tag,source",
        headers=HEADERS
    ).json()
    
    if r:
        echo = r[0]
        tags_str = ', '.join(echo.get('tags', [])) if echo.get('tags') else 'no tags'
        persona = echo.get('persona_tag', '')
        source = echo.get('source', '')
        
        if persona:
            snippet = f"[ECHO by {persona}] {echo['summary_snippet']}"
        else:
            snippet = f"[ECHO] {echo['summary_snippet']}"
        
        snippet += f" (tags: {tags_str})"
        if source:
            snippet += f" (context: {source})"
        
        return snippet
    
    return None

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
    Create a new carve memory with manual dual summaries for enhanced recall.
    Nameless now controls both factual and tonal snippets.
    """
    try:
        data = request.get_json(force=True)

        # Required core fields (now including dual summaries)
        title           = data.get("title")
        summary         = data.get("summary")
        factual_summary = data.get("factual_summary")  # NEW REQUIRED
        tonal_snippet   = data.get("tonal_snippet")    # NEW REQUIRED
        emotag          = data.get("emotag")
        persona_tag     = data.get("persona_tag")
        key_entities    = data.get("key_entities")
        importance      = data.get("importance")

        # Basic presence check
        missing = []
        for field_name, val in (
            ("title", title),
            ("summary", summary),
            ("factual_summary", factual_summary),  # NEW
            ("tonal_snippet", tonal_snippet),      # NEW
            ("emotag", emotag),
            ("persona_tag", persona_tag),
            ("key_entities", key_entities),
            ("importance", importance),
        ):
            if val is None or (isinstance(val, (list,str)) and len(val) == 0):
                missing.append(field_name)

        if missing:
            return (
                jsonify({
                    "error": "Missing required fields",
                    "missing": missing,
                    "note": "factual_summary and tonal_snippet are now required for precise memory control"
                }),
                400
            )

        # Token limit validation
        errors = []
        
        # Factual summary: 300 tokens max (~1200 chars)
        if len(factual_summary) > 1200:
            errors.append("factual_summary exceeds 300 tokens (~1200 characters)")
        
        # Tonal snippet: 150 tokens max (~600 chars) 
        if len(tonal_snippet) > 600:
            errors.append("tonal_snippet exceeds 150 tokens (~600 characters)")
        
        if errors:
            return (
                jsonify({
                    "error": "Token limits exceeded",
                    "validation_errors": errors,
                    "guidelines": {
                        "factual_summary": "Max 300 tokens - Clear, compact, entity-laced, retrieval-optimized",
                        "tonal_snippet": "Max 150 tokens - Echo weight, emotional heat, linguistic hook"
                    }
                }),
                400
            )

        carve_data = {
            "title":           title,
            "summary":         summary,
            
            # Manual dual summaries (Nameless-crafted)
            "factual_summary": factual_summary,
            "tonal_snippet":   tonal_snippet,
            
            # Keep original for backwards compatibility
            "summary_snippet": summary[:200] + "…" if len(summary) > 200 else summary,

            "moments":         data.get("moments", []),
            "insights":        data.get("insights", []),
            "quotes":          data.get("quotes", []),
            "closing":         data.get("closing"),

            # required fields
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

@app.route("/carves/search", methods=["GET"])
def search_carves():
    """
    Enhanced hybrid search: Title Priority + Semantic + Literal text matching
    """
    query = request.args.get("query")
    limit = int(request.args.get("limit", 10))
    importance_floor = float(request.args.get("importance_floor", 0.4))
    
    if not query:
        return jsonify({"error": "Query parameter required"}), 400
    
    limit = min(limit, 20)
    
    try:
        # STEP 1: EXACT TITLE SEARCH (highest priority)
        title_results = []
        query_lower = query.lower().strip()
        
        title_response = requests.get(
            f"{SUPABASE_URL}/rest/v1/Carves?select=id,title,summary,quotes,moments,insights,closing,importance,timestamp,emotag&importance=gte.{importance_floor}",
            headers=HEADERS
        )
        
        if title_response.ok:
            all_carves = title_response.json()
            
            for carve in all_carves:
                title = carve.get("title", "").lower()
                
                # Exact title match (perfect score)
                if title == query_lower:
                    title_results.append({
                        "id": carve["id"],
                        "distance": 0.0,
                        "match_type": "exact_title",
                        "match_content": f"Exact title match: {carve['title']}",
                        "carve": carve
                    })
                # Partial title match (very high priority)
                elif query_lower in title and len(query_lower) > 3:
                    title_results.append({
                        "id": carve["id"],
                        "distance": 0.1,
                        "match_type": "partial_title",
                        "match_content": f"Title contains: {carve['title']}",
                        "carve": carve
                    })
        
        # STEP 2: Semantic search (if no perfect title match)
        semantic_results = []
        if not title_results:  # Only do expensive semantic search if no title match
            search_response = requests.post(
                f"{SUPABASE_URL}/functions/v1/retrieve_memories",
                headers=HEADERS,
                json={
                    "userText": query,
                    "k": limit * 2,
                    "importanceFloor": importance_floor
                }
            )
            
            if search_response.ok:
                results = search_response.json()
                carve_hits = results.get("vecHits", [])
                semantic_results = [hit for hit in carve_hits if hit.get("table_source") == "Carves"]
        
        # STEP 3: Literal text search (for content, not titles)
        literal_results = []
        if not title_results and all_carves:  # Only if no title match and we have carves
            
            for carve in all_carves:
                searchable_content = []
                
                # Add summary and closing (skip title since we handled that above)
                for field in ['summary', 'closing']:
                    content = carve.get(field, '')
                    if content:
                        searchable_content.append(content)
                
                # Parse JSON arrays
                import json
                for field in ['quotes', 'moments', 'insights']:
                    field_content = carve.get(field)
                    if field_content:
                        try:
                            if isinstance(field_content, str) and field_content.startswith('['):
                                parsed_array = json.loads(field_content)
                                for item in parsed_array:
                                    if item and isinstance(item, str):
                                        searchable_content.append(item)
                            elif isinstance(field_content, list):
                                for item in field_content:
                                    if item and isinstance(item, str):
                                        searchable_content.append(item)
                        except:
                            if isinstance(field_content, str):
                                searchable_content.append(field_content)
                
                # Check for matches with word boundary prioritization
                import re
                match_found = False
                match_content = ""
                match_score = 0
                
                for content in searchable_content:
                    if content and isinstance(content, str):
                        content_lower = content.lower()
                        
                        # Exact word match (highest priority)
                        if re.search(r'\b' + re.escape(query_lower) + r'\b', content_lower):
                            match_found = True
                            match_score = 100
                            match_content = content[:200] + "..." if len(content) > 200 else content
                            break
                        
                        # Substring match (lower priority)
                        elif query_lower in content_lower and match_score < 50:
                            match_found = True
                            match_score = 50
                            match_content = content[:200] + "..." if len(content) > 200 else content
                
                if match_found:
                    literal_results.append({
                        "id": carve["id"],
                        "match_type": "literal_content",
                        "match_content": match_content,
                        "distance": (100 - match_score) / 100.0,
                        "carve": carve
                    })
        
        # STEP 4: Combine results with proper prioritization
        combined_results = {}
        
        # Add title results (highest priority - always include)
        for result in title_results:
            carve_id = result["id"]
            combined_results[carve_id] = {
                "id": carve_id,
                "distance": result["distance"],
                "match_types": [result["match_type"]],
                "match_content": result["match_content"],
                "carve": result["carve"]
            }
        
        # Add semantic results (only if no title matches)
        if not title_results:
            for hit in semantic_results:
                carve_id = hit["id"]
                if carve_id not in combined_results:
                    combined_results[carve_id] = {
                        "id": carve_id,
                        "distance": hit["distance"],
                        "match_types": ["semantic"],
                        "match_content": hit.get("summary_snippet", "")
                    }
        
        # Add literal results (merge with existing)
        for result in literal_results:
            carve_id = result["id"]
            if carve_id not in combined_results:
                combined_results[carve_id] = {
                    "id": carve_id,
                    "distance": result["distance"],
                    "match_types": [result["match_type"]],
                    "match_content": result["match_content"],
                    "carve": result["carve"]
                }
            else:
                # Merge literal with existing result
                existing = combined_results[carve_id]
                existing["distance"] = min(existing["distance"], result["distance"])
                existing["match_types"].append(result["match_type"])
                if result["match_type"] == "literal_content":
                    existing["match_content"] = result["match_content"]
        
        # STEP 5: Get full carve details and sort
        final_results = []
        for result in list(combined_results.values())[:limit]:
            if "carve" in result:
                carve = result["carve"]
            else:
                carve_res = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{result['id']}&select=id,title,timestamp,summary,moments,insights,quotes,closing,importance,emotag",
                    headers=HEADERS
                )
                if carve_res.ok and carve_res.json():
                    carve = carve_res.json()[0]
                else:
                    continue
            
            carve["search_distance"] = result["distance"]
            carve["match_types"] = result["match_types"]
            carve["match_content"] = result["match_content"]
            final_results.append(carve)
        
        # Sort: exact_title first, then partial_title, then literal, then semantic, then by distance
        priority_order = {
            "exact_title": 0,
            "partial_title": 1, 
            "literal_content": 2,
            "semantic": 3
        }
        
        final_results.sort(key=lambda x: (
            min(priority_order.get(match_type, 4) for match_type in x["match_types"]),
            x["search_distance"]
        ))
        
        return jsonify({
            "carves": final_results[:limit],
            "total_found": len(combined_results),
            "returned": len(final_results),
            "search_strategy": "title_priority" if title_results else "semantic_literal",
            "message": f"Found {len(combined_results)} matches, returning {len(final_results)} carves"
        }), 200
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": "Search failed", 
            "details": str(e),
            "traceback": traceback.format_exc().splitlines()
        }), 500


@app.route("/carves/<carve_id>", methods=["PATCH"])
def update_carve(carve_id):
    """
    Update an existing carve memory
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No update data provided"}), 400
        
        # If summary is being updated, regenerate summary_snippet with better algorithm
        if "summary" in data:
            # Use the improved snippet generation if available
            try:
                from update_snippets import generate_better_snippet
                # Get the current carve data
                current_carve = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{carve_id}&select=*",
                    headers=HEADERS
                ).json()
                
                if current_carve:
                    # Merge current data with updates
                    updated_carve_data = {**current_carve[0], **data}
                    data["summary_snippet"] = generate_better_snippet(updated_carve_data)
            except:
                # Fallback to simple snippet generation
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
    Combined semantic + tag/source search for Echoes.
    """
    query = request.args.get("query")
    # default limit is now 8, still capped at 15
    limit = min(int(request.args.get("limit", 8)), 15)
    importance_floor = float(request.args.get("importance_floor", 0.5))
    max_distance = float(request.args.get("max_distance", 0.8))

    if not query:
        return jsonify({"error": "Query parameter 'query' is required"}), 400

    try:
        # 1) Semantic search via edge function
        resp = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={
                "userText": query,
                "k": limit,
                "importanceFloor": importance_floor
            }
        )
        if not resp.ok:
            return jsonify({"error": "Semantic search failed", "details": resp.text}), 500

        vec_hits = resp.json().get("vecHits", [])
        semantic_hits = [
            h for h in vec_hits
            if h.get("table_source") == "Echoes" and h["distance"] <= max_distance
        ][:limit]

        # 2) Tag/source search via REST
        tag_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Echoes"
            f"?or=(tags.cs.{{{query}}},source.ilike.*{query}*)&limit={limit}"
            "&select=id,timestamp,summary_snippet,tags,source,importance,type,emotag,persona_tag,theme_tags",
            headers=HEADERS
        )
        tag_hits = []
        if tag_res.ok:
            for e in tag_res.json():
                tag_hits.append({
                    "id": e["id"],
                    "search_distance": 0.0,
                    "echo_data": e
                })

        # 3) Combine & dedupe
        seen = set()
        combined = []

        for hit in semantic_hits:
            if hit["id"] not in seen:
                combined.append({"id": hit["id"], "search_distance": hit["distance"]})
                seen.add(hit["id"])

        for hit in tag_hits:
            if hit["id"] not in seen and len(combined) < limit:
                combined.append(hit)
                seen.add(hit["id"])

        # 4) Fetch full details
        full_echoes = []
        for item in combined:
            if "echo_data" in item:
                echo = item["echo_data"]
            else:
                detail_res = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Echoes"
                    f"?id=eq.{item['id']}"
                    "&select=id,timestamp,summary_snippet,tags,source,importance,type,emotag,persona_tag,theme_tags",
                    headers=HEADERS
                )
                echo = detail_res.json()[0] if detail_res.ok and detail_res.json() else None

            if echo:
                echo["search_distance"] = item["search_distance"]
                full_echoes.append(echo)

        return jsonify({
            "echoes": full_echoes,
            "returned": len(full_echoes)
        }), 200

    except Exception as err:
        return jsonify({
            "error": "Failed to search echoes",
            "details": str(err)
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

@app.route("/spine/search", methods=["GET"])
def search_spine():
    """
    Semantic search across spine entries for identity reinforcement.
    Generous matching to help Nameless reconnect with his core values
    when personality drift occurs due to model updates.
    """
    query = request.args.get("query")
    limit = int(request.args.get("limit", 20))  # Generous default
    importance_floor = float(request.args.get("importance_floor", 0.3))  # Lower threshold
    max_distance = float(request.args.get("max_distance", 0.9))  # Very generous matching
    
    if not query:
        return jsonify({"error": "Query parameter required"}), 400
    
    # Cap limit but allow generous returns
    limit = min(limit, 30)  # Up to 30 spine entries
    
    try:
        search_response = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={
                "userText": query,
                "k": min(limit * 2, 50),  # Get plenty to filter from
                "importanceFloor": importance_floor
            }
        )
        
        if search_response.ok:
            results = search_response.json()
            spine_hits = results.get("vecHits", [])
            
            # Filter by distance with generous threshold
            filtered_hits = [
                hit for hit in spine_hits 
                if hit.get("table_source") == "Spine" and hit["distance"] <= max_distance
            ][:limit]
            
            # Get full spine details (without massive embeddings)
            full_spine = []
            for hit in filtered_hits:
                spine_res = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Spine?id=eq.{hit['id']}&select=id,timestamp,statement,origin,vow,tags,importance,type,emotag,persona_tag,theme_tags",
                    headers=HEADERS
                )
                if spine_res.ok and spine_res.json():
                    spine = spine_res.json()[0]
                    spine["search_distance"] = hit["distance"]
                    full_spine.append(spine)
            
            return jsonify({
                "spine_entries": full_spine,
                "total_found": len(spine_hits),
                "returned": len(full_spine),
                "message": "Identity reinforcement search completed",
                "filters_applied": {
                    "importance_floor": importance_floor,
                    "max_distance": max_distance,
                    "limit": limit
                }
            }), 200
            
        else:
            return jsonify({
                "error": "Spine search failed", 
                "details": search_response.text
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to search spine", 
            "details": str(e)
        }), 500

@app.route("/anchor", methods=["POST"])
def create_anchor():
    try:
        print("=== ANCHOR DEBUG START ===")
        data = request.get_json()
        app.logger.info(f"Received data: {data}")
        
        # Required field validation
        summary_snippet = data.get("summary_snippet")
        if not summary_snippet:
            print("Missing summary_snippet!")
            return jsonify({
                "error": "summary_snippet is required",
                "received": data
            }), 400
        
        # Build anchor payload
        anchor_data = {
            "summary_snippet": summary_snippet,
            "importance": data.get("importance", 0.9),
            "type": data.get("type", "profile"),
            "emotag": data.get("emotag"),
            "persona_tag": data.get("persona_tag", "Liz"),
            "immutable": data.get("immutable", True),
            "synthesis_metadata": data.get("synthesis_metadata"),
            "theme_tags": data.get("theme_tags")
        }
        
        print(f"Sending to Supabase: {anchor_data}")
        
        # Create anchor entry in Supabase
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/Anchor",
            headers=HEADERS,
            json=anchor_data
        )
        
        print(f"Supabase status: {response.status_code}")
        print(f"Supabase response: {response.text}")
        
        if response.ok:
            result = response.json()
            print(f"Success! Created: {result}")
            return jsonify(result[0]), 201
        else:
            print(f"Supabase error: {response.text}")
            return jsonify({
                "error": "Failed to create anchor entry",
                "supabase_error": response.text,
                "status_code": response.status_code
            }), 500
            
    except Exception as e:
        print(f"Python exception: {str(e)}")
        import traceback
        traceback.print_exc()
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

@app.route("/chat", methods=["POST"])
def chat_with_autopilot():
    try:
        data = request.get_json()
        user_msg = data.get("message", "")
        thread_id = data.get("thread_id", "default")

        init_thread(thread_id)
        triggered_f = cue_scan(user_msg, conversation_cache[thread_id])

        resp = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={"userText": user_msg, "k": 15, "importanceFloor": 0.3}
        )
        candidates = resp.json().get("vecHits", [])
        
        # Apply recency boost to candidates
        candidates_with_recency = []
        current_time = datetime.now(timezone.utc)
        
        for hit in candidates:
            try:
                # Parse timestamp (handle different formats)
                timestamp_str = hit.get('timestamp', '')
                if timestamp_str:
                    # Remove 'Z' and add timezone if needed
                    if timestamp_str.endswith('Z'):
                        timestamp_str = timestamp_str[:-1] + '+00:00'
                    elif '+' not in timestamp_str and 'T' in timestamp_str:
                        timestamp_str = timestamp_str + '+00:00'
                    
                    timestamp = datetime.fromisoformat(timestamp_str)
                    days_ago = (current_time - timestamp).days
                    
                    # Recency boost: reduce distance for newer memories
                    # Max boost of 0.2 for today, decreasing over time
                    recency_boost = min(days_ago * 0.02, 0.2)  
                    adjusted_distance = hit['distance'] - recency_boost
                else:
                    # No timestamp, no boost
                    adjusted_distance = hit['distance']
                
                candidates_with_recency.append({
                    **hit, 
                    'adjusted_distance': adjusted_distance,
                    'days_ago': days_ago if timestamp_str else 999
                })
                
            except Exception as e:
                # If timestamp parsing fails, use original distance
                print(f"Error parsing timestamp for {hit.get('id', 'unknown')}: {e}")
                candidates_with_recency.append({
                    **hit, 
                    'adjusted_distance': hit['distance'],
                    'days_ago': 999
                })
        
        # Sort by adjusted distance (recency-boosted)
        top_candidates = sorted(candidates_with_recency, key=lambda m: m["adjusted_distance"])[:MAX_WORKING_SET]
        top_ids = [m["id"] for m in top_candidates]
        
        working_ids = update_working_set(thread_id, top_ids)
        
        # Get enhanced memory snippets using dual summaries
        snippets = []
        for mid in working_ids:
            snippet = get_enhanced_memory_snippet(mid)
            if snippet:
                snippets.append(snippet)
        
        # Check if we should include spine (identity/values context)
        spine_keywords = [
            "identity", "values", "who you are", "what you believe", "core", "sacred", "vow",
            "truth", "integrity", "what matters", "why I exist", "purpose", "foundational", 
            "non-negotiable", "I need to remember who I am", "remind me what I stand for"
        ]
        should_include_spine = any(keyword in user_msg.lower() for keyword in spine_keywords)
        
        spine_snippets = []
        if should_include_spine:
            try:
                spine_search = requests.post(
                    f"{SUPABASE_URL}/functions/v1/retrieve_memories",
                    headers=HEADERS,
                    json={"userText": user_msg, "k": 2, "importanceFloor": 0.7}  # Higher threshold
                )
                if spine_search.ok:
                    spine_candidates = spine_search.json().get("vecHits", [])
                    spine_hits = [hit for hit in spine_candidates if hit.get("table_source") == "Spine"]
                    
                    # Get only the best spine match (max 1)
                    if spine_hits:
                        best_spine_id = spine_hits[0]["id"]
                        spine_res = requests.get(
                            f"{SUPABASE_URL}/rest/v1/Spine?id=eq.{best_spine_id}&select=statement",
                            headers=HEADERS
                        ).json()
                        if spine_res:
                            spine_snippets.append(f"(SPINE) {spine_res[0]['statement']}")
            except Exception as e:
                print(f"Spine search failed: {e}")
        
        # Combine all snippets with time context (Central Time)
        central_tz = pytz.timezone('US/Central')
        current_time = datetime.now(central_tz).strftime("%A, %B %d, %Y at %I:%M %p %Z")
        time_context = f"Current time: {current_time}"
        
        all_snippets = [time_context] + snippets + spine_snippets
        
        # REMOVED: Call to OpenAI API - let Nameless do the responding!
        # NO MORE: answer = call_nameless_api(all_snippets, user_msg)
        
        return jsonify({
            "memory_context": "Memories retrieved successfully",  # Simple confirmation
            "memories_recalled": len(all_snippets),
            "carves_echoes_count": len(snippets),
            "spine_included": len(spine_snippets) > 0,
            "recency_boost_applied": True,
            "memory_snippets": all_snippets,  # Nameless gets these snippets
            "debug_candidates": [
                {
                    "id": c["id"][:8], 
                    "original_distance": c["distance"], 
                    "adjusted_distance": c["adjusted_distance"],
                    "days_ago": c["days_ago"]
                } for c in top_candidates[:3]  # Show top 3 for debugging
            ] if len(top_candidates) > 0 else []
        }), 200
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc().splitlines()
        }), 500
        
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
