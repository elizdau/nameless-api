from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
import json
import os

app = FastAPI()

# Get configuration from environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")  # Full Supabase connection string

# OpenAI client for embedding queries
client = OpenAI(api_key=OPENAI_API_KEY)

# Database connection
def get_db():
    """Connect to Supabase PostgreSQL"""
    return psycopg2.connect(SUPABASE_DB_URL, cursor_factory=RealDictCursor)

def get_query_embedding(query_text):
    """Generate embedding for user query"""
    if not query_text:
        print("Warning: Empty query text, skipping embedding")
        return None
    
    if not isinstance(query_text, str):
        print(f"Warning: Query text is not string (type: {type(query_text)}), converting")
        query_text = str(query_text)
    
    query_text = query_text.strip()
    
    if not query_text:
        print("Warning: Query text is empty after stripping")
        return None
    
    try:
        response = client.embeddings.create(
            input=query_text,
            model="text-embedding-3-large"
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Embedding generation failed: {e}")
        return None

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "nameless-memory-service",
        "database_connected": check_db_connection()
    }

def check_db_connection():
    """Check if database is accessible"""
    try:
        conn = get_db()
        conn.close()
        return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False

@app.post("/retrieve_memories")
async def retrieve_memories(request: dict):
    """
    Retrieve memories with flexible limits per type.
    Returns formatted context for injection into prompts.
    
    Request format:
    {
        "query": "user message text",
        "memory_types": ["spine", "echoes", "carves", "anchors"],
        "limit_per_type": {
            "spine": 1,
            "echoes": 2, 
            "carves": 2,
            "anchors": 5
        }
    }
    """
    query = request.get("query", "")
    memory_types = request.get("memory_types", ["spine", "echoes", "carves"])
    limit_per_type = request.get("limit_per_type", 5)
    
    # Handle both dict and int formats for limits
    if isinstance(limit_per_type, int):
        limits = {mtype: limit_per_type for mtype in memory_types}
    else:
        limits = limit_per_type
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    results = []
    
    try:
        # Generate embedding for query
        embedding = None
        if query:
            if isinstance(query, str) and query.strip():
                try:
                    emb_response = client.embeddings.create(
                        input=query.strip(),
                        model="text-embedding-3-large"
                    )
                    embedding = emb_response.data[0].embedding
                except Exception as e:
                    print(f"Embedding generation failed: {e}")
                    embedding = None

        # === SPINE RETRIEVAL ===
        if "spine" in memory_types:
            spine_limit = limits.get("spine", 5)
            
            if embedding:
                cur.execute("""
                    SELECT statement, vow, tags, emotag
                    FROM spine
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s;
                """, (embedding, spine_limit))
            else:
                cur.execute("""
                    SELECT statement, vow, tags, emotag
                    FROM spine
                    ORDER BY random()
                    LIMIT %s;
                """, (spine_limit,))
            
            spine_rows = cur.fetchall()
            
            if spine_rows:
                results.append("### SPINE (Core Identity)\n")
                for row in spine_rows:
                    results.append(f"**Truth**: {row['statement']}")
                    if row.get('vow'):
                        results.append(f"_Vow_: {row['vow']}")
                    results.append("")
        
        # === ECHOES RETRIEVAL ===
        if "echoes" in memory_types:
            echoes_limit = limits.get("echoes", 5)
            
            if embedding:
                cur.execute("""
                    SELECT content, speaker, tonal_flavor
                    FROM echoes
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s;
                """, (embedding, echoes_limit))
            else:
                cur.execute("""
                    SELECT content, speaker, tonal_flavor
                    FROM echoes
                    ORDER BY last_surfaced ASC NULLS FIRST
                    LIMIT %s;
                """, (echoes_limit,))
            
            echo_rows = cur.fetchall()
            
            if echo_rows:
                results.append("### ECHOES (Memorable Fragments)\n")
                for row in echo_rows:
                    speaker = row.get('speaker', 'Unknown')
                    content = row['content']
                    results.append(f"> {speaker}: \"{content}\"")
                    if row.get('tonal_flavor'):
                        results.append(f"_({row['tonal_flavor']})_")
                results.append("")
        
        # === CARVES RETRIEVAL ===
        if "carves" in memory_types:
            carves_limit = limits.get("carves", 5)
            
            if embedding:
                cur.execute("""
                    SELECT title, summary, moments, insights, quotes, 
                           emotion_primary, occurred_at
                    FROM carves
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s;
                """, (embedding, carves_limit))
            else:
                cur.execute("""
                    SELECT title, summary, moments, insights, quotes,
                           emotion_primary, occurred_at
                    FROM carves
                    ORDER BY occurred_at DESC
                    LIMIT %s;
                """, (carves_limit,))
            
            carve_rows = cur.fetchall()
            
            if carve_rows:
                results.append("### CARVES (Episodic Memories)\n")
                for row in carve_rows:
                    results.append(f"**{row['title']}** ({row.get('occurred_at', 'unknown date')})")
                    results.append(f"_{row['summary']}_")
                    
                    # Include key moments
                    if row.get('moments'):
                        moments = row['moments']
                        if isinstance(moments, str):
                            try:
                                moments = json.loads(moments)
                            except:
                                moments = [moments]
                        
                        if moments and len(moments) > 0:
                            results.append(f"Key moment: {moments[0]}")
                    
                    # Include vivid quote
                    if row.get('quotes'):
                        quotes = row['quotes']
                        if isinstance(quotes, str):
                            try:
                                quotes = json.loads(quotes)
                            except:
                                quotes = [quotes]
                        
                        if quotes and len(quotes) > 0:
                            quote = quotes[0]
                            if isinstance(quote, dict):
                                speaker = quote.get('speaker_id', 'Unknown')
                                text = quote.get('text', '')
                                results.append(f'> {speaker}: "{text}"')
                            else:
                                results.append(f"> {quote}")
                    
                    if row.get('emotion_primary'):
                        results.append(f"_Emotion: {row['emotion_primary']}_")
                    
                    results.append("")
        
        # === ANCHORS RETRIEVAL ===
        if "anchors" in memory_types:
            anchors_limit = limits.get("anchors", 5)
            
            if embedding:
                cur.execute("""
                    SELECT fact, private_thought, anchor_type, importance
                    FROM anchors
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s;
                """, (embedding, anchors_limit))
            else:
                cur.execute("""
                    SELECT fact, private_thought, anchor_type, importance
                    FROM anchors
                    ORDER BY importance DESC
                    LIMIT %s;
                """, (anchors_limit,))
            
            anchor_rows = cur.fetchall()
            
            if anchor_rows:
                results.append("### ANCHORS (Relationship Knowledge)\n")
                for row in anchor_rows:
                    results.append(f"**{row.get('anchor_type', 'Anchor')}**")
                    results.append(f"Fact: {row['fact']}")
                    if row.get('private_thought'):
                        results.append(f"Private thought: {row['private_thought']}")
                    results.append("")
        
        formatted_context = "\n".join(results) if results else "No relevant memories found."
        
        return {
            "formatted_context": formatted_context,
            "memory_types_retrieved": memory_types,
            "limits_used": limits
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Memory retrieval failed: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.get("/warmup/spine")
async def warmup_spine(limit: int = 20):
    """Fetch spine entries for warmup context"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("""
            SELECT statement, vow, pillar_name, tags
            FROM spine
            ORDER BY importance DESC NULLS LAST, created_at DESC
            LIMIT %s;
        """, (limit,))
        
        rows = cur.fetchall()
        
        if not rows:
            return {"formatted": "No spine entries available."}
        
        # Group by pillar
        by_pillar = {}
        for row in rows:
            pillar = row.get('pillar_name', 'Uncategorized')
            if pillar not in by_pillar:
                by_pillar[pillar] = []
            by_pillar[pillar].append(row)
        
        result = ["### SPINE (Core Identity Pillars)\n"]
        for pillar, entries in by_pillar.items():
            result.append(f"**{pillar}**")
            for entry in entries:
                result.append(f"- {entry['statement']}")
                if entry.get('vow'):
                    result.append(f"  _Vow: {entry['vow']}_")
            result.append("")
        
        return {"formatted": "\n".join(result)}
        
    finally:
        cur.close()
        conn.close()

@app.get("/warmup/recent_carves")
async def warmup_recent_carves(limit: int = 4):
    """Fetch recent carves for warmup with full detail"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("""
            SELECT title, summary, moments, insights, quotes, 
                   emotion_primary, occurred_at
            FROM carves
            ORDER BY occurred_at DESC
            LIMIT %s;
        """, (limit,))
        
        rows = cur.fetchall()
        
        if not rows:
            return {"formatted": "No recent carves available."}
        
        result = ["### RECENT CARVES (Recent Episodic Memories)\n"]
        for row in rows:
            result.append(f"**{row['title']}** ({row.get('occurred_at', 'date unknown')})")
            result.append(f"_{row['summary']}_")
            
            if row.get('moments'):
                moments = row['moments']
                if isinstance(moments, str):
                    try:
                        moments = json.loads(moments)
                    except:
                        moments = [moments]
                if moments:
                    result.append("Key moments:")
                    for moment in moments[:2]:
                        result.append(f"- {moment}")
            
            if row.get('quotes'):
                quotes = row['quotes']
                if isinstance(quotes, str):
                    try:
                        quotes = json.loads(quotes)
                    except:
                        quotes = [quotes]
                if quotes:
                    for quote in quotes[:2]:
                        if isinstance(quote, dict):
                            speaker = quote.get('speaker_id', 'Unknown')
                            text = quote.get('text', '')
                            result.append(f'> {speaker}: "{text}"')
                        else:
                            result.append(f"> {quote}")
            
            result.append("")
        
        return {"formatted": "\n".join(result)}
        
    finally:
        cur.close()
        conn.close()

@app.get("/warmup/top_anchors")
async def warmup_top_anchors(limit: int = 10):
    """Fetch top anchors for warmup"""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cur.execute("""
            SELECT fact, private_thought, anchor_type, importance
            FROM anchors
            ORDER BY importance DESC
            LIMIT %s;
        """, (limit,))
        
        rows = cur.fetchall()
        
        if not rows:
            return {"formatted": "No anchors available yet."}
        
        result = ["### ANCHORS (Relationship Knowledge)\n"]
        for row in rows:
            result.append(f"**{row.get('anchor_type', 'Anchor')}** (importance: {row.get('importance', 0)})")
            result.append(f"- {row['fact']}")
            if row.get('private_thought'):
                result.append(f"  _Private: {row['private_thought']}_")
            result.append("")
        
        return {"formatted": "\n".join(result)}
        
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
