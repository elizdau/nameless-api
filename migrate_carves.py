# Migration script to generate factual_summary and tonal_snippet for all existing carves.
# Run this ONCE after adding the new database columns.

import requests
import json
import time
from datetime import datetime

# Your Supabase configuration
SUPABASE_URL = "https://heaelbdveisxdjwsfiyk.supabase.co"
SUPABASE_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhlYWVsYmR2ZWlzeGRqd3NmaXlrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDQzMDg3NDEsImV4cCI6MjA1OTg4NDc0MX0.e72xiBw0pYhvD_m54Tx7m8KKrFzJMymo0-lu0UsS8ns"

HEADERS = {
    "apikey": SUPABASE_API_KEY,
    "Authorization": f"Bearer {SUPABASE_API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

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
    if key_entities:
        if isinstance(key_entities, str):
            # Handle JSON string
            try:
                key_entities = json.loads(key_entities)
            except:
                key_entities = [key_entities]
        
        if key_entities and len(key_entities) > 0:
            entity_str = ", ".join(str(e) for e in key_entities[:5])  # Top 5 entities
            factual_parts.append(f"Involves: {entity_str}")
    
    # Add timestamp context if available
    if timestamp:
        try:
            # Handle different timestamp formats
            if 'T' in timestamp:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                factual_parts.append(f"Date: {dt.strftime('%B %d, %Y')}")
        except Exception as e:
            print(f"  Warning: Could not parse timestamp {timestamp}: {e}")
    
    # Add key factual moments (first 2-3)
    factual_moments = []
    if moments:
        if isinstance(moments, str):
            try:
                moments = json.loads(moments)
            except:
                moments = [moments]
        
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
    
    # Check quotes first
    if quotes:
        if isinstance(quotes, str):
            try:
                quotes = json.loads(quotes)
            except:
                quotes = [quotes]
        
        for quote in quotes:
            if quote and len(str(quote)) < 120:
                quote_str = str(quote)
                # Look for evocative words
                if any(word in quote_str.lower() for word in 
                    ['felt', 'heart', 'soul', 'breath', 'whisper', 'echo', 'light', 'shadow', 
                     'warm', 'cold', 'still', 'wild', 'soft', 'gentle', 'fierce', 'quiet']):
                    evocative_content = f'"{quote_str}"'
                    break
    
    # If no evocative quote, check insights
    if not evocative_content and insights:
        if isinstance(insights, str):
            try:
                insights = json.loads(insights)
            except:
                insights = [insights]
        
        for insight in insights:
            if insight and len(str(insight)) < 120:
                evocative_content = str(insight)
                break
    
    # Extract tonal essence from summary (look for metaphors, sensory details)
    tonal_essence = ""
    if summary:
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
        # Fallback to first part of summary with emotional framing
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

def migrate_carves():
    """Main migration function"""
    print("🚀 Starting dual summary migration...")
    
    # Get all carves that need migration (where dual summaries are null)
    print("📋 Fetching carves to migrate...")
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/Carves?select=*&or=(factual_summary.is.null,tonal_snippet.is.null)",
        headers=HEADERS
    )
    
    if not response.ok:
        print(f"❌ Failed to fetch carves: {response.text}")
        return
    
    carves = response.json()
    total_carves = len(carves)
    
    if total_carves == 0:
        print("✅ No carves need migration. All done!")
        return
    
    print(f"📊 Found {total_carves} carves to migrate")
    
    success_count = 0
    error_count = 0
    
    for i, carve in enumerate(carves, 1):
        carve_id = carve.get("id")
        title = carve.get("title", "Untitled")
        
        print(f"🔄 [{i}/{total_carves}] Processing: {title[:50]}{'...' if len(title) > 50 else ''}")
        
        try:
            # Generate dual summaries
            dual_summaries = generate_dual_summaries(carve)
            
            # Update the carve
            update_response = requests.patch(
                f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{carve_id}",
                headers=HEADERS,
                json={
                    "factual_summary": dual_summaries["factual_summary"],
                    "tonal_snippet": dual_summaries["tonal_snippet"]
                }
            )
            
            if update_response.ok:
                success_count += 1
                print(f"  ✅ Updated successfully")
            else:
                error_count += 1
                print(f"  ❌ Update failed: {update_response.text}")
            
        except Exception as e:
            error_count += 1
            print(f"  ❌ Error processing carve: {str(e)}")
        
        # Small delay to be nice to the API
        time.sleep(0.1)
    
    print(f"\n🎉 Migration complete!")
    print(f"✅ Successfully migrated: {success_count}")
    print(f"❌ Errors: {error_count}")
    print(f"📊 Total processed: {success_count + error_count}")

def verify_migration():
    """Verify the migration worked"""
    print("\n🔍 Verifying migration...")
    
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/Carves?select=id,title,factual_summary,tonal_snippet&limit=5",
        headers=HEADERS
    )
    
    if response.ok:
        carves = response.json()
        print(f"📋 Sample of migrated carves:")
        for carve in carves[:3]:
            title = carve.get("title", "Untitled")
            factual = carve.get("factual_summary", "")[:100] + "..." if carve.get("factual_summary") else "None"
            tonal = carve.get("tonal_snippet", "")[:100] + "..." if carve.get("tonal_snippet") else "None"
            
            print(f"\n📄 {title}")
            print(f"  🔍 Factual: {factual}")
            print(f"  🎭 Tonal: {tonal}")

if __name__ == "__main__":
    try:
        migrate_carves()
        verify_migration()
    except KeyboardInterrupt:
        print("\n⏹️  Migration stopped by user")
    except Exception as e:
        print(f"\n💥 Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
