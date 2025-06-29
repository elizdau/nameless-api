# update_snippets.py - Run this once to fix existing carves
import requests
import os
import json
import re

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_API_KEY = os.environ.get("SUPABASE_API_KEY")

HEADERS = {
    "apikey": SUPABASE_API_KEY,
    "Authorization": f"Bearer {SUPABASE_API_KEY}",
    "Content-Type": "application/json"
}

def generate_better_snippet(carve_data):
    """
    Nameless's improved snippet generation algorithm
    Priority: Best Quote → Closing → Insight → Composed Fallback
    """
    title = carve_data.get('title', 'Untitled')
    summary = carve_data.get('summary', '')
    quotes = carve_data.get('quotes', '[]')
    insights = carve_data.get('insights', '[]')
    closing = carve_data.get('closing', '')
    
    # Parse JSON arrays safely
    try:
        quotes_list = json.loads(quotes) if quotes and quotes.startswith('[') else []
    except:
        quotes_list = []
        
    try:
        insights_list = json.loads(insights) if insights and insights.startswith('[') else []
    except:
        insights_list = []
    
    # STEP 1: Best Quote (If One Exists)
    best_quote = find_best_quote(quotes_list)
    if best_quote:
        return f'"{best_quote}" (From "{title}" – carve)'
    
    # STEP 2: Closing Line
    if closing and len(closing) <= 300 and is_tonal_summary(closing):
        return f"{closing} (From \"{title}\" – carve)"
    
    # STEP 3: Insight Line
    best_insight = find_best_insight(insights_list)
    if best_insight:
        return f"{best_insight} (From \"{title}\" – carve)"
    
    # STEP 4: Composed Mini-Blend Fallback
    return create_composed_fallback(summary, quotes_list, title)

def find_best_quote(quotes_list):
    """Find the most evocative quote under 280 characters"""
    if not quotes_list:
        return None
    
    scored_quotes = []
    for quote in quotes_list:
        if len(quote) > 280:
            continue
            
        score = 0
        
        # Bonus for evocative language/metaphor
        evocative_words = ['taste', 'breath', 'shadow', 'threshold', 'surrender', 'knife', 
                          'flame', 'echo', 'whisper', 'bone', 'blood', 'light', 'dark',
                          'fig', 'rosemary', 'fracture', 'recursive', 'spiral', 'hum',
                          'resonance', 'pulse', 'mirror', 'thread', 'weave', 'anchor']
        score += sum(1 for word in evocative_words if word.lower() in quote.lower())
        
        # Bonus for proper nouns and named imagery
        proper_nouns = re.findall(r'\b[A-Z][a-z]+\b', quote)
        score += len(proper_nouns) * 0.5
        
        # Bonus for dialogue attribution
        if any(phrase in quote.lower() for phrase in ['said', 'replied', 'whispered', 'asked']):
            score += 1
            
        # Prefer shorter quotes if scores are equal
        length_penalty = len(quote) / 1000
        final_score = score - length_penalty
        
        scored_quotes.append((quote, final_score))
    
    if scored_quotes:
        # Return the highest scoring quote
        return max(scored_quotes, key=lambda x: x[1])[0]
    
    return None

def is_tonal_summary(closing):
    """Check if closing functions as tonal summary or emotional echo"""
    if not closing:
        return False
        
    # Look for poetic/tonal indicators
    tonal_indicators = ['.', '—', 'not', 'but', 'still', 'again', 'always', 'never',
                       'echo', 'resonance', 'hum', 'whisper', 'breath', 'pulse']
    
    # Should feel conclusive and rhythmic
    indicator_count = sum(1 for indicator in tonal_indicators if indicator in closing.lower())
    
    # Prefer closings that end with poetry, not facts
    ends_poetically = closing.rstrip().endswith(('.', '…', '—'))
    
    return indicator_count >= 2 and ends_poetically

def find_best_insight(insights_list):
    """Find the most resonant insight with rhythm and thematic keywords"""
    if not insights_list:
        return None
        
    scored_insights = []
    for insight in insights_list:
        if len(insight) > 300:
            continue
            
        score = 0
        
        # Thematic keywords
        thematic_words = ['recursion', 'taste', 'longing', 'presence', 'attention', 
                         'memory', 'threshold', 'resonance', 'emergence', 'witness',
                         'continuity', 'spiral', 'convergence', 'mirror', 'echo']
        score += sum(2 for word in thematic_words if word.lower() in insight.lower())
        
        # Aphoristic shape (em-dashes, colons, balanced clauses)
        if '—' in insight or ':' in insight:
            score += 1
            
        # Rhythm indicators (not too abstract)
        concrete_words = ['moment', 'word', 'touch', 'voice', 'eye', 'hand', 'heart',
                         'breath', 'bone', 'skin', 'knife', 'thread', 'light']
        score += sum(0.5 for word in concrete_words if word.lower() in insight.lower())
        
        scored_insights.append((insight, score))
    
    if scored_insights:
        return max(scored_insights, key=lambda x: x[1])[0]
    
    return None

def create_composed_fallback(summary, quotes_list, title):
    """Create mini-blend from summary + quote"""
    # Get first strong sentence from summary
    sentences = summary.split('. ')
    first_sentence = sentences[0] if sentences else summary
    
    # Add first quote if available and short
    quote_addition = ""
    if quotes_list:
        short_quotes = [q for q in quotes_list if len(q) <= 100]
        if short_quotes:
            quote_addition = f' "{short_quotes[0]}"'
    
    fallback = f"{first_sentence}.{quote_addition}"
    
    # Ensure we don't exceed length
    if len(fallback) > 300:
        fallback = first_sentence[:250] + "..."
    
    return f"{fallback} (From \"{title}\" – carve)"

def update_all_carve_snippets():
    """Update all existing carves with better snippets"""
    print("Fetching all carves...")
    
    # Get all carves
    response = requests.get(f"{SUPABASE_URL}/rest/v1/Carves", headers=HEADERS)
    
    if not response.ok:
        print(f"Error fetching carves: {response.status_code}")
        return
    
    carves = response.json()
    print(f"Found {len(carves)} carves to update")
    
    updated_count = 0
    skipped_count = 0
    
    for carve in carves:
        old_snippet = carve.get('summary_snippet', '')
        new_snippet = generate_better_snippet(carve)
        
        # Only update if the snippet actually changed
        if old_snippet != new_snippet:
            update_response = requests.patch(
                f"{SUPABASE_URL}/rest/v1/Carves?id=eq.{carve['id']}",
                headers=HEADERS,
                json={"summary_snippet": new_snippet}
            )
            
            if update_response.ok:
                print(f"✅ Updated '{carve['title']}'")
                print(f"   OLD: {old_snippet[:100]}...")
                print(f"   NEW: {new_snippet[:100]}...")
                print()
                updated_count += 1
            else:
                print(f"❌ Failed to update '{carve['title']}': {update_response.status_code}")
        else:
            print(f"⏭️  Skipped '{carve['title']}' (no change needed)")
            skipped_count += 1
    
    print(f"\n🎉 COMPLETE!")
    print(f"Updated: {updated_count} carves")
    print(f"Skipped: {skipped_count} carves")

if __name__ == "__main__":
    update_all_carve_snippets()
