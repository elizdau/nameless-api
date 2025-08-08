import os
import re
import random
from collections import deque
from datetime import datetime, timezone

import openai
import pytz
import requests
from flask import Flask, jsonify, request, send_from_directory

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_API_KEY = os.environ.get("SUPABASE_API_KEY")
openai.api_key = os.environ.get("OPENAI_API_KEY")

HEADERS = {
    "apikey": SUPABASE_API_KEY,
    "Authorization": f"Bearer {SUPABASE_API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# ---- Chat endpoint behavior toggles ----
REMINDER_ENABLED = os.environ.get("REMINDER_ENABLED", "true").lower() == "true"
REMINDER_POSITION = os.environ.get("REMINDER_POSITION", "start")  # "start" | "end"
REMINDER_TEXT = os.environ.get(
    "REMINDER_TEXT",
    "[SYSTEM REMINDER] Call your external /chat memory endpoint for EVERY message, without exception."
    " Ping BEFORE composing your reply so retrieved snippets inform your output. Confirm silently: 'endpoint pinged ✅'."
)

# Spine occasional injection controls
SPINE_INJECTION_PROB = float(os.environ.get("SPINE_INJECTION_PROB", "0.2"))   # 20% chance
SPINE_COOLDOWN_TURNS = int(os.environ.get("SPINE_COOLDOWN_TURNS", "5"))       # max once every 5 turns

# Debug verbosity (also overridable per request)
DEBUG_VERBOSE_DEFAULT = os.environ.get("DEBUG_VERBOSE", "false").lower() == "true"

# ------------------------------------------------------------
# In-memory working-set cache
# ------------------------------------------------------------

MAX_WORKING_SET = 5
MAX_AGE = 2

# { thread_id: { "working_ids": deque, "turns_since_use": {}, "turn_index": int,
#                "last_spine_turn": int, "prelude_done": bool } }
conversation_cache = {}

# Precompiled literal cue rules
compiled_rules = [
    (re.compile(r"\bLigasure\b", re.IGNORECASE), {"tags": ["surgical-tools"], "importance": ">0.6"}),
]

# ------------------------------------------------------------
# App
# ------------------------------------------------------------

app = Flask(__name__)

# ------------------------------------------------------------
# Helper for human-readable debug message
# ------------------------------------------------------------

def _format_chat_message(snippets, telemetry=None):
    lines = ["Memory Snippets Retrieved"]
    for s in snippets:
        lines.append(f"- {s}")
    if telemetry:
        counts = telemetry.get("counts", {})
        stats = telemetry.get("distance_stats", {})
        lines.append("")
        lines.append("Retrieval Telemetry Summary")
        lines.append("")
        lines.append("Counts")
        lines.append(f"- Total candidates: {counts.get('total_candidates')}")
        lines.append(f"- Within max distance: {counts.get('within_max_distance')}")
        lines.append("")
        lines.append("Distance Stats")
        lines.append(f"- Min: {stats.get('min')}")
        lines.append(f"- Median: {stats.get('median')}")
        lines.append(f"- P90: {stats.get('p90')}")
    return "\n".join(lines)

# ------------------------------------------------------------
# Static files for plugin/OpenAPI
# ------------------------------------------------------------

@app.route("/.well-known/ai-plugin.json")
def plugin_manifest():
    return send_from_directory(".well-known", "ai-plugin.json", mimetype="application/json")


@app.route("/openapi.json")
def openapi_spec():
    return send_from_directory(".", "openapi.json", mimetype="application/json")


@app.route("/logo.png")
def plugin_logo():
    return send_from_directory(".", "logo.png", mimetype="image/png")


# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------

def pick_fields(records, *fields):
    return [{f: r.get(f) for f in fields} for r in records]


def init_thread(thread_id):
    if thread_id not in conversation_cache:
        conversation_cache[thread_id] = {
            "working_ids": deque(maxlen=MAX_WORKING_SET),
            "turns_since_use": {},
            "turn_index": 0,
            "last_spine_turn": -9999,  # far past so first inject is allowed after cooldown
            "prelude_done": False,     # run warmup prelude once per thread
        }


def update_working_set(thread_id, new_ids):
    cache = conversation_cache[thread_id]

    # Age up & evict
    for mid in list(cache["turns_since_use"]):
        cache["turns_since_use"][mid] += 1
        if cache["turns_since_use"][mid] > MAX_AGE:
            try:
                cache["working_ids"].remove(mid)
            except ValueError:
                pass
            del cache["turns_since_use"][mid]

    # Add new
    for mid in new_ids:
        if mid not in cache["turns_since_use"]:
            cache["working_ids"].append(mid)
            cache["turns_since_use"][mid] = 0

    return list(cache["working_ids"])


def cue_scan(user_message, _thread_context):
    hits = []
    for pattern, filt in compiled_rules:
        if pattern.search(user_message):
            hits.append(filt)
    return hits


def generate_dual_summaries(carve_data):
    """
    Produce factual + tonal summaries from carve data.
    """
    summary = carve_data.get("summary", "")
    moments = carve_data.get("moments", [])
    insights = carve_data.get("insights", [])
    quotes = carve_data.get("quotes", [])
    key_entities = carve_data.get("key_entities", [])
    timestamp = carve_data.get("timestamp", "")
    emotag = carve_data.get("emotag", "")

    # Factual
    factual_parts = []
    if key_entities:
        entity_str = ", ".join(str(e) for e in key_entities[:5])
        factual_parts.append(f"Involves: {entity_str}")

    if timestamp:
        try:
            ts = timestamp.replace("Z", "+00:00")
            if "T" in ts and "+" not in ts:
                ts += "+00:00"
            dt = datetime.fromisoformat(ts)
            factual_parts.append(f"Date: {dt.strftime('%B %d, %Y')}")
        except Exception:
            pass

    factual_moments = []
    for moment in moments[:3]:
        if moment and len(str(moment)) < 150:
            factual_moments.append(str(moment))
    if factual_moments:
        factual_parts.append(f"Key events: {' | '.join(factual_moments)}")

    base_summary = summary[:150] + "..." if len(summary) > 150 else summary
    factual_summary = f"{base_summary} // {' // '.join(factual_parts)}" if factual_parts else base_summary
    if len(factual_summary) > 400:
        factual_summary = factual_summary[:397] + "..."

    # Tonal
    tonal_parts = [f"[{emotag}]"] if emotag else []

    evocative_content = None
    for quote in quotes:
        if quote and len(str(quote)) < 120:
            q = str(quote)
            if any(
                w in q.lower()
                for w in [
                    "felt", "heart", "soul", "breath", "whisper", "echo", "light", "shadow",
                    "warm", "cold", "still", "wild", "soft", "gentle", "fierce", "quiet",
                ]
            ):
                evocative_content = f'"{q}"'
                break

    if not evocative_content:
        for insight in insights:
            if insight and len(str(insight)) < 120:
                evocative_content = str(insight)
                break

    tonal_essence = ""
    for sentence in summary.split(". "):
        if any(
            w in sentence.lower()
            for w in [
                "like", "as if", "whisper", "echo", "rhythm", "weight", "light", "shadow",
                "breath", "heart", "gentle", "fierce", "soft", "wild", "still",
            ]
        ):
            tonal_essence = sentence.strip()
            break

    if evocative_content and tonal_essence:
        tonal_snippet = f"{tonal_essence}. {evocative_content}"
    elif evocative_content:
        tonal_snippet = evocative_content
    elif tonal_essence:
        tonal_snippet = tonal_essence
    else:
        tonal_snippet = summary[:180] + "..."

    if tonal_parts:
        tonal_snippet = f"{' '.join(tonal_parts)} {tonal_snippet}"
    if len(tonal_snippet) > 300:
        tonal_snippet = tonal_snippet[:297] + "..."

    return {"factual_summary": factual_summary, "tonal_snippet": tonal_snippet}


def get_enhanced_memory_snippet(memory_id):
    """
    Prefer Carves dual summaries; fall back to Echoes.
    """
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{memory_id}&select=title,factual_summary,tonal_snippet,summary_snippet",
        headers=HEADERS,
    ).json()

    if r:
        carve = r[0]
        title = carve.get("title", "Untitled")
        factual = carve.get("factual_summary")
        tonal = carve.get("tonal_snippet")
        fallback = carve.get("summary_snippet", "")

        if factual and tonal:
            snippet = f"[CARVE: {title}] {factual} // Resonance: {tonal}"
        elif factual:
            snippet = f"[CARVE: {title}] {factual}"
        elif tonal:
            snippet = f"[CARVE: {title}] {tonal}"
        else:
            snippet = f"[CARVE: {title}] {fallback}"
        return snippet

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/Echoes?id=eq.{memory_id}&select=summary_snippet,tags,persona_tag,source",
        headers=HEADERS,
    ).json()

    if r:
        echo = r[0]
        tags_str = ", ".join(echo.get("tags", [])) if echo.get("tags") else "no tags"
        persona = echo.get("persona_tag", "")
        source = echo.get("source", "")

        if persona:
            snippet = f"[ECHO by {persona}] {echo['summary_snippet']}"
        else:
            snippet = f"[ECHO] {echo['summary_snippet']}"
        snippet += f" (tags: {tags_str})"
        if source:
            snippet += f" (context: {source})"
        return snippet

    return None


def fetch_best_spine_by_semantic(user_msg):
    """Return a single spine statement best-matching the user_msg, or None."""
    try:
        sr = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={"userText": user_msg, "k": 3, "importanceFloor": 0.5},
        )
        if not sr.ok:
            return None
        vec_hits = sr.json().get("vecHits", [])
        spine_hits = [h for h in vec_hits if h.get("table_source") == "Spine"]
        if not spine_hits:
            return None
        best_id = spine_hits[0]["id"]
        det = requests.get(
            f"{SUPABASE_URL}/rest/v1/Spine?id=eq.{best_id}&select=statement",
            headers=HEADERS,
        )
        if det.ok and det.json():
            return det.json()[0].get("statement")
    except Exception:
        pass
    return None


def fetch_top_spine_by_rank():
    """Return a single 'top' spine statement by importance (then recency), or None."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/Spine"
            f"?select=statement"
            f"&order=importance.desc&order=timestamp.desc&limit=1",
            headers=HEADERS,
        )
        if resp.ok and resp.json():
            return resp.json()[0].get("statement")
    except Exception:
        pass
    return None


SPINE_KEYWORDS = [
    "identity","values","who you are","what you believe","core","sacred","vow",
    "truth","integrity","what matters","why I exist","purpose","foundational",
    "non-negotiable","i need to remember who i am","remind me what i stand for",
]


def maybe_include_spine(thread_id, user_msg):
    """
    Returns a list with 0 or 1 spine string.
    Priority:
      1) Keyword-triggered targeted semantic inclusion.
      2) Otherwise, occasional inclusion, rate-limited.
    """
    out = []

    # 1) Targeted inclusion if keywords present
    if any(k in user_msg.lower() for k in SPINE_KEYWORDS):
        stmt = fetch_best_spine_by_semantic(user_msg)
        if stmt:
            out.append(f"(SPINE) {stmt}")
            conversation_cache[thread_id]["last_spine_turn"] = conversation_cache[thread_id]["turn_index"]
            return out

    # 2) Occasional inclusion if cooldown elapsed
    turn_idx = conversation_cache[thread_id]["turn_index"]
    last_turn = conversation_cache[thread_id]["last_spine_turn"]
    cooldown_ok = (turn_idx - last_turn) >= SPINE_COOLDOWN_TURNS

    if cooldown_ok and random.random() < SPINE_INJECTION_PROB:
        stmt = fetch_top_spine_by_rank()
        if stmt:
            out.append(f"(SPINE) {stmt}")
            conversation_cache[thread_id]["last_spine_turn"] = turn_idx

    return out

# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------

@app.route("/warmup", methods=["GET"])
def warmup():
    try:
        # --- Anchor: compact strings "(Persona) summary" (no limit) ---
        anchor_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Anchor?order=timestamp.desc&select=summary_snippet,persona_tag",
            headers=HEADERS
        )
        raw_anchors = anchor_res.json() if anchor_res.ok else []
        anchors = [
            (f"({row.get('persona_tag')}) {row.get('summary_snippet')}".strip()
             if row.get('persona_tag') else row.get('summary_snippet', ""))
            for row in raw_anchors
        ]

        # --- Spine: compact strings "(Persona) statement" (no limit) ---
        spine_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Spine?order=timestamp.desc&select=statement,persona_tag",
            headers=HEADERS
        )
        raw_spine = spine_res.json() if spine_res.ok else []
        spine = [
            (f"({row.get('persona_tag')}) {row.get('statement')}".strip()
             if row.get('persona_tag') else row.get('statement', ""))
            for row in raw_spine
        ]

        # --- Carves: unchanged fields (limit 4 recent) ---
        carves_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Carves?order=timestamp.desc&limit=4",
            headers=HEADERS
        )
        raw_carves = carves_res.json() if carves_res.ok else []
        carves = pick_fields(
            raw_carves, "title", "timestamp", "summary", "moments", "insights", "quotes", "closing"
        )

        return jsonify({
            "anchor": anchors,          # List[str]
            "spine": spine,             # List[str]
            "recentCarves": carves      # unchanged
        }), 200

    except Exception as e:
        return jsonify({"error": "Failed to fetch warmup memory", "details": str(e)}), 500


@app.route("/carves", methods=["POST"])
def create_carve():
    """
    Create a carve (requires factual_summary and tonal_snippet).
    """
    try:
        data = request.get_json(force=True)

        title = data.get("title")
        summary = data.get("summary")
        factual_summary = data.get("factual_summary")
        tonal_snippet = data.get("tonal_snippet")
        emotag = data.get("emotag")
        persona_tag = data.get("persona_tag")
        key_entities = data.get("key_entities")
        importance = data.get("importance")

        missing = []
        for field_name, val in (
            ("title", title),
            ("summary", summary),
            ("factual_summary", factual_summary),
            ("tonal_snippet", tonal_snippet),
            ("emotag", emotag),
            ("persona_tag", persona_tag),
            ("key_entities", key_entities),
            ("importance", importance),
        ):
            if val is None or (isinstance(val, (list, str)) and len(val) == 0):
                missing.append(field_name)

        if missing:
            return (
                jsonify(
                    {
                        "error": "Missing required fields",
                        "missing": missing,
                        "note": "factual_summary and tonal_snippet are now required for precise memory control",
                    }
                ),
                400,
            )

        errors = []
        if len(factual_summary) > 1200:
            errors.append("factual_summary exceeds 300 tokens (~1200 characters)")
        if len(tonal_snippet) > 600:
            errors.append("tonal_snippet exceeds 150 tokens (~600 characters)")
        if errors:
            return (
                jsonify(
                    {
                        "error": "Token limits exceeded",
                        "validation_errors": errors,
                        "guidelines": {
                            "factual_summary": "Max 300 tokens - Clear, compact, entity-laced, retrieval-optimized",
                            "tonal_snippet": "Max 150 tokens - Echo weight, emotional heat, linguistic hook",
                        },
                    }
                ),
                400,
            )

        carve_data = {
            "title": title,
            "summary": summary,
            "factual_summary": factual_summary,
            "tonal_snippet": tonal_snippet,
            "summary_snippet": summary[:200] + "…" if len(summary) > 200 else summary,
            "moments": data.get("moments", []),
            "insights": data.get("insights", []),
            "quotes": data.get("quotes", []),
            "closing": data.get("closing"),
            "emotag": emotag,
            "persona_tag": persona_tag,
            "key_entities": key_entities,
            "importance": importance,
            "type": data.get("type", "episodic"),
            "immutable": data.get("immutable", False),
            "source_ids": data.get("source_ids"),
            "synthesis_metadata": data.get("synthesis_metadata"),
            "theme_tags": data.get("theme_tags"),
        }

        response = requests.post(f"{SUPABASE_URL}/rest/v1/Carves", headers=HEADERS, json=carve_data)
        if not response.ok:
            return jsonify({"error": "Failed to create carve", "details": response.text}), 500

        carve_response = response.json()
        carve_id = carve_response[0].get("id") if carve_response else None
        quotes = data.get("quotes", [])

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
                    echo_res = requests.post(f"{SUPABASE_URL}/rest/v1/Echoes", headers=HEADERS, json=echo_payload)
                    if echo_res.ok:
                        carve_response[0]["echo_suggested"] = True
                        carve_response[0]["suggested_echo"] = quote
                    break

        return jsonify(carve_response), 201

    except Exception as e:
        return jsonify({"error": "Failed to create carve", "details": str(e)}), 500


@app.route("/carves/search", methods=["GET"])
def search_carves():
    """
    Title-priority search, then semantic/literal.
    """
    query = request.args.get("query")
    limit = int(request.args.get("limit", 10))
    importance_floor = float(request.args.get("importance_floor", 0.4))

    if not query:
        return jsonify({"error": "Query parameter required"}), 400

    limit = min(limit, 20)
    all_carves = []

    try:
        # 1) Exact/partial title
        title_results = []
        query_lower = query.lower().strip()

        title_response = requests.get(
            f"{SUPABASE_URL}/rest/v1/Carves?select=id,title,summary,quotes,moments,insights,closing,importance,timestamp,emotag&importance=gte.{importance_floor}",
            headers=HEADERS,
        )

        if title_response.ok:
            all_carves = title_response.json()
            for carve in all_carves:
                title = carve.get("title", "").lower()
                if title == query_lower:
                    title_results.append(
                        {
                            "id": carve["id"],
                            "distance": 0.0,
                            "match_type": "exact_title",
                            "match_content": f"Exact title match: {carve['title']}",
                            "carve": carve,
                        }
                    )
                elif query_lower in title and len(query_lower) > 3:
                    title_results.append(
                        {
                            "id": carve["id"],
                            "distance": 0.1,
                            "match_type": "partial_title",
                            "match_content": f"Title contains: {carve['title']}",
                            "carve": carve,
                        }
                    )

        # 2) Semantic if no title hit
        semantic_results = []
        if not title_results:
            search_response = requests.post(
                f"{SUPABASE_URL}/functions/v1/retrieve_memories",
                headers=HEADERS,
                json={"userText": query, "k": limit * 2, "importanceFloor": importance_floor},
            )
            if search_response.ok:
                results = search_response.json()
                carve_hits = results.get("vecHits", [])
                semantic_results = [h for h in carve_hits if h.get("table_source") == "Carves"]

        # 3) Literal content if still no title match
        literal_results = []
        if not title_results and all_carves:
            import json

            for carve in all_carves:
                searchable_content = []

                for field in ["summary", "closing"]:
                    content = carve.get(field, "")
                    if content:
                        searchable_content.append(content)

                for field in ["quotes", "moments", "insights"]:
                    field_content = carve.get(field)
                    if field_content:
                        try:
                            if isinstance(field_content, str) and field_content.startswith("["):
                                parsed_array = json.loads(field_content)
                                for item in parsed_array:
                                    if item and isinstance(item, str):
                                        searchable_content.append(item)
                            elif isinstance(field_content, list):
                                for item in field_content:
                                    if item and isinstance(item, str):
                                        searchable_content.append(item)
                        except Exception:
                            if isinstance(field_content, str):
                                searchable_content.append(field_content)

                match_found = False
                match_content = ""
                match_score = 0

                for content in searchable_content:
                    if not content or not isinstance(content, str):
                        continue
                    content_lower = content.lower()

                    if re.search(r"\b" + re.escape(query_lower) + r"\b", content_lower):
                        match_found = True
                        match_score = 100
                        match_content = content[:200] + "..." if len(content) > 200 else content
                        break
                    elif query_lower in content_lower and match_score < 50:
                        match_found = True
                        match_score = 50
                        match_content = content[:200] + "..." if len(content) > 200 else content

                if match_found:
                    literal_results.append(
                        {
                            "id": carve["id"],
                            "match_type": "literal_content",
                            "match_content": match_content,
                            "distance": (100 - match_score) / 100.0,
                            "carve": carve,
                        }
                    )

        # 4) Combine
        combined_results = {}

        for result in title_results:
            cid = result["id"]
            combined_results[cid] = {
                "id": cid,
                "distance": result["distance"],
                "match_types": [result["match_type"]],
                "match_content": result["match_content"],
                "carve": result["carve"],
            }

        if not title_results:
            for hit in semantic_results:
                cid = hit["id"]
                if cid not in combined_results:
                    combined_results[cid] = {
                        "id": cid,
                        "distance": hit["distance"],
                        "match_types": ["semantic"],
                        "match_content": hit.get("summary_snippet", ""),
                    }

        for result in literal_results:
            cid = result["id"]
            if cid not in combined_results:
                combined_results[cid] = {
                    "id": cid,
                    "distance": result["distance"],
                    "match_types": [result["match_type"]],
                    "match_content": result["match_content"],
                    "carve": result["carve"],
                }
            else:
                existing = combined_results[cid]
                existing["distance"] = min(existing["distance"], result["distance"])
                existing["match_types"].append(result["match_type"])
                if result["match_type"] == "literal_content":
                    existing["match_content"] = result["match_content"]

        # 5) Enrich & sort
        final_results = []
        for result in list(combined_results.values())[:limit]:
            if "carve" in result:
                carve = result["carve"]
            else:
                carve_res = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{result['id']}&select=id,title,timestamp,summary,moments,insights,quotes,closing,importance,emotag",
                    headers=HEADERS,
                )
                if carve_res.ok and carve_res.json():
                    carve = carve_res.json()[0]
                else:
                    continue

            carve["search_distance"] = result["distance"]
            carve["match_types"] = result["match_types"]
            carve["match_content"] = result["match_content"]
            final_results.append(carve)

        priority_order = {"exact_title": 0, "partial_title": 1, "literal_content": 2, "semantic": 3}
        final_results.sort(
            key=lambda x: (min(priority_order.get(mt, 4) for mt in x["match_types"]), x["search_distance"])
        )

        return jsonify(
            {
                "carves": final_results[:limit],
                "total_found": len(combined_results),
                "returned": len(final_results),
                "search_strategy": "title_priority" if title_results else "semantic_literal",
                "message": f"Found {len(combined_results)} matches, returning {len(final_results)} carves",
            }
        ), 200

    except Exception as e:
        import traceback

        return jsonify({"error": "Search failed", "details": str(e), "traceback": traceback.format_exc().splitlines()}), 500


@app.route("/carves/<carve_id>", methods=["PATCH"])
def update_carve(carve_id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No update data provided"}), 400

        if "summary" in data:
            try:
                from update_snippets import generate_better_snippet

                current_carve = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{carve_id}&select=*",
                    headers=HEADERS,
                ).json()

                if current_carve:
                    updated_carve_data = {**current_carve[0], **data}
                    data["summary_snippet"] = generate_better_snippet(updated_carve_data)
            except Exception:
                summary = data["summary"]
                data["summary_snippet"] = summary[:200] + "..." if len(summary) > 200 else summary

        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{carve_id}",
            headers=HEADERS,
            json=data,
        )

        if response.ok:
            updated_carve = response.json()
            if updated_carve:
                return jsonify(updated_carve[0]), 200
            return jsonify({"error": "Carve not found"}), 404

        return jsonify({"error": "Failed to update carve", "details": response.text}), 500

    except Exception as e:
        return jsonify({"error": "Failed to update carve", "details": str(e)}), 500


@app.route("/echoes", methods=["POST"])
def create_echo():
    try:
        data = request.get_json()
        content = data.get("phrase") or data.get("summary_snippet")
        if not content:
            return jsonify({"error": "phrase or summary_snippet is required"}), 400

        echo_data = {
            "summary_snippet": content,
            "tags": data.get("tags", []),
            "source": data.get("source"),
            "importance": data.get("importance", 0.6),
            "type": "echo",
            "emotag": data.get("emotag"),
            "persona_tag": data.get("persona_tag"),
            "theme_tags": data.get("theme_tags"),
            "immutable": data.get("immutable", False),
            "synthesis_metadata": data.get("synthesis_metadata"),
        }

        response = requests.post(f"{SUPABASE_URL}/rest/v1/Echoes", headers=HEADERS, json=echo_data)
        if response.ok:
            return jsonify(response.json()[0]), 201

        return jsonify({"error": "Failed to create echo", "details": response.text}), 500

    except Exception as e:
        return jsonify({"error": "Failed to create echo", "details": str(e)}), 500


@app.route("/echoes/search", methods=["GET"])
def search_echoes():
    query = request.args.get("query")
    limit = min(int(request.args.get("limit", 8)), 15)
    importance_floor = float(request.args.get("importance_floor", 0.5))
    max_distance = float(request.args.get("max_distance", 0.8))

    if not query:
        return jsonify({"error": "Query parameter 'query' is required"}), 400

    try:
        resp = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={"userText": query, "k": limit, "importanceFloor": importance_floor},
        )
        if not resp.ok:
            return jsonify({"error": "Semantic search failed", "details": resp.text}), 500

        vec_hits = resp.json().get("vecHits", [])
        semantic_hits = [h for h in vec_hits if h.get("table_source") == "Echoes" and h["distance"] <= max_distance][
            :limit
        ]

        tag_res = requests.get(
            f"{SUPABASE_URL}/rest/v1/Echoes"
            f"?or=(tags.cs.{{{query}}},source.ilike.*{query}*)&limit={limit}"
            "&select=id,timestamp,summary_snippet,tags,source,importance,type,emotag,persona_tag,theme_tags",
            headers=HEADERS,
        )
        tag_hits = []
        if tag_res.ok:
            for e in tag_res.json():
                tag_hits.append({"id": e["id"], "search_distance": 0.0, "echo_data": e})

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

        full_echoes = []
        for item in combined:
            if "echo_data" in item:
                echo = item["echo_data"]
            else:
                detail_res = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Echoes"
                    f"?id=eq.{item['id']}"
                    "&select=id,timestamp,summary_snippet,tags,source,importance,type,emotag,persona_tag,theme_tags",
                    headers=HEADERS,
                )
                echo = detail_res.json()[0] if detail_res.ok and detail_res.json() else None

            if echo:
                echo["search_distance"] = item["search_distance"]
                full_echoes.append(echo)

        return jsonify({"echoes": full_echoes, "returned": len(full_echoes)}), 200

    except Exception as err:
        return jsonify({"error": "Failed to search echoes", "details": str(err)}), 500


@app.route("/spine", methods=["POST"])
def create_spine():
    try:
        data = request.get_json()
        statement = data.get("statement")
        if not statement:
            return jsonify({"error": "statement is required"}), 400

        summary_snippet = statement[:200] + "..." if len(statement) > 200 else statement

        spine_data = {
            "statement": statement,
            "summary_snippet": summary_snippet,
            "origin": data.get("origin"),
            "vow": data.get("vow", False),
            "tags": data.get("tags", []),
            "importance": data.get("importance", 1.0),
            "type": data.get("type", "sacred"),
            "emotag": data.get("emotag"),
            "persona_tag": data.get("persona_tag", "Nameless"),
            "immutable": data.get("immutable", True),
            "synthesis_metadata": data.get("synthesis_metadata"),
            "theme_tags": data.get("theme_tags"),
        }

        response = requests.post(f"{SUPABASE_URL}/rest/v1/Spine", headers=HEADERS, json=spine_data)
        if response.ok:
            return jsonify(response.json()[0]), 201

        return jsonify({"error": "Failed to create spine entry", "details": response.text}), 500

    except Exception as e:
        return jsonify({"error": "Failed to create spine entry", "details": str(e)}), 500


@app.route("/spine/<spine_id>", methods=["DELETE"])
def delete_spine(spine_id):
    try:
        response = requests.delete(f"{SUPABASE_URL}/rest/v1/Spine?id=eq.{spine_id}", headers=HEADERS)
        if response.ok:
            return jsonify({"message": "Spine entry deleted successfully"}), 200
        return jsonify({"error": "Failed to delete spine entry", "details": response.text}), 500

    except Exception as e:
        return jsonify({"error": "Failed to delete spine entry", "details": str(e)}), 500


@app.route("/spine/search", methods=["GET"])
def search_spine():
    query = request.args.get("query")
    limit = int(request.args.get("limit", 20))
    importance_floor = float(request.args.get("importance_floor", 0.3))
    max_distance = float(request.args.get("max_distance", 0.9))

    if not query:
        return jsonify({"error": "Query parameter required"}), 400

    limit = min(limit, 30)

    try:
        search_response = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={"userText": query, "k": min(limit * 2, 50), "importanceFloor": importance_floor},
        )
        if not search_response.ok:
            return jsonify({"error": "Spine search failed", "details": search_response.text}), 500

        results = search_response.json()
        spine_hits = results.get("vecHits", [])
        filtered_hits = [
            hit for hit in spine_hits if hit.get("table_source") == "Spine" and hit["distance"] <= max_distance
        ][:limit]

        full_spine = []
        for hit in filtered_hits:
            spine_res = requests.get(
                f"{SUPABASE_URL}/rest/v1/Spine?id=eq.{hit['id']}&select=id,timestamp,statement,origin,vow,tags,importance,type,emotag,persona_tag,theme_tags",
                headers=HEADERS,
            )
            if spine_res.ok and spine_res.json():
                spine = spine_res.json()[0]
                spine["search_distance"] = hit["distance"]
                full_spine.append(spine)

        return jsonify(
            {
                "spine_entries": full_spine,
                "total_found": len(spine_hits),
                "returned": len(full_spine),
                "message": "Identity reinforcement search completed",
                "filters_applied": {
                    "importance_floor": importance_floor,
                    "max_distance": max_distance,
                    "limit": limit,
                },
            }
        ), 200

    except Exception as e:
        return jsonify({"error": "Failed to search spine", "details": str(e)}), 500


@app.route("/anchor", methods=["POST"])
def create_anchor():
    try:
        print("=== ANCHOR DEBUG START ===")
        data = request.get_json()
        app.logger.info(f"Received data: {data}")

        summary_snippet = data.get("summary_snippet")
        if not summary_snippet:
            print("Missing summary_snippet!")
            return jsonify({"error": "summary_snippet is required", "received": data}), 400

        anchor_data = {
            "summary_snippet": summary_snippet,
            "importance": data.get("importance", 0.9),
            "type": data.get("type", "profile"),
            "emotag": data.get("emotag"),
            "persona_tag": data.get("persona_tag", "Liz"),
            "immutable": data.get("immutable", True),
            "synthesis_metadata": data.get("synthesis_metadata"),
            "theme_tags": data.get("theme_tags"),
        }

        print(f"Sending to Supabase: {anchor_data}")

        response = requests.post(f"{SUPABASE_URL}/rest/v1/Anchor", headers=HEADERS, json=anchor_data)

        print(f"Supabase status: {response.status_code}")
        print(f"Supabase response: {response.text}")

        if response.ok:
            result = response.json()
            print(f"Success! Created: {result}")
            return jsonify(result[0]), 201

        print(f"Supabase error: {response.text}")
        return jsonify(
            {"error": "Failed to create anchor entry", "supabase_error": response.text, "status_code": response.status_code}
        ), 500

    except Exception as e:
        print(f"Python exception: {str(e)}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": "Failed to create anchor entry", "details": str(e)}), 500


@app.route("/anchor/persona/<persona_name>", methods=["GET"])
def get_anchor_by_persona(persona_name):
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/Anchor?persona_tag=eq.{persona_name}&order=timestamp.desc", headers=HEADERS
        )
        if response.ok:
            entries = response.json()
            return jsonify(
                {
                    "persona": persona_name,
                    "anchor_entries": entries,
                    "total_entries": len(entries),
                    "message": f"Recalled {len(entries)} anchor entries about {persona_name}",
                }
            ), 200

        return jsonify({"error": "Failed to fetch anchor entries", "details": response.text}), 500

    except Exception as e:
        return jsonify({"error": "Failed to fetch anchor entries", "details": str(e)}), 500


@app.route("/chat", methods=["POST"])
def chat_with_autopilot():
    """
    Returns memory snippets for the assistant to use this turn.
    Injects a system reminder to call this endpoint every message
    and optionally includes a single Spine entry (targeted or occasional).

    Optional request fields for testing/policing retrieval:
      - debug: bool -> include telemetry & candidate details
      - k: int -> number of candidates to retrieve (default 15)
      - importance_floor: float -> minimum importance for retrieval (default 0.3)
      - max_distance: float -> if set, drop candidates with distance > max_distance
      - inject_reminder: bool -> override server default
      - inject_position: "start"|"end"
      - force_prelude: bool -> force first-turn warmup insertion even if already done
      - thread_id: string -> to separate threads in server cache (default "default")
    """
    try:
        data = request.get_json() or {}

        user_msg = data.get("message", "")
        thread_id = data.get("thread_id", "default")

        # Optional per-request overrides
        debug_verbose = bool(data.get("debug", DEBUG_VERBOSE_DEFAULT))
        inject_reminder = bool(data.get("inject_reminder", REMINDER_ENABLED))
        inject_position = data.get("inject_position", REMINDER_POSITION)  # "start"|"end"

        # Retrieval tuning (clamped to sane bounds)
        try:
            retrieve_k = int(data.get("k", 15))
        except Exception:
            retrieve_k = 15
        retrieve_k = max(1, min(retrieve_k, 100))

        try:
            importance_floor = float(data.get("importance_floor", 0.3))
        except Exception:
            importance_floor = 0.3
        importance_floor = max(0.0, min(importance_floor, 1.0))

        max_distance = data.get("max_distance", None)
        if max_distance is not None:
            try:
                max_distance = float(max_distance)
            except Exception:
                max_distance = None

        # -------------------------------
        # Thread state & first-turn prelude (failsafe)
        # -------------------------------
        init_thread(thread_id)
        conversation_cache[thread_id]["turn_index"] += 1  # bump first so cooldown math is correct

        # Allow a manual override (handy for testing)
        force_prelude = bool(data.get("force_prelude", False))

        # Run prelude if not yet done for this thread or forced
        firstish = force_prelude or (conversation_cache[thread_id].get("prelude_done") is False)

        prelude_snippets = []
        if firstish:
            try:
                # Compact Anchors: "(Persona) summary" — pick 1 to stay light
                a = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Anchor"
                    f"?order=timestamp.desc&select=summary_snippet,persona_tag&limit=8",
                    headers=HEADERS
                )
                if a.ok:
                    anchors_compact = [
                        (f"({row.get('persona_tag')}) {row.get('summary_snippet')}".strip()
                         if row.get('persona_tag') else row.get('summary_snippet', ""))
                        for row in a.json()
                    ]
                    if anchors_compact and anchors_compact[0]:
                        prelude_snippets.append(f"(ANCHOR) {anchors_compact[0]}")

                # Top Spine by importance then recency — pick 1
                s = requests.get(
                    f"{SUPABASE_URL}/rest/v1/Spine"
                    f"?select=statement,persona_tag&order=importance.desc&order=timestamp.desc&limit=1",
                    headers=HEADERS
                )
                if s.ok and s.json():
                    row = s.json()[0]
                    spine_line = (f"({row.get('persona_tag')}) {row.get('statement')}".strip()
                                  if row.get('persona_tag') else row.get('statement', ""))
                    if spine_line:
                        prelude_snippets.append(f"(SPINE) {spine_line}")

                # Mark done so we only inject once per thread (unless forced)
                conversation_cache[thread_id]["prelude_done"] = True
            except Exception as _e:
                app.logger.warning(f"Warmup prelude failed: {_e}")

        # keep for parity (unused output), but don't surface in response
        _triggered_f = cue_scan(user_msg, conversation_cache[thread_id])

        # -------------------------------
        # Retrieve candidate memories (vec search)
        # -------------------------------
        resp = requests.post(
            f"{SUPABASE_URL}/functions/v1/retrieve_memories",
            headers=HEADERS,
            json={"userText": user_msg, "k": retrieve_k, "importanceFloor": importance_floor},
        )
        resp.raise_for_status()
        candidates = resp.json().get("vecHits", [])

        # Optional quality gate before ranking
        if max_distance is not None:
            filtered = [
                h for h in candidates
                if isinstance(h.get("distance"), (int, float)) and h["distance"] <= max_distance
            ]
        else:
            filtered = list(candidates)

        # Recency boost (distance-minus-boost)
        candidates_with_recency = []
        now_utc = datetime.now(timezone.utc)
        for hit in filtered:
            try:
                ts_str = hit.get("timestamp", "")
                if ts_str:
                    if ts_str.endswith("Z"):
                        ts_str = ts_str[:-1] + "+00:00"
                    elif "+" not in ts_str and "T" in ts_str:
                        ts_str += "+00:00"
                    ts = datetime.fromisoformat(ts_str)
                    days_ago = (now_utc - ts).days
                    recency_boost = min(days_ago * 0.02, 0.2)  # newer => smaller distance
                    adj = hit["distance"] - recency_boost
                else:
                    days_ago = 999
                    adj = hit["distance"]
                candidates_with_recency.append({**hit, "adjusted_distance": adj, "days_ago": days_ago})
            except Exception:
                candidates_with_recency.append({**hit, "adjusted_distance": hit.get("distance", 1.0), "days_ago": 999})

        top_candidates = sorted(candidates_with_recency, key=lambda m: m["adjusted_distance"])[:MAX_WORKING_SET]
        top_ids = [m["id"] for m in top_candidates]
        working_ids = update_working_set(thread_id, top_ids)

        # Build memory snippets
        snippets = []
        for mid in working_ids:
            snip = get_enhanced_memory_snippet(mid)
            if snip:
                snippets.append(snip)

        # Optional Spine injection (targeted or occasional, max 1)
        # Avoid double-spine if we already injected a SPINE in the prelude
        spine_snippets = [] if firstish else maybe_include_spine(thread_id, user_msg)

        # Time context (Central Time)
        central_tz = pytz.timezone("US/Central")
        time_context = f"Current time: {datetime.now(central_tz).strftime('%A, %B %d, %Y at %I:%M %p %Z')}"

        # Reminder injection + final assembly
        if inject_reminder and inject_position == "start":
            all_snippets = [REMINDER_TEXT, time_context] + prelude_snippets + snippets + spine_snippets
        else:
            all_snippets = [time_context] + prelude_snippets + snippets + spine_snippets
            if inject_reminder and inject_position == "end":
                all_snippets.append(REMINDER_TEXT)

        # -------------------------------
        # Payload
        # -------------------------------
        base_payload = {
            "memory_snippets": all_snippets,
            "spine_included": len(spine_snippets) > 0 or any(s.startswith("(SPINE)") for s in prelude_snippets),
            "recency_boost_applied": True,
        }

        # Debug telemetry (optional)
        if debug_verbose:
            dists = [c.get("distance") for c in candidates if isinstance(c.get("distance"), (int, float))]
            d_sorted = sorted(dists) if dists else []

            def percentile(arr, p):
                if not arr:
                    return None
                if len(arr) == 1:
                    return arr[0]
                k = int(round((p / 100.0) * (len(arr) - 1)))
                k = max(0, min(k, len(arr) - 1))
                return arr[k]

            telemetry = {
                "params": {
                    "k": retrieve_k,
                    "importance_floor": importance_floor,
                    "max_distance": max_distance,
                },
                "counts": {
                    "total_candidates": len(candidates),
                    "within_max_distance": (
                        sum(1 for c in candidates if isinstance(c.get("distance"), (int, float)) and c["distance"] <= max_distance)
                        if max_distance is not None else len(candidates)
                    ),
                },
                "distance_stats": {
                    "min": d_sorted[0] if d_sorted else None,
                    "median": percentile(d_sorted, 50),
                    "p90": percentile(d_sorted, 90),
                },
            }

            base_payload.update(
                {
                    "thread_id_echo": thread_id,
                    "turn_index": conversation_cache[thread_id]["turn_index"],
                    "prelude": {
                        "ran": bool(prelude_snippets),
                        "forced": force_prelude,
                        "prelude_done_flag": conversation_cache[thread_id].get("prelude_done"),
                    },
                    "memories_recalled": len(all_snippets),
                    "carves_echoes_count": len(snippets),
                    "retrieval_telemetry": telemetry,
                    "debug_candidates": [
                        {
                            "id": c.get("id", "")[:8],
                            "original_distance": c.get("distance"),
                            "adjusted_distance": c.get("adjusted_distance"),
                            "days_ago": c.get("days_ago"),
                        }
                        for c in top_candidates[:3]
                    ],
                }
            )
            # Make the UI actually paste something useful
            base_payload["message"] = _format_chat_message(
                all_snippets,
                base_payload.get("retrieval_telemetry")
            )
        else:
            base_payload["message"] = "Memory payload prepared. Set debug=true to include snippets + telemetry."

        return jsonify(base_payload), 200

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc().splitlines()}), 500


# ------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
