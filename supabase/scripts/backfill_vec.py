import os, time, json, requests
from typing import List, Optional
from openai import OpenAI

# ── Env ────────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_PRIV_KEY = os.environ["SUPABASE_PRIV_KEY"]
OPENAI_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
# If your column dim != the model's default, set EMBED_DIM (e.g., 1536 or 3072)
EMBED_DIM = os.environ.get("EMBED_DIM")  # string or None
BATCH = int(os.environ.get("BATCH", "100"))  # rows per page

HEADERS = {
    "apikey": SUPABASE_PRIV_KEY,
    "Authorization": f"Bearer {SUPABASE_PRIV_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

client = OpenAI()  # uses OPENAI_API_KEY

# ── Which text to use per table (fallback order) ───────────────────────────────
TBL_CONFIG = {
    "Carves": {
        "fields": ["summary_snippet", "summary", "title", "closing"],
        "array_fields": ["quotes", "moments", "insights"],  # optional strings in arrays
    },
    "Echoes": {
        "fields": ["summary_snippet"],
        "array_fields": [],
    },
    "Spine": {
        "fields": ["summary_snippet", "statement"],
        "array_fields": [],
    },
    "Anchor": {
        "fields": ["summary_snippet"],
        "array_fields": [],
    },
}

TABLES = ["Carves", "Echoes", "Spine", "Anchor"]


# ── Helpers ────────────────────────────────────────────────────────────────────
def _coerce_list_from_jsonish(val) -> List[str]:
    """If val is a JSON string list, parse it; if list already, return it; else []."""
    if not val:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.strip().startswith("["):
        try:
            data = json.loads(val)
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _first_text_from_row(table: str, row: dict) -> str:
    """Pick the best available text for embedding with fallbacks."""
    cfg = TBL_CONFIG[table]
    # 1) Normal fields in order
    for f in cfg["fields"]:
        v = (row.get(f) or "").strip()
        if v:
            return v

    # 2) Try first non-empty string in any array field
    for af in cfg.get("array_fields", []):
        arr = _coerce_list_from_jsonish(row.get(af))
        for item in arr:
            if isinstance(item, str) and item.strip():
                return item.strip()

    # 3) Nothing usable
    return ""


def fetch_nulls(table: str, limit: int, offset: int) -> List[dict]:
    # Build select list: id + all configured fields + timestamp (for ordering) + array fields
    cfg = TBL_CONFIG[table]
    select_cols = ["id", "timestamp"] + cfg["fields"] + cfg.get("array_fields", [])
    params = {
        "select": ",".join(dict.fromkeys(select_cols)),  # dedupe
        "embedding": "is.null",
        "order": "timestamp.desc",  # all these tables have timestamp; if not, drop this
        "limit": str(limit),
        "offset": str(offset),
    }
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def embed_many(texts: List[str]) -> List[List[float]]:
    # Optionally pass dimensions to match your pgvector column
    if EMBED_DIM:
        resp = client.embeddings.create(model=OPENAI_MODEL, input=texts, dimensions=int(EMBED_DIM))
    else:
        resp = client.embeddings.create(model=OPENAI_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def patch_row(table: str, row_id: str, vec: List[float]) -> None:
    for attempt in range(3):
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}",
            headers=HEADERS,
            json={"embedding": vec},  # DO NOT send "now()" here
            timeout=30,
        )
        if r.status_code in (200, 204):
            return
        # Helpful to print the body when it fails (dimension mismatch shows up here)
        print(f"[PATCH {table}] attempt {attempt+1} -> {r.status_code} {r.text}")
        time.sleep(1 + attempt)


def process_table(table: str) -> int:
    print(f"▶  {table}", flush=True)
    total_updated = 0
    offset = 0
    while True:
        try:
            rows = fetch_nulls(table, BATCH, offset)
        except requests.HTTPError as e:
            print(f"   ✗ Fetch error: {e.response.status_code} {e.response.text}", flush=True)
            break

        if not rows:
            if offset == 0:
                print("   ✓ already complete", flush=True)
            break

        print(f"   Found {len(rows)} rows to process (page offset {offset})", flush=True)
        texts, ids = [], []

        for i, row in enumerate(rows, start=1):
            if i % 10 == 1:
                # periodic progress
                print(f"   Processing row {i}/{len(rows)}", flush=True)

            text = _first_text_from_row(table, row)
            if not text:
                print(f"   ⚠ Skipping {row['id']} - no usable text", flush=True)
                continue

            # Hard cap length (embedding can take long inputs, but we keep it tidy)
            texts.append(text[:8000])
            ids.append(row["id"])

        if not texts:
            offset += len(rows)
            continue

        try:
            vecs = embed_many(texts)
        except Exception as e:
            print(f"   ✗ Embedding error: {type(e).__name__}: {e}", flush=True)
            # If the error is dimensions-related, set EMBED_DIM env var accordingly.
            break

        for row_id, vec in zip(ids, vecs):
            try:
                patch_row(table, row_id, vec)
                total_updated += 1
            except Exception as e:
                print(f"   ✗ Patch error for {row_id}: {type(e).__name__}: {e}", flush=True)

        offset += len(rows)

    print(f"   ✓ {total_updated} rows updated", flush=True)
    return total_updated


def main():
    grand_total = 0
    print(f"Model={OPENAI_MODEL}  Batch={BATCH}  Dim={EMBED_DIM or 'model default'}")
    for tbl in TABLES:
        try:
            grand_total += process_table(tbl)
        except Exception as e:
            print(f"[{tbl}] Unhandled: {type(e).__name__}: {e}", flush=True)
    print(f"Done. Total updated across tables: {grand_total}")


if __name__ == "__main__":
    import sys
    try:
        main()
        sys.exit(0)   # success even if 0 rows updated
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:
        print("Fatal:", e, flush=True)
        sys.exit(1)

