"""
backfill_vec.py  –  populate missing pgvector embeddings in Supabase.

Run once. It finds rows where `embedding IS NULL` in every memory table
(Carves, Echoes, Spine, Anchor, Figures), generates an OpenAI vector from
`summary_snippet`, and writes it back via Supabase REST API.

Env vars used (already present for your Render worker):
  SUPABASE_URL
  SUPABASE_PRIV_KEY   (service-role key)
  OPENAI_API_KEY
"""

import os, sys, time, json, requests

# ─── Remove any inherited proxy settings to avoid client proxy bugs ───────
for _var in ("HTTP_PROXY","http_proxy","HTTPS_PROXY","https_proxy","NO_PROXY","no_proxy"):
    os.environ.pop(_var, None)

# ─── Setup Supabase REST headers ───────────────────────────────────────
URL = os.environ.get("SUPABASE_URL").rstrip('/')
KEY = os.environ.get("SUPABASE_PRIV_KEY")
HEADERS = {
    'apikey': KEY,
    'Authorization': f'Bearer {KEY}',
    'Content-Type': 'application/json'
}

# ─── OpenAI embedding function ───────────────────────────────────────
import openai
openai.api_key = os.environ.get("OPENAI_API_KEY")

def embed(text: str):
    response = openai.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

# ─── Tables to backfill ───────────────────────────────────────────────
TABLES = ["Carves", "Echoes", "Spine", "Anchor", "Figures"]

# ─── Backfill routine ─────────────────────────────────────────────────
def backfill(table: str) -> None:
    print(f"▶  {table}")
    # Fetch rows with embedding null
    params = {
        'select': 'id,summary_snippet,embedding',
        'embedding': 'is.null'
    }
    resp = requests.get(
        f"{URL}/rest/v1/{table}", params=params, headers=HEADERS
    )
    rows = resp.json()
    if not rows:
        print("   ✓ already complete")
        return
    for row in rows:
        snippet = row.get('summary_snippet','') or ''
        vec = embed(snippet)
        payload = {"embedding": vec, "last_used": "now()"}
        patch = requests.patch(
            f"{URL}/rest/v1/{table}?id=eq.{row['id']}",
            headers=HEADERS,
            data=json.dumps(payload)
        )
        if patch.status_code not in (200,204):
            print(f"   ✗ failed to update {row['id']}: {patch.text}")
        time.sleep(0.05)
    print(f"   ✓ {len(rows)} rows updated")

# ─── Execute backfill ─────────────────────────────────────────────────
def main():
    for tbl in TABLES:
        backfill(tbl)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
