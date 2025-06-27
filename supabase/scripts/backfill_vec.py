"""
backfill_vec.py  –  populate missing pgvector embeddings in Supabase.

Run once.  It finds rows where `embedding IS NULL` in every memory table
(Carves, Echoes, Spine, Anchor, Figures), generates a MiniLM vector from
`summary_snippet`, and writes it back.

Env vars used (already present for your Render service):
  SUPABASE_URL
  SUPABASE_PRIV_KEY   (service-role key)
  OPENAI_API_KEY      (if you choose OpenAI backend)
"""

import os, sys, time
from typing import List
from supabase import create_client, Client

# ── choose embedding backend ────────────────────────────────────────────────
USE_LOCAL = True            # set False to call OpenAI instead

if USE_LOCAL:
    from sentence_transformers import SentenceTransformer
    _model = SentenceTransformer("all-MiniLM-L6-v2")
    def embed(text: str) -> List[float]:
        return _model.encode(text).tolist()
else:
    import openai
    openai.api_key = os.environ["OPENAI_API_KEY"]
    def embed(text: str) -> List[float]:
        return openai.embeddings.create(
            model="text-embedding-3-small",
            input=text
        ).data[0].embedding
# ───────────────────────────────────────────────────────────────────────────

URL = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_PRIV_KEY"]
sup: Client = create_client(URL, KEY)

TABLES = ["Carves", "Echoes", "Spine", "Anchor", "Figures"]

def backfill(table: str) -> None:
    print(f"▶  {table}")
    rows = (sup.table(table)
              .select("id,summary_snippet")
              .is_("embedding", "null")
              .execute()
              .data)
    if not rows:
        print("   ✓ already complete")
        return
    for r in rows:
        snippet = r["summary_snippet"] or ""
        vec = embed(snippet)
        sup.table(table).update(
            {"embedding": vec, "last_used": "now()"}
        ).eq("id", r["id"]).execute()
        time.sleep(0.05)          # polite rate-limit
    print(f"   ✓ {len(rows)} rows updated")

def main():
    for tbl in TABLES:
        backfill(tbl)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
