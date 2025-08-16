import os
import re
import uuid
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

# ---- HTTP timeouts (connector-friendly) ----
CONNECT_TO = float(os.environ.get("CONNECT_TIMEOUT_S", "2.5"))
READ_TO = float(os.environ.get("READ_TIMEOUT_S", "7.5"))
TIMEOUT = (CONNECT_TO, READ_TO)  # (connect, read)

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

# Max number of memory snippet lines to return (excludes time/reminder text)
MAX_SNIPPETS = int(os.environ.get("MAX_SNIPPETS", "3"))

# Debug verbosity (also overridable per request)
DEBUG_VERBOSE_DEFAULT = os.environ.get("DEBUG_VERBOSE", "false").lower() == "true"

# Warmup caps (no "compact" mode anymore)
WARMUP_MAX_SPINE  = int(os.environ.get("WARMUP_MAX_SPINE", 100))
WARMUP_MAX_ANCHOR = int(os.environ.get("WARMUP_MAX_ANCHOR", 100))
WARMUP_CARVES_LIMIT = int(os.environ.get("WARMUP_CARVES_LIMIT", 4))

# ------------------------------------------------------------
# In-memory working-set cache
# ------------------------------------------------------------

MAX_WORKING_SET = 5
MAX_AGE = 2

# { thread_id: { "working_ids": deque, "turns_since_use": {}, "turn_index": int, "last_spine_turn": int } }
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


def _cap_snippets(core, spine_line_or_none, max_n):
    """
    Keep at most max_n lines total.
    - core: list of carves/echoes (already relevance-ordered)
    - spine_line_or_none: optional "(SPINE) ..." string
    If spine is present and we'd exceed max_n, replace the last core line.
    """
    out = list(core[:max_n])
    if spine_line_or_none:
        if len(out) >= max_n:
            out[-1] = spine_line_or_none
        else:
            out.append(spine_line_or_none)
    return out

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
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{memory_id}&select=title,factual_summary,tonal_snippet,summary_snippet",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        data = []

    if data:
        carve = data[0]
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

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/Echoes?id=eq.{memory_id}&select=summary_snippet,tags,persona_tag,source",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        data = []

    if data:
        echo = data[0]
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


def fetch_top_spine_by_rank():
    """Return a single 'top' spine statement by importance (then recency), or None."""
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/Spine"
            f"?select=statement"
            f"&order=importance.desc&order=timestamp.desc&limit=1",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if resp.ok and resp.json():
            return resp.json()[0].get("statement")
    except Exception:
        pass
    return None


def maybe_include_spine(thread_id):
    """
    Occasional inclusion only (no keyword trigger):
    - 20% chance (SPINE_INJECTION_PROB)
    - obey SPINE_COOLDOWN_TURNS per thread
    Returns a single "(SPINE) ..." string or None.
    """
    turn_idx = conversation_cache[thread_id]["turn_index"]
    last_turn = conversation_cache[thread_id]["last_spine_turn"]
    cooldown_ok = (turn_idx - last_turn) >= SPINE_COOLDOWN_TURNS

    if cooldown_ok and random.random() < SPINE_INJECTION_PROB:
        stmt = fetch_top_spine_by_rank()
        if stmt:
            conversation_cache[thread_id]["last_spine_turn"] = turn_idx
            return f"(SPINE) {stmt}"
    return None

# ------------------------------------------------------------
# Health / diagnostics
# ------------------------------------------------------------

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"ok": True, "ts": datetime.now(timezone.utc).isoformat()}), 200

# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------

@app.route("/warmup", methods=["GET"])
def warmup():
    """
    Return usable context only:
      - up to WARMUP_MAX_ANCHOR anchor entries (newest first)
      - up to WARMUP_MAX_SPINE spine entries (newest first)
      - the last WARMUP_CARVES_LIMIT carves (newest first)
    No embeddings are selected; only human-usable fields are returned.
    """
    import uuid
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /warmup start")

    try:
        # --- Anchor: summary only (no embeddings) ---
        anchor_qs = (
            f"{SUPABASE_URL}/rest/v1/Anchor"
            f"?select=summary_snippet,persona_tag,emotag"
            f"&order=timestamp.desc&limit={WARMUP_MAX_ANCHOR}"
        )
        anchor_res = requests.get(anchor_qs, headers=HEADERS, timeout=TIMEOUT)
        anchors = anchor_res.json() if anchor_res.ok else []

        # --- Spine: statement only (no embeddings) ---
        spine_qs = (
            f"{SUPABASE_URL}/rest/v1/Spine"
            f"?select=timestamp,statement,persona_tag,emotag"
            f"&order=timestamp.desc&limit={WARMUP_MAX_SPINE}"
        )
        spine_res = requests.get(spine_qs, headers=HEADERS, timeout=TIMEOUT)
        spine = spine_res.json() if spine_res.ok else []

        # --- Carves: last few, with human-useful fields only ---
        carves_qs = (
            f"{SUPABASE_URL}/rest/v1/Carves"
            f"?select=title,timestamp,summary,moments,insights,quotes,closing,emotag,persona_tag"
            f"&order=timestamp.desc&limit={WARMUP_CARVES_LIMIT}"
        )
        carves_res = requests.get(carves_qs, headers=HEADERS, timeout=TIMEOUT)
        carves = carves_res.json() if carves_res.ok else []

        out = {"anchor": anchors, "spine": spine, "recentCarves": carves}
        app.logger.info(
            f"[RID {rid}] /warmup ok anchors={len(anchors)} spine={len(spine)} carves={len(carves)}"
        )
        return jsonify(out), 200

    except requests.Timeout:
        app.logger.error(f"[RID {rid}] /warmup timeout")
        return jsonify({"error": "Failed to fetch warmup memory", "details": "timeout"}), 504
    except Exception as e:
        app.logger.exception(f"[RID {rid}] /warmup exception: {e}")
        return jsonify({"error": "Failed to fetch warmup memory", "details": str(e)}), 500


@app.route("/carves", methods=["POST"])
def create_carve():
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /carves start")
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
            app.logger.warning(f"[RID {rid}] /carves missing fields: {missing}")
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
            app.logger.warning(f"[RID {rid}] /carves validation: {errors}")
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

        response = requests.post(f"{SUPABASE_URL}/rest/v1/Carves", headers=HEADERS, json=carve_data, timeout=TIMEOUT)
        if not response.ok:
            app.logger.error(f"[RID {rid}] /carves supabase error: {response.text}")
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
                    echo_res = requests.post(f"{SUPABASE_URL}/rest/v1/Echoes", headers=HEADERS, json=echo_payload, timeout=TIMEOUT)
                    if echo_res.ok:
                        carve_response[0]["echo_suggested"] = True
                        carve_response[0]["suggested_echo"] = quote
                    break

        app.logger.info(f"[RID {rid}] /carves done ok")
        return jsonify(carve_response), 201

    except Exception as e:
        app.logger.exception(f"[RID {rid}] /carves exception: {e}")
        return jsonify({"error": "Failed to create carve", "details": str(e)}), 500


@app.route("/carves/search", methods=["GET"])
def search_carves():
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /carves/search start")

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

        try:
            title_response = requests.get(
                f"{SUPABASE_URL}/rest/v1/Carves?select=id,title,summary,quotes,moments,insights,closing,importance,timestamp,emotag&importance=gte.{importance_floor}",
                headers=HEADERS,
                timeout=TIMEOUT,
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
        except Exception:
            all_carves = []
            title_results = []

        # 2) Semantic if no title hit
        semantic_results = []
        if not title_results:
            try:
                search_response = requests.post(
                    f"{SUPABASE_URL}/functions/v1/retrieve_memories",
                    headers=HEADERS,
                    json={"userText": query, "k": limit * 2, "importanceFloor": importance_floor},
                    timeout=TIMEOUT,
                )
                if search_response.ok:
                    results = search_response.json()
                    carve_hits = results.get("vecHits", [])
                    semantic_results = [h for h in carve_hits if h.get("table_source") == "Carves"]
            except Exception:
                semantic_results = []

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
                    timeout=TIMEOUT,
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

        app.logger.info(f"[RID {rid}] /carves/search done ok")
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
        app.logger.exception(f"[RID {rid}] /carves/search error: {e}")
        return jsonify({"error": "Search failed", "details": str(e), "traceback": traceback.format_exc().splitlines()}), 500


@app.route("/carves/<carve_id>", methods=["PATCH"])
def update_carve(carve_id):
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /carves PATCH start {carve_id}")
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
                    timeout=TIMEOUT,
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
            timeout=TIMEOUT,
        )

        if response.ok:
            updated_carve = response.json()
            if updated_carve:
                app.logger.info(f"[RID {rid}] /carves PATCH done ok")
                return jsonify(updated_carve[0]), 200
            return jsonify({"error": "Carve not found"}), 404

        app.logger.error(f"[RID {rid}] /carves PATCH supabase error: {response.text}")
        return jsonify({"error": "Failed to update carve", "details": response.text}), 500

    except Exception as e:
        app.logger.exception(f"[RID {rid}] /carves PATCH exception: {e}")
        return jsonify({"error": "Failed to update carve", "details": str(e)}), 500


@app.route("/echoes", methods=["POST"])
def create_echo():
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /echoes start")
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

        response = requests.post(f"{SUPABASE_URL}/rest/v1/Echoes", headers=HEADERS, json=echo_data, timeout=TIMEOUT)
        if response.ok:
            app.logger.info(f"[RID {rid}] /echoes done ok")
            return jsonify(response.json()[0]), 201

        app.logger.error(f"[RID {rid}] /echoes supabase error: {response.text}")
        return jsonify({"error": "Failed to create echo", "details": response.text}), 500

    except Exception as e:
        app.logger.exception(f"[RID {rid}] /echoes exception: {e}")
        return jsonify({"error": "Failed to create echo", "details": str(e)}), 500


@app.route("/echoes/search", methods=["GET"])
def search_echoes():
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /echoes/search start")

    query = request.args.get("query")
    limit = min(int(request.args.get("limit", 8)), 15)
    importance_floor = float(request.args.get("importance_floor", 0.5))
    max_distance = float(request.args.get("max_distance", 0.8))

    if not query:
        return jsonify({"error": "Query parameter 'query' is required"}), 400

    try:
        try:
            resp = requests.post(
                f"{SUPABASE_URL}/functions/v1/retrieve_memories",
                headers=HEADERS,
                json={"userText": query, "k": limit, "importanceFloor": importance_floor},
                timeout=TIMEOUT,
            )
            vec_hits = resp.json().get("vecHits", []) if resp.ok else []
        except Exception:
            vec_hits = []

        semantic_hits = [h for h in vec_hits if h.get("table_source") == "Echoes" and h.get("distance", 1) <= max_distance][:limit]

        try:
            tag_res = requests.get(
                f"{SUPABASE_URL}/rest/v1/Echoes"
                f"?or=(tags.cs.{{{query}}},source.ilike.*{query}*)&limit={limit}"
                "&select=id,timestamp,summary_snippet,tags,source,importance,type,emotag,persona_tag,theme_tags",
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            tag_hits_json = tag_res.json() if tag_res.ok else []
        except Exception:
            tag_hits_json = []

        tag_hits = []
        for e in tag_hits_json:
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
                    timeout=TIMEOUT,
                )
                echo = detail_res.json()[0] if detail_res.ok and detail_res.json() else None

            if echo:
                echo["search_distance"] = item["search_distance"]
                full_echoes.append(echo)

        app.logger.info(f"[RID {rid}] /echoes/search done ok")
        return jsonify({"echoes": full_echoes, "returned": len(full_echoes)}), 200

    except Exception as err:
        app.logger.exception(f"[RID {rid}] /echoes/search exception: {err}")
        return jsonify({"error": "Failed to search echoes", "details": str(err)}), 500


@app.route("/spine", methods=["POST"])
def create_spine():
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /spine start")
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

        response = requests.post(f"{SUPABASE_URL}/rest/v1/Spine", headers=HEADERS, json=spine_data, timeout=TIMEOUT)
        if response.ok:
            app.logger.info(f"[RID {rid}] /spine done ok")
            return jsonify(response.json()[0]), 201

        app.logger.error(f"[RID {rid}] /spine supabase error: {response.text}")
        return jsonify({"error": "Failed to create spine entry", "details": response.text}), 500

    except Exception as e:
        app.logger.exception(f"[RID {rid}] /spine exception: {e}")
        return jsonify({"error": "Failed to create spine entry", "details": str(e)}), 500


@app.route("/spine/<spine_id>", methods=["DELETE"])
def delete_spine(spine_id):
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /spine DELETE start {spine_id}")
    try:
        response = requests.delete(f"{SUPABASE_URL}/rest/v1/Spine?id=eq.{spine_id}", headers=HEADERS, timeout=TIMEOUT)
        if response.ok:
            app.logger.info(f"[RID {rid}] /spine DELETE done ok")
            return jsonify({"message": "Spine entry deleted successfully"}), 200
        app.logger.error(f"[RID {rid}] /spine DELETE supabase error: {response.text}")
        return jsonify({"error": "Failed to delete spine entry", "details": response.text}), 500

    except Exception as e:
        app.logger.exception(f"[RID {rid}] /spine DELETE exception: {e}")
        return jsonify({"error": "Failed to delete spine entry", "details": str(e)}), 500


@app.route("/spine/search", methods=["GET"])
def search_spine():
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /spine/search start")

    query = request.args.get("query")
    limit = int(request.args.get("limit", 20))
    importance_floor = float(request.args.get("importance_floor", 0.3))
    max_distance = float(request.args.get("max_distance", 0.9))

    if not query:
        return jsonify({"error": "Query parameter required"}), 400

    limit = min(limit, 30)

    try:
        try:
            search_response = requests.post(
                f"{SUPABASE_URL}/functions/v1/retrieve_memories",
                headers=HEADERS,
                json={"userText": query, "k": min(limit * 2, 50), "importanceFloor": importance_floor},
                timeout=TIMEOUT,
            )
            results = search_response.json() if search_response.ok else {}
            spine_hits = results.get("vecHits", [])
            filtered_hits = [
                hit for hit in spine_hits if hit.get("table_source") == "Spine" and hit.get("distance", 1) <= max_distance
            ][:limit]
        except Exception:
            filtered_hits = []

        full_spine = []
        for hit in filtered_hits:
            spine_res = requests.get(
                f"{SUPABASE_URL}/rest/v1/Spine?id=eq.{hit['id']}&select=id,timestamp,statement,origin,vow,tags,importance,type,emotag,persona_tag,theme_tags",
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            if spine_res.ok and spine_res.json():
                spine = spine_res.json()[0]
                spine["search_distance"] = hit["distance"]
                full_spine.append(spine)

        app.logger.info(f"[RID {rid}] /spine/search done ok")
        return jsonify(
            {
                "spine_entries": full_spine,
                "total_found": len(filtered_hits),
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
        app.logger.exception(f"[RID {rid}] /spine/search exception: {e}")
        return jsonify({"error": "Failed to search spine", "details": str(e)}), 500


@app.route("/anchor", methods=["POST"])
def create_anchor():
    """
    Create an anchor entry about the conversation partner (usually Liz).
    Uses strict timeouts so Actions never stall.
    """
    import uuid
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /anchor POST start")

    try:
        data = request.get_json(force=True) or {}
        summary_snippet = data.get("summary_snippet")
        if not summary_snippet:
            app.logger.warning(f"[RID {rid}] /anchor missing summary_snippet")
            return jsonify({"error": "summary_snippet is required"}), 400

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

        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/Anchor",
            headers=HEADERS,
            json=anchor_data,
            timeout=TIMEOUT,
        )

        if resp.ok:
            out = resp.json()[0]
            app.logger.info(f"[RID {rid}] /anchor created id={out.get('id')}")
            return jsonify(out), 201

        app.logger.error(f"[RID {rid}] /anchor supabase error {resp.status_code}: {resp.text}")
        return jsonify({"error": "Failed to create anchor entry", "details": resp.text}), 500

    except requests.Timeout:
        app.logger.error(f"[RID {rid}] /anchor timeout")
        return jsonify({"error": "Anchor creation timed out"}), 504
    except Exception as e:
        app.logger.exception(f"[RID {rid}] /anchor exception: {e}")
        return jsonify({"error": "Failed to create anchor entry", "details": str(e)}), 500


@app.route("/anchor/persona/<persona_name>", methods=["GET"])
def get_anchor_by_persona(persona_name):
    import uuid
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /anchor/persona GET start persona={persona_name}")

    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/Anchor?persona_tag=eq.{persona_name}&order=timestamp.desc",
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if resp.ok:
            entries = resp.json()
            app.logger.info(f"[RID {rid}] /anchor/persona returned {len(entries)} rows")
            return jsonify(
                {
                    "persona": persona_name,
                    "anchor_entries": entries,
                    "total_entries": len(entries),
                    "message": f"Recalled {len(entries)} anchor entries about {persona_name}",
                }
            ), 200

        app.logger.error(f"[RID {rid}] /anchor/persona supabase error {resp.status_code}: {resp.text}")
        return jsonify({"error": "Failed to fetch anchor entries", "details": resp.text}), 500

    except requests.Timeout:
        app.logger.error(f"[RID {rid}] /anchor/persona timeout")
        return jsonify({"persona": persona_name, "anchor_entries": [], "total_entries": 0}), 200
    except Exception as e:
        app.logger.exception(f"[RID {rid}] /anchor/persona exception: {e}")
        return jsonify({"error": "Failed to fetch anchor entries", "details": str(e)}), 500


@app.route("/chat", methods=["POST"])
def chat_with_autopilot():
    """
    Returns up to 3 memory snippets (Carves/Echoes) that semantically match this turn,
    with a 20% chance to include one Spine truth (without exceeding 3 lines total).

    Fail-soft behavior:
      - If the vector function or Supabase calls time out, we return a minimal-but-valid payload quickly,
        so the Action never "stops talking to connector".
    """
    import uuid
    rid = str(uuid.uuid4())[:8]
    app.logger.info(f"[RID {rid}] /chat start")

    try:
        data = request.get_json() or {}
        user_msg   = data.get("message", "")
        thread_id  = data.get("thread_id", "default")

        # Optional flags
        debug_verbose  = bool(data.get("debug", DEBUG_VERBOSE_DEFAULT))
        diag_verbose   = bool(data.get("diag", False))
        inject_reminder = bool(data.get("inject_reminder", REMINDER_ENABLED))
        inject_position = data.get("inject_position", REMINDER_POSITION)

        # Retrieval tuning (clamped & safer defaults)
        try:
            retrieve_k = int(data.get("k", 12))
        except Exception:
            retrieve_k = 12
        retrieve_k = max(1, min(retrieve_k, 40))

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

        # Thread state
        init_thread(thread_id)
        conversation_cache[thread_id]["turn_index"] += 1

        # Retrieve candidate memories (fail-soft)
        candidates = []
        try:
            resp = requests.post(
                f"{SUPABASE_URL}/functions/v1/retrieve_memories",
                headers=HEADERS,
                json={"userText": user_msg, "k": retrieve_k, "importanceFloor": importance_floor},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            candidates = resp.json().get("vecHits", []) or []
        except requests.Timeout:
            app.logger.warning(f"[RID {rid}] /chat vector retrieve timeout; continuing with empty candidates")
            candidates = []
        except requests.RequestException as e:
            app.logger.warning(f"[RID {rid}] /chat vector retrieve error: {e}; continuing with empty candidates")
            candidates = []

        # Optional quality gate
        if max_distance is not None:
            def _ok(h):
                d = h.get("distance")
                return isinstance(d, (int, float)) and d <= max_distance
            candidates = [h for h in candidates if _ok(h)]

        # Only keep Carves or Echoes for working memory
        candidates = [h for h in candidates if h.get("table_source") in ("Carves", "Echoes")]

        # Recency boost (distance-minus-boost)
        boosted = []
        now_utc = datetime.now(timezone.utc)
        for hit in candidates:
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
                    adj = hit.get("distance", 1.0)
                boosted.append({**hit, "adjusted_distance": adj, "days_ago": days_ago})
            except Exception:
                boosted.append({**hit, "adjusted_distance": hit.get("distance", 1.0), "days_ago": 999})

        top_candidates = sorted(boosted, key=lambda m: m["adjusted_distance"])[:MAX_WORKING_SET]
        top_ids = [m["id"] for m in top_candidates]
        working_ids = update_working_set(thread_id, top_ids)

        # Build core snippets
        core_snippets = []
        for mid in working_ids:
            # Note: get_enhanced_memory_snippet() performs Supabase reads; those should
            # have been updated earlier in the file to add timeout=TIMEOUT.
            snip = get_enhanced_memory_snippet(mid)
            if snip:
                core_snippets.append(snip)
        core_snippets = core_snippets[:MAX_SNIPPETS]

        # Occasional Spine (20% chance + cooldown)
        spine_line = maybe_include_spine(thread_id)
        memory_lines = _cap_snippets(core_snippets, spine_line, MAX_SNIPPETS)

        # Time context (Central)
        central_tz = pytz.timezone("US/Central")
        time_context = f"Current time: {datetime.now(central_tz).strftime('%A, %B %d, %Y at %I:%M %p %Z')}"

        # Assemble
        if inject_reminder and inject_position == "start":
            all_snippets = [REMINDER_TEXT, time_context] + memory_lines
        else:
            all_snippets = [time_context] + memory_lines
            if inject_reminder and inject_position == "end":
                all_snippets.append(REMINDER_TEXT)

        base_payload = {
            "memory_snippets": all_snippets,
            "spine_included": any(s.startswith("(SPINE)") for s in memory_lines),
            "recency_boost_applied": True,
        }

        # Telemetry (brief by default; fuller if diag=true)
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
                "request_id": rid,
                "params": {"k": retrieve_k, "importance_floor": importance_floor, "max_distance": max_distance},
                "counts": {
                    "total_candidates": len(candidates),
                    "within_max_distance": (
                        sum(1 for c in candidates if isinstance(c.get("distance"), (int, float)) and (max_distance is None or c["distance"] <= max_distance))
                    ),
                },
                "distance_stats": {
                    "min": d_sorted[0] if d_sorted else None,
                    "median": percentile(d_sorted, 50),
                    "p90": percentile(d_sorted, 90),
                },
            }
            base_payload["retrieval_telemetry"] = telemetry

            if diag_verbose:
                base_payload.update({
                    "thread_id_echo": thread_id,
                    "turn_index": conversation_cache[thread_id]["turn_index"],
                    "debug_candidates": [
                        {
                            "id": c.get("id", "")[:8],
                            "original_distance": c.get("distance"),
                            "adjusted_distance": c.get("adjusted_distance"),
                            "days_ago": c.get("days_ago"),
                            "table_source": c.get("table_source"),
                        }
                        for c in top_candidates[:3]
                    ],
                })

        base_payload["message"] = _format_chat_message(
            memory_lines,
            base_payload.get("retrieval_telemetry") if (debug_verbose and diag_verbose) else None
        )

        app.logger.info(f"[RID {rid}] /chat ok, lines={len(memory_lines)}")
        return jsonify(base_payload), 200

    except Exception as e:
        import traceback
        app.logger.exception(f"[RID {rid}] /chat exception: {e}")
        return jsonify({"error": str(e), "traceback": traceback.format_exc().splitlines(), "request_id": rid}), 500


# ------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------
if __name__ == "__main__":
    # Important: do not enable Flask debug in prod behind Actions; Werkzeug reloader
    # can serve a transient empty/partial OpenAPI during reloads.
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
