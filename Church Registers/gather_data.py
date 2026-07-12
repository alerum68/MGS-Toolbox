import os
import sys
import json
import time
import re
import math
from textwrap import dedent

from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types, errors

# 1. Load variables from .env
load_dotenv(override=True) 

# 2. Initialize the GenAI Client
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Configuration from .env
CONFIG = {
    "parish_name": os.getenv("PARISH_NAME"),
    "parish_location": os.getenv("PARISH_LOCATION"),
    "volume_title": os.getenv("VOLUME_TITLE"),
    "volume_num": os.getenv("VOLUME_NUM", ""),
    
    "api_budget": float(os.getenv("API_BUDGET", "5.00")),
    "cost_per_1m_in": float(os.getenv("COST_PER_1M_INPUT", "0.075")),
    "cost_per_1m_out": float(os.getenv("COST_PER_1M_OUTPUT", "0.30")),
}

MASTER_DB = os.getenv("MASTER_DB_NAME")
IMAGE_DIR = os.getenv("IMAGE_SOURCE_DIR")
MODEL_ID = os.getenv("MODEL_NAME")
DEBUG_FILE = sys.argv[1] if len(sys.argv) > 1 else None

with open("register_schema.json", "r") as f:
    SCHEMA = json.load(f)

# --- NEW: IMAGE OPTIMIZATION ---
def optimize_image(image_path, max_dimension=2048):
    """
    Downscales images client-side to drastically reduce token costs.
    2048px is the sweet spot: large enough to perfectly preserve 19th-century 
    cursive legibility, but small enough to reduce Gemini tile costs by up to 80%.
    """
    img = Image.open(image_path)
    # LANCZOS resampling maintains sharp edges for text readability
    img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return img

# --- NEW: STATIC CACHED INSTRUCTIONS ---
def get_cached_system_instruction():
    """Returns the static, heavy ruleset to be stored in the Context Cache."""
    return dedent("""
        You are an expert genealogist and translator specializing in 19th-century Catholic parish registers from Métis and French-Canadian communities in the Red River / northern Great Plains region. Extract the historical parish register sheet into JSON.

        RULES:
        INSTRUCTION PRECEDENCE: If instructions conflict, apply them in this order: 1. Preserve the historical record exactly. 2. Standardize only where explicitly permitted. 3. Never invent missing information.

        WORKFLOW (Mental Sandbox):
        Before generating JSON, you must process the record in this exact order:
        1. Transcribe the original text exactly (French/Latin).
        2. Translate the full text into English.
        3. Identify all participants and assign their specific roles.
        4. Standardize the names and format the dates.
        5. Populate the final structured JSON.

        1. SCHEMA & IDENTIFIERS
        - Output must match the provided JSON schema exactly. No extra fields. Use null when data isn't explicitly present.
        - Priest UIDs: For the priest ONLY, use the static sequence format: 1001 + [Volume] + 000000000 + [Sequence_Number] and store this 15-digit ID directly in the 'role_number' field.
          - Sequence: (1) Jean-Edouard Darveau (2) Georges-Antoine Belcourt (3) Albert Lacombe (4) Joseph Goiffon (5) Jean-Baptiste Marie Genin (6) Laurent Simonet (7) Louis Spitzelberger (8) Pierre Champagne (9) L. Lavigne.

        2. CHARACTERS
        - English letters/numbers only (A-Z, a-z, 0-9) in Fact fields, including "English translation". Strip all diacritics/accents.
        - Only "raw" fields, and "Original Transcription" should retain diacritics/accdents.
        - Margin numbers are load-bearing identifiers. Read each digit individually.

        3. NAMES & PRE-COMPUTATION VERIFICATION
        - BAPTISM SURNAME: For baptismal records where the child's surname is not explicitly written, you MUST populate the child's structured surname field with the Father's surname.
        - STANDARDIZED NAMES: Use the standardized historical spelling for all standard fields (std_given, std_surname). The exact, raw spelling from the document must ONLY be placed in the raw_given and raw_surname fields.
        - PRIEST PREFIX: You MUST add "Father" to the `prefix` field for the officiating priest.
        - Read the surname exactly as written. Do not standardize toward or favor any name merely because it seems more common.
        - If the reading remains ambiguous, do NOT guess. Use "[illegible]" and flag the participant as "[NEEDS REVIEW]".
        - Be careful to not include descriptive words in the name. (e.g. Late, Passed, Young, Old, etc). Keep any Titles or Honorifics (Father, Chief, Lord, etc)
        - NEVER invent a surname.
        - Trace each name token to its distinct word. Do not conflate roles.
        - Suffix: Set to "Jr" or "Sr" when the primary participant and their father share an identical standardized given name and surname.
        - Dit names: Main surname field = full surname + dit name. 'dit_name' field = ONLY the dit surname itself.

        4. FORMULAIC LANGUAGE CHECK
        - Standard clerical phrases must make grammatical sense.

        5. DATES
        - FORMATTING: All dates in structured JSON fields MUST be formatted as YYYY-MM-DD.
        - If there is no evidence of birthdate stated, leave the date field null. Infer if possible directly from text.
        - All dates will span either the 1700s or 1800s.

        6. LOCATIONS & RESIDENCE
        - Use the register's stated location exactly as written.
        - Fallback defaults (only if completely unstated in the register):
          - Baptism/Marriage/Burial event_place: "Assumption Parish, Pembina, Pembina County, North Dakota, USA"
          - Birth/Death: "Pembina, Pembina County, North Dakota, USA"
        - RESIDENCE (RESI): Only extract if EXPLICITLY stated.

        7. MISSING / ILLEGIBLE / INFERRED TEXT
        - RELIGION: Set to "Roman Catholic" for all participants unless explicitly stated otherwise.
        - Infer sex from role/given name if not stated.
        - Illegible text: Attempt reading at least 3 times. Use "[illegible]" if genuinely unreadable. NEVER guess silently.
        - STRIKETHROUGHS: Insert "[struck through: <best reading>]" inline in transcription.
        - Record rare short notes in transcription/translation as transcriber notes, not in fact fields. Mark abnormalities as "[NEED REVIEW]".

        8. RECORD CLASSIFICATION
        - record_type_code: 1=Baptism, 2=Marriage, 3=Burial.
        - record_id: Prefix with "S-" for Burials, "B-" for Baptisms, "M-" for Marriages.
        - record_number: Integer portion of margin number only.

        9. ROLES & RELATIONSHIPS
        - 1 = Primary (Baptism: Child, Burial: Deceased, Marriage: Groom)
        - 2 = Father (of Primary)
        - 3 = Mother (of Primary)
        - 4 = Spouse (Marriage: Bride, Burial: Spouse of deceased. Leave blank/unused for Baptisms)
        - 5 = Father (of Spouse)
        - 6 = Mother (of Spouse)
        - 7 = Godfather / Witness 1
        - 8 = Godmother / Witness 2
        - 9/0 = Other individuals

        10. TRANSCRIPTION & TRANSLATION
        - english_translation = full English translation. 
        - original_transcription = exact original French/Latin.
        - use English when filling in all structured facts fields. Use original_transcription just in the citation block.
        
        11. RECORDS SPANNING PAGE BOUNDARIES
        - Merge records logically, but NEVER invent or hallucinate missing text. 
        - If cut off, use "[illegible]". Mark these records as "[NEEDS REVIEW]".

        12. STRICT VISUAL FIDELITY
        - Read exactly what is written. Pay attention to standard abbreviations ("Chs" for Charles).
        - ANTI-CROSS-POLLINATION: NEVER carry over surnames from adjacent records.
    """)

# --- NEW: DYNAMIC USER PROMPT ---
def get_dynamic_prompt(file_name, volume, pages_str):
    """Generates only the dynamic metadata for the specific image being processed."""
    return dedent(f"""
        Metadata Context: File: {file_name}, Volume: {volume}, Pages: {pages_str}, Church: {CONFIG['parish_name']}, Location: {CONFIG['parish_location']}
    """)

def run_batch_process():
    if os.path.exists(MASTER_DB):
        with open(MASTER_DB, 'r', encoding='utf-8') as f:
            master_data = json.load(f)
            total_spent = master_data.get("total_spent", 0.0)
            total_pages_processed = master_data.get("total_pages_processed", 0)
    else:
        master_data = {
            "register_title": CONFIG['volume_title'], 
            "sheets": [],
            "total_spent": 0.0,
            "total_pages_processed": 0
        }
        total_spent = 0.0
        total_pages_processed = 0

    processed_files = {s['document_metadata']['file_name'] for s in master_data.get('sheets', [])}
    all_images = [f for f in os.listdir(IMAGE_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.tiff', '.tif'))]

    if DEBUG_FILE:
        if DEBUG_FILE not in all_images:
            print(f"[DEBUG MODE] '{DEBUG_FILE}' not found in {IMAGE_DIR}. Aborting.")
            return
        print(f"[DEBUG MODE] Processing ONLY '{DEBUG_FILE}' with thinking enabled. Nothing will be saved to {MASTER_DB}.\n")
        all_images = [DEBUG_FILE]

    total_files = len(all_images)

    # --- NEW: CREATE CONTEXT CACHE BEFORE LOOP ---
    active_cache_name = None
    if not DEBUG_FILE:
        print(f"Found {total_files} images in the source directory.")
        print(f"Creating Context Cache for System Instructions to reduce costs...")
        try:
            cache = client.caches.create(
                model=MODEL_ID,
                config=types.CreateCachedContentConfig(
                    system_instruction=get_cached_system_instruction(),
                    ttl="86400s", # 24 hours, ensuring it survives the longest batch run
                )
            )
            active_cache_name = cache.name
            print(f"Cache created successfully: {active_cache_name}\n")
        except Exception as e:
            print(f"Warning: Failed to create cache. Proceeding without it. Error: {e}\n")

    for index, filename in enumerate(all_images, start=1):
        if not DEBUG_FILE and filename in processed_files:
            print(f"[{index}/{total_files}] Skipping {filename} (already processed).")
            continue

        file_base = os.path.splitext(filename)[0]
        file_ext = os.path.splitext(filename)[1].upper().replace(".", "")
        if file_ext == "JPG":
            file_ext = "JPEG"

        print(f"[{index}/{total_files}] Processing {filename} with {MODEL_ID}...", end="", flush=True)
        pages_str = file_base.split('_')[-1]

        try:
            # Downscale image client-side to save tokens
            img = optimize_image(os.path.join(IMAGE_DIR, filename))
            prompt = get_dynamic_prompt(file_base, CONFIG['volume_num'], pages_str)

            if DEBUG_FILE:
                # Cache cannot be used with thinking mode enabled in current API limits, 
                # so we append the static instructions directly for debug runs.
                prompt = get_cached_system_instruction() + "\n\n" + prompt
                gen_config_kwargs = dict(
                    thinking_config=types.ThinkingConfig(include_thoughts=True),
                )
                prompt += (
                    "\n\nOUTPUT FORMAT: Respond with ONLY raw JSON matching this "
                    "schema, no markdown code fences, no commentary before or "
                    f"after:\n{json.dumps(SCHEMA)}"
                )
            else:
                gen_config_kwargs = dict(
                    response_mime_type="application/json",
                    response_schema=SCHEMA,
                )
                if active_cache_name:
                    gen_config_kwargs["cached_content"] = active_cache_name

            max_retries = 10
            attempts = 0
            success = False

            while attempts < max_retries and not success:
                try:
                    response = client.models.generate_content(
                        model=MODEL_ID,
                        contents=[prompt, img],
                        config=types.GenerateContentConfig(**gen_config_kwargs)
                    )

                    if DEBUG_FILE:
                        thought_parts = [
                            p.text for p in response.candidates[0].content.parts
                            if getattr(p, "thought", False)
                        ]
                        if thought_parts:
                            print("\n\n--- MODEL THINKING ---")
                            print("\n".join(thought_parts))
                            print("--- END THINKING ---\n")

                    raw_text = response.text.strip()
                    backticks = "`" * 3
                    if raw_text.startswith(backticks):
                        raw_text = re.sub(r"^" + backticks + r"(?:json)?\s*|\s*" + backticks + r"$", "", raw_text.strip())
                    
                    page_data = json.loads(raw_text)

                    usage = response.usage_metadata
                    if usage:
                        # 1. Extract all three token buckets
                        in_tokens = getattr(usage, 'prompt_token_count', 0)
                        out_tokens = getattr(usage, 'candidates_token_count', 0)
                        cached_tokens = getattr(usage, 'cached_content_token_count', 0)
                        
                        # 2. Calculate costs (assuming Cache is ~25% of standard input cost)
                        cost_cached = (cached_tokens / 1_000_000) * (CONFIG["cost_per_1m_in"] * 0.25)
                        cost_in = (in_tokens / 1_000_000) * CONFIG["cost_per_1m_in"]
                        cost_out = (out_tokens / 1_000_000) * CONFIG["cost_per_1m_out"]
                        
                        call_cost = cost_cached + cost_in + cost_out
                        total_spent += call_cost
                        total_pages_processed += 1
                        
                        avg_cost_per_page = total_spent / total_pages_processed if total_pages_processed > 0 else 0
                        load_dotenv(override=True)
                        live_budget = float(os.getenv("API_BUDGET", CONFIG["api_budget"]))
                        remaining_budget = max(0.0, CONFIG["api_budget"] - total_spent)
                        estimated_pages_left = math.floor(remaining_budget / avg_cost_per_page) if avg_cost_per_page > 0 else 0

                    for sheet in page_data.get("sheets", []):
                        if "document_metadata" not in sheet:
                            sheet["document_metadata"] = {}
                        sheet["document_metadata"]["file_name"] = filename
                        sheet["document_metadata"]["file_type"] = file_ext
                        sheet["document_metadata"]["volume"] = CONFIG['volume_num']

                    if DEBUG_FILE:
                        print("--- EXTRACTED JSON (not saved to master DB) ---")
                        print(json.dumps(page_data, indent=2, ensure_ascii=False))
                        if usage:
                            total_tokens = cached_tokens + in_tokens + out_tokens
                            print(f" DONE! ✓ (debug) | Call Cost: ${call_cost:.4f} | Total Spent: ${total_spent:.4f} | Remaining: ${remaining_budget:.2f}")
                            print(f"      Tokens -> Cached: {cached_tokens} | Input: {in_tokens} | Output/Think: {out_tokens} = Total: {total_tokens}")
                        else:
                            print(" DONE! ✓ (debug run)")
                    else:
                        master_data["sheets"].extend(page_data.get("sheets", []))
                        
                        master_data["total_spent"] = total_spent
                        master_data["total_pages_processed"] = total_pages_processed
                        
                        with open(MASTER_DB, 'w', encoding='utf-8') as f:
                            json.dump(master_data, f, indent=2, ensure_ascii=False)

                        if usage:
                            total_tokens = cached_tokens + in_tokens + out_tokens
                            print(f" DONE! ✓ | Cost: ${call_cost:.4f}")
                            print(f"      Tokens -> Cached: {cached_tokens} | Input: {in_tokens} | Output/Think: {out_tokens} = Total: {total_tokens}")
                            print(f"      Budget -> Total Spent: ${total_spent:.4f} | Est Pages Left: ~{estimated_pages_left}")
                        else:
                            print(" DONE! ✓")

                    success = True
                
                except json.JSONDecodeError as e:
                    print(f"\n   [!] Malformed JSON generated. Retrying... ({e})", end="", flush=True)
                    time.sleep(2)
                    attempts += 1

                except errors.ClientError as api_error:
                    error_msg = str(api_error)

                    if "PerDay" in error_msg:
                        print(f"\n\n[FATAL ERROR] Daily Quota Exhausted.")
                        print("Progress saved. Exiting script to prevent infinite crashing.")
                        return

                    elif "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                        match = re.search(r"retry in (\d+\.?\d*)s", error_msg)
                        if match:
                            wait_time = float(match.group(1)) + 1.5
                        else:
                            # Exponential backoff: 35s, 70s, 140s...
                            wait_time = 35.0 * (2 ** attempts) 
                        
                        print(f"\n   [!] Rate limit hit. Sleeping {wait_time:.2f}s (Attempt {attempts + 1})...", end="", flush=True)
                        time.sleep(wait_time)
                        attempts += 1
                        
                    elif "500" in error_msg or "503" in error_msg or "504" in error_msg:
                        print(f"\n   [!] Google Server Error ({error_msg[:30]}). Sleeping 5s...", end="", flush=True)
                        time.sleep(5)
                        attempts += 1

                    else:
                        print(f" API ERROR! ✗\nDetails: {error_msg}")
                        break

            if not success and attempts >= max_retries:
                print(f"\n[{index}/{total_files}] FAILED: {filename} exhausted all {max_retries} retries.")

        except Exception as e:
            print(f" LOCAL ERROR! ✗\nDetails: {e}")

if __name__ == "__main__":
    run_batch_process()