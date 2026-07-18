"""
Church Register to GEDCOM Converter Script.

This script parses structured JSON data extracted from historical 
Catholic parish registers and converts it into a standardized GEDCOM 5.5.1 
file, tailored for RootsMagic. Will attach witness records to each event.
"""

import calendar
import datetime
import hashlib
import json
import os
import re
from typing import Union

from dotenv import load_dotenv

load_dotenv(override=True)

# ==========================================
# GLOBALS & CONFIGURATION
# ==========================================
PROGRAM_DIR = os.getenv("PROGRAM_DIR", "")

def safe_path(base: str, *parts: str) -> str:
    """
    Safely joins paths, allowing absolute paths to override the base.
    """
    res = base
    for p in parts:
        if not p:
            continue
        if os.path.isabs(p):
            res = p
        else:
            res = os.path.join(res, p)
    return res

# Process priest sequence list
priest_list = os.getenv("PRIEST_NAMES", "").split(',')
PRIEST_SEQ = {name.strip(): str(i + 1)
    for i, name in enumerate(priest_list) if name.strip()
}

CONFIG = {
    "researcher": os.getenv("RESEARCHER", "Unknown"),
    "image_dir": safe_path(PROGRAM_DIR, os.getenv("IMAGE_DIR", "")),
    "master_db": safe_path(PROGRAM_DIR, os.getenv("JSON_DIR", ""),
        os.getenv("MASTER_DB_NAME", "church_register.json")
    ),
    "gedcom_out_path": safe_path(PROGRAM_DIR, os.getenv("GEDCOM_OUTPUT_PATH", "")),
    "gedcom_out_name": os.getenv("GEDCOM_OUTPUT_NAME", "Church_Register.ged"),
    "parish_file_name": os.getenv("PARISH_FILE_NAME", "Parish_Export"),
    "parish_name": os.getenv("PARISH_NAME", "Parish Name"),
    "parish_city": os.getenv("PARISH_CITY", "City"),
    "parish_state": os.getenv("PARISH_STATE", "State"), "parish_location": (f"{os.getenv('PARISH_CITY', 'City')}, "
                                                                            f"{os.getenv('PARISH_STATE', 'State')}").strip(
        ", "),
    "parish_name_short": os.getenv("PARISH_NAME_SHORT", "Parish"),
    "volume_title": os.getenv("VOLUME_TITLE", ""),
    "volume_num": os.getenv("VOLUME_NUM", ""),
    "register_name": os.getenv("REGISTER_NAME", ""),
    "call_number": os.getenv("CALL_NUMBER", ""),
    "repository": os.getenv("REPOSITORY", ""),
    "repository_loc": os.getenv("REPOSITORY_LOC", ""),
    "subm_address": os.getenv("SUBM_ADDRESS", ""),
    "mgs_group_url": os.getenv("MGS_GROUP_URL", ""),
    "ancestry_group_url": os.getenv("ANCESTRY_GROUP_URL", ""),
    "collection_url": os.getenv("COLLECTION_URL", ""),
    "collection_name": os.getenv("COLLECTION_NAME", ""),
    "root_source_id": os.getenv("ROOT_SOURCE_ID", "@S1@"),
    "register_source_id": os.getenv("REGISTER_SOURCE_ID", "1"),
    "default_location": os.getenv("DEFAULT_EVENT_LOCATION", ""),
    "transcription_header": os.getenv("TRANSCRIPTION_HEADER", "Original Transcription:"),
    "translation_header": os.getenv("TRANSLATION_HEADER", "English Translation:"),
    "role_clergy": os.getenv("ROLE_CLERGY", "Priest"),
    "clergy_honorific": os.getenv("CLERGY_HONORIFIC", "Father"),
    "role_default_witness": os.getenv("ROLE_DEFAULT_WITNESS", "Witness"),
    "org_name": os.getenv("ORG_NAME", "Michif Genealogical Society"),
    "software_name": os.getenv("SOFTWARE_NAME", "RootsMagic"),
    "software_vers": os.getenv("SOFTWARE_VERS", "11.0"),
    "copyright_start": os.getenv("COPYRIGHT_START", "2018"),
    "gedcom_note": os.getenv("GEDCOM_NOTE", "This file contains original historical translations and research."),
    "gedcom_conc": os.getenv("GEDCOM_CONC",
        "Please do not upload this raw GEDCOM to public trees without attribution."),
    "review_color": os.getenv("REVIEW_COLOR", "1")
}

# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def clean_val(val: Union[str, int, float, None]) -> str:
    """
    Scrubs literal nulls, normalizes spaces, and clears invisible characters.
    Properly type-hinted to silence IDE '__str__' warnings.
    """
    if val is None or val == "":
        return ""
    val_str = re.sub(r' +', ' ',
        str(val).replace('\xa0', ' ').replace('\u200b', '').replace('\u202f', ' ')
    ).strip()
    return "" if val_str.lower() in ["null", "none", ""] else val_str

def extract_volume(sheet: dict) -> str:
    """
    Aggressively extracts volume from metadata or sheet root,
    falling back to global CONFIG.
    """
    meta = sheet.get('document_metadata', {})
    if isinstance(meta, dict):
        for k, v in meta.items():
            if k.lower() == 'volume' and clean_val(v):
                return clean_val(v)
            
    for k, v in sheet.items():
        if k.lower() == 'volume' and clean_val(v):
            return clean_val(v)
        
    return clean_val(CONFIG.get('volume_num'))

def get_dynamic_source_id(vol_val: str) -> str:
    """Constructs a dynamic GEDCOM Source ID based on the volume."""
    # Used f-string instead of str() to avoid type checker warnings on Any
    base_id = re.sub(r'\D', '', f"{CONFIG.get('register_source_id', '1')}")
    if base_id.endswith('001') and len(base_id) > 1:
        base_id = base_id[:-3]
    vol_digits = re.sub(r'\D', '', f"{vol_val or '1'}") or '1'
    return f"@S{base_id or '1'}{vol_digits.zfill(3)}@"

def format_gedcom_date(date_str: str) -> str:
    """
    Converts standard YYYY-MM-DD or YYYY-MM dates into GEDCOM standard format.
    Handles genealogical prefixes (BEF, AFT, ABT, CAL, EST).
    """
    date_str = clean_val(date_str)
    if not date_str:
        return ""
    
    prefix = ""
    valid_prefixes = ("BEF ", "AFT ", "ABT ", "CAL ", "EST ")
    if date_str.upper().startswith(valid_prefixes):
        prefix = date_str[:4].upper()
        date_str = date_str[4:].strip()
        
    try:
        parts = date_str.split('-')
        if len(parts) == 3 and len(parts[0]) == 4:
            dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            return f"{prefix}{dt.day} {dt.strftime('%b').upper()} {dt.year}"
        elif len(parts) == 2 and len(parts[0]) == 4:
            dt = datetime.datetime.strptime(date_str, "%Y-%m")
            return f"{prefix}{dt.strftime('%b').upper()} {dt.year}"
    except ValueError:
        pass
    
    return prefix + date_str

def get_proof_status(date_str: str) -> str:
    """Evaluates date prefixes to assign proposed or proven proof status tags."""
    if date_str:
        upper_date = f"{date_str}".upper().strip()
        if upper_date.startswith(("BEF", "ABT", "AFT", "EST", "CAL")):
            return "proposed"
    return "proven"

def estimate_birth_from_age(event_date: str, age_str: str) -> str:
    """
    Calculates an estimated birthdate based on event date precision
    and detailed age metadata (years, months, weeks, days).
    """
    if not event_date or not age_str:
        return ""
    
    try:
        # Determine precision of event date safely handling Any type
        if len(f"{event_date}") == 10:
            dt = datetime.datetime.strptime(f"{event_date}"[:10], "%Y-%m-%d")
            precision = "day"
        elif len(f"{event_date}") == 7:
            dt = datetime.datetime.strptime(f"{event_date}"[:7], "%Y-%m")
            precision = "month"
        else:
            dt = datetime.datetime.strptime(f"{event_date}"[:4], "%Y")
            precision = "year"
    except ValueError:
        return ""
    
    age_str = f"{age_str}".strip().lower()
    years = sum(map(int, re.findall(r'(\d+)\s*(?:year|yr|y\b)', age_str)))
    months = sum(map(int, re.findall(r'(\d+)\s*(?:month|mo|m\b)', age_str)))
    weeks = sum(map(int, re.findall(r'(\d+)\s*(?:week|wk|w\b)', age_str)))
    days = sum(map(int, re.findall(r'(\d+)\s*(?:day|d\b)', age_str)))
    
    if not any([years, months, weeks, days]):
        match = re.search(r'\d+', age_str)
        if match:
            years = int(match.group())
        else:
            return ""

    if precision == "year" and (months or weeks or days):
        return f"CAL {dt.year - years}" if years else f"ABT {dt.year}"
        
    if precision == "month" and (weeks or days):
        if not (months or years):
            return f"ABT {dt.strftime('%Y-%m')}"
        weeks, days = 0, 0

    if weeks or days:
        dt -= datetime.timedelta(weeks=weeks, days=days)
        
    if months or years:
        total_months = dt.month - 1 - months
        new_year = dt.year - years + (total_months // 12)
        new_month = (total_months % 12) + 1
        last_day_of_month = calendar.monthrange(new_year, new_month)[1]
        dt = dt.replace(year=new_year, month=new_month, day=min(dt.day, last_day_of_month))

    if precision == "day" and any([months, weeks, days]):
        return f"CAL {dt.strftime('%Y-%m-%d')}"
    elif precision in ["day", "month"] and months:
        return f"CAL {dt.strftime('%Y-%m')}"
        
    return f"CAL {dt.year}"

def wrap_text(text: str, tag: str = "5 CONT") -> str:
    """
    Wraps text content natively for GEDCOM, slicing CONC tags
    mid-word to prevent layout artifacts.
    """
    if not text:
        return ""
    lines = clean_val(text).split('\n')
    wrapped = []
    
    for line in lines:
        if not line:
            continue
        wrapped.append(f"{tag} {line[:75]}")
        for i in range(75, len(line), 75):
            wrapped.append(f"{tag[:1]} CONC {line[i:i+75]}")
            
    return "\n".join(wrapped)

def get_by_role(rec: dict, role_num: str) -> dict:
    """Finds and extracts the first participant profile for a specific role."""
    for p in rec.get('participants', []):
        if f"{p.get('role_number', '')}" == f"{role_num}":
            return p
    return None

def get_role_name(part: dict, is_marriage: bool, event_tag: str) -> str:
    """Maps structural register metadata role digits into human-readable strings."""
    if part.get('is_priest'):
        return CONFIG['role_clergy']
    
    role = f"{part.get('role_number', '0')}"
    if is_marriage:
        mapping = {"2": "Father of Groom", "3": "Mother of Groom", "4": "Bride", "5": "Father of Bride",
            "6": "Mother of Bride", "7": "Witness",
            "8": "Witness"
        }
        return mapping.get(role, CONFIG['role_default_witness'])
        
    base_mapping = {"2": "Father", "3": "Mother", "7": "Godfather" if event_tag in ["BAPM", "CHR"] else "Witness",
        "8": "Godmother" if event_tag in ["BAPM", "CHR"] else "Witness"
    }
    return base_mapping.get(role, CONFIG['role_default_witness'])

def evaluate_task_priority(task_note: str) -> tuple:
    """
    Evaluates task notes for keywords to assign a priority,
    color code, and dynamic folder name.
    """
    task_note_lower = f"{task_note}".lower()

    # (Keywords, Priority Level, RM Color Code, Target Folder Name)
    # noinspection SpellCheckingInspection
    rules = [
        (["conflict", "error", "mismatch", "discrepancy", "inconsistent", "contradict", "wrong"],
            1, "1", "Data Conflicts & Errors"
        ),
        (["illegible", "unreadable", "hard to read", "faded", "damaged", "torn", "blot", "margin", "ink"],
            1, "1", "Legibility & Record Condition"
        ),
        (["surname", "given name", "blank name", "no name", "unknown name", "alias", "dit name", "spelling",
          "identity"],
            1, "1", "Name & Identity Issues"
        ),
        (["parent", "father", "mother", "sponsor", "godparent", "witness", "spouse", "bride", "groom", "relationship",
          "unknown parents"],
            2, "2", "Relationship & Participant Issues"
        ),
        (["translate", "translation", "latin", "french", "language"],
            2, "2", "Translation Needed"
        ),
        (["date", "year", "month", "day", "invalid", "chronology", "sequence", "estimated", "calculated", "age",
          "born"],
            3, "3", "Date & Chronology Issues"
        )
    ]

    for keywords, priority, color, folder in rules:
        if any(kw in task_note_lower for kw in keywords):
            return priority, color, folder

    return 3, CONFIG.get('review_color', '1'), "General Review"

# ==========================================
# ID GENERATORS
# ==========================================
def generate_uid(rec: dict, part: dict, vol: str) -> str:
    """
    Generates a deterministic ID based on the physical location of the record
    to prevent typos from causing collisions.
    """
    src_id = re.sub(r'\D', '', get_dynamic_source_id(vol))
    vol_str = f"{vol or '1'}".zfill(1)
    
    if part.get('is_priest'):
        if part.get('role_number') and len(f"{part['role_number']}") > 10:
            return f"{part['role_number']}"
        
        std_g = clean_val(part.get('std_given'))
        std_s = clean_val(part.get('std_surname'))
        std_name = f"{std_g} {std_s}".strip()
        seq = PRIEST_SEQ.get(std_name, "0")
        return f"{src_id}{vol_str}000000000{seq}"
    
    page_str = clean_val(rec.get('page'))
    rec_id = clean_val(rec.get('record_id'))
    role = f"{part.get('role_number', '0')}"
    
    participants = rec.get('participants', [])
    same_role = [i for i, p in enumerate(participants) if f"{p.get('role_number', '0')}" == role]
    pos = next((i for i, p in enumerate(participants) if p is part), None)
    occ = same_role.index(pos) if pos in same_role else 0

    unique_string = f"indi_{vol}_{page_str}_{rec_id}_{role}_{occ}"
    numeric_id = int(hashlib.md5(unique_string.encode('utf-8')).hexdigest(), 16) % (10**10)
    return f"{src_id}{numeric_id:010d}"

def generate_media_uid(meta: dict, vol: str) -> str:
    """Generates a deterministic 10-digit numerical UID for a media file."""
    file_name = clean_val(meta.get('file_name'))
    image_dir = clean_val(CONFIG.get('image_dir', ''))
    
    if file_name:
        if image_dir:
            dir_base = os.path.basename(os.path.normpath(image_dir))
            unique_string = f"{dir_base}\\{file_name}"
        else:
            unique_string = file_name
    else:
        unique_string = f"vol_{vol}_pages_{meta.get('pages')}"
        
    numeric_id = int(hashlib.md5(unique_string.encode('utf-8')).hexdigest(), 16) % (10**10)
    return f"M{numeric_id:010d}"

def generate_fam_uid(rec: dict, vol: str) -> str:
    """Generates a deterministic Family ID to match the INDI logic."""
    src_id = re.sub(r'\D', '', get_dynamic_source_id(vol))
    page_str = clean_val(rec.get('page'))
    rec_id = clean_val(rec.get('record_id'))
    
    unique_string = f"fam_{vol}_{page_str}_{rec_id}"
    numeric_id = int(hashlib.md5(unique_string.encode('utf-8')).hexdigest(), 16) % (10**10)
    return f"{src_id}{numeric_id:010d}"

# ==========================================
# GEDCOM BLOCK BUILDERS
# ==========================================
def build_citation_block(rec: dict, part: dict, tag_name: str, vol: str, media_uid: str,
        proof_status: str = "proven") -> str:
    """Constructs the standard source citation block for an event."""
    std_g = clean_val(part.get('std_given'))
    std_s = clean_val(part.get('std_surname'))
    page = clean_val(rec.get('page')) or 'X'
    rec_id = clean_val(rec.get('record_id')) or 'Unknown'
    year = clean_val(rec.get('year')) or 'Unknown'
    
    vol_clause = f"Vol. {vol}, " if vol else ""
    
    block = [
        f"2 _PROOF {proof_status}",
        f"2 SOUR {get_dynamic_source_id(vol)}",
        f"3 NAME {std_s}, {std_g}, {vol_clause}Page {page}, {rec_id}, {year}",
        f"3 _TITL {std_s}, {std_g}, {tag_name}, {year}",
        f"3 PAGE Page {page}, Record {rec_id}",
        "3 DATA",
        f"4 TEXT {CONFIG['translation_header']}"
    ]
    
    trans_text = wrap_text(rec.get('english_translation'), '5 CONT')
    if trans_text:
        block.append(trans_text)
        
    block.append(f"3 NOTE {CONFIG['transcription_header']}")
    
    orig_text = wrap_text(rec.get('original_transcription'), '4 CONT')
    if orig_text:
        block.append(orig_text)
    
    block.extend([
        f"3 REFN {clean_val(rec.get('record_number')) or rec_id}",
        "3 _TMPLT", "4 FIELD", "5 NAME ItemOfInterest",
        f"5 VALUE {f'{std_g} {std_s}'.strip()}", "4 FIELD", "5 NAME Page",
        f"5 VALUE Page {page}", "4 FIELD", "5 NAME DetailRef",
        f"5 VALUE {rec_id}",
        "3 QUAY 3", "3 _QUAL", "4 _SOUR O", "4 _INFO P",
        "4 _EVID D"
    ])
    
    if media_uid:
        block.append(f"3 OBJE @{media_uid}@")
        
    return "\n".join(block)


def build_individual(uid: str, rec: dict, part: dict, vol: str, media_uid: str, gedcom_date: str,
        any_part_review: bool) -> tuple:
    """
    Builds a single individual's INDI block, along with their Task block
    if flagged for review.
    """
    # Safe handling of Any types using f-strings for conditionals
    is_marriage = f"{rec.get('record_type_code', '')}" == "2"
    rec_num_check = clean_val(rec.get('record_number')) or clean_val(rec.get('record_id'))
    is_christening = f"{rec_num_check}".upper().startswith('C')
    is_baptism = f"{rec.get('record_type_code', '')}" == "1" and not is_christening
    is_bap_or_chr = is_baptism or is_christening
    
    is_primary = f"{part.get('role_number', '')}" == "1"
    is_parent = f"{part.get('role_number', '')}" in ["2", "3", "5", "6"]
    is_deceased = bool(part.get('deceased')) or bool(part.get('is_deceased'))
    
    std_g = clean_val(part.get('std_given'))
    std_s = clean_val(part.get('std_surname'))
    dit_name = re.sub(r'(?i)^dit\s+', '', clean_val(part.get('dit_name'))).strip()
    
    sex = clean_val(part.get('sex'))[:1]
    race = clean_val(part.get('race'))
    religion = clean_val(part.get('religion'))
    occu = clean_val(part.get('occupation'))
    resi = clean_val(part.get('residence'))
    
    age = clean_val(part.get('age'))
    raw_event_date = clean_val(rec.get('event_date'))
    raw_b = clean_val(part.get('birth_date'))
    raw_d = clean_val(part.get('death_date'))

    # Date heuristics
    if not raw_b and age and raw_event_date:
        raw_b = estimate_birth_from_age(raw_event_date, age)
        
    if raw_event_date:
        if is_bap_or_chr and is_primary:
            raw_b = raw_b or f"BEF {raw_event_date}"
        elif (not is_bap_or_chr and not is_marriage and is_primary) or (is_parent and is_deceased):
            raw_d = raw_d or f"BEF {raw_event_date}"
        
        if not is_bap_or_chr and raw_b and not f"{raw_b}".upper().startswith("CAL "):
            if (f"{raw_event_date}"[:4] in raw_b and any(
                m in f"{raw_b}".upper() for m in ["BEF", "ABT", "EST", "CAL"])):
                raw_b = ""

    b_date = format_gedcom_date(raw_b)
    d_date = format_gedcom_date(raw_d)
    b_place = clean_val(part.get('birth_place'))
    d_place = clean_val(part.get('death_place'))

    indi = [f"0 @I{uid}@ INDI", f"1 _UID {uid}", f"1 REFN {uid}"]
    task_block, needs_review, folder_name = None, False, None

    # Handle Tasks / Review Flags
    needs_primary_review = rec.get('review', False) and not any_part_review and is_primary
    if part.get('review', False) or needs_primary_review:
        needs_review = True
        reasons = [r for r in [clean_val(part.get('review_reason')), clean_val(rec.get('review_reason'))] if r]
        task_note = "; ".join(reasons) if reasons else "Review requested during extraction."
        
        priority, color_code, folder_name = evaluate_task_priority(task_note)
        
        indi.extend([f"1 _COLOR {color_code}", f"1 _TASK @T{uid}@"])
        
        tag_for_cit = ("CHR" if is_christening else ("BAPM" if is_baptism else ("MARR" if is_marriage else "Event")))
        raw_cit = build_citation_block(rec, part, f"Review {tag_for_cit}", vol, media_uid).split('\n')
        
        task_citation = []
        for line in raw_cit:
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[0].isdigit():
                lvl = int(parts[0])
                if lvl == 2 and parts[1].startswith("_PROOF"):
                    continue
                task_citation.append(f"{lvl - 1} {parts[1]}")
            else:
                task_citation.append(line)

        weblink = []
        if clean_val(CONFIG.get('collection_url')):
            weblink.extend([
                "1 _WEBTAG",
                f"2 NAME {CONFIG.get('collection_name', 'Collection Link')}",
                f"2 URL {CONFIG.get('collection_url')}"
            ])

        task_lines = [
            f"0 @T{uid}@ _TASK", f"1 DESC {std_s or '[No Surname]'}, {std_g or '[No Given Name]'} "
                                 f"({clean_val(rec.get('record_id')) or 'Unknown'}): {task_note}", f"1 REFN {uid}",
            f"1 _LINK @I{uid}@", "1 TYPE 2", f"1 DATE {gedcom_date}",
            f"1 _LDATE {gedcom_date}", f"1 NOTE {task_note}", "1 STAT NEW", f"1 PRTY {priority}",
            f"1 _COLOR {color_code}"
        ]
        
        task_lines.extend(weblink)
        task_lines.extend(task_citation)
        if media_uid:
            task_lines.extend([f"1 OBJE @{media_uid}@"])
        task_block = "\n".join(task_lines)

    # Process Dit Names
    std_s_base = std_s
    if dit_name:
        if re.search(r'\s+dit\s+', std_s, flags=re.IGNORECASE):
            std_s_base = re.split(r'\s+dit\s+', std_s, flags=re.IGNORECASE)[0]
        else:
            std_s_base = std_s.replace(dit_name, "")
    std_s_base = re.sub(r'(?i)\s+dit\b', '', std_s_base).strip()
    
    indi.append(f"1 NAME {std_g} /{std_s_base}/")
    if clean_val(part.get('prefix')):
        indi.append(f"2 NPFX {clean_val(part.get('prefix'))}")
    elif part.get('is_priest'):
        indi.append(f"2 NPFX {CONFIG['clergy_honorific']}")
        
    if clean_val(part.get('suffix')):
        indi.append(f"2 NSFX {clean_val(part.get('suffix'))}")
    
    indi.extend([f"1 SOUR {CONFIG['root_source_id']}", f"2 NAME Researcher: {CONFIG['researcher']}",
        f"2 _TITL Researcher: {CONFIG['researcher']}"
    ])

    if dit_name:
        indi.extend([f"1 NAME {std_g} /{std_s_base} dit {dit_name}/", f"1 NAME {std_g} /{dit_name}/",
            f"1 EVEN {dit_name}", "2 TYPE dit Name",
            build_citation_block(rec, part, 'dit Name', vol, media_uid)
        ])

    if sex:
        indi.append(f"1 SEX {sex}")
    if race:
        indi.append(f"1 _RACE {race}")
    if religion:
        indi.extend([f"1 RELI {religion}",
            build_citation_block(rec, part, "RELI", vol, media_uid)
        ])
    
    final_occu = CONFIG['role_clergy'] if part.get('is_priest') else occu
    if final_occu:
        indi.extend([f"1 OCCU {final_occu}",
            build_citation_block(rec, part, "OCCU", vol, media_uid)
        ])
    if resi:
        indi.extend([f"1 RESI\n2 PLAC {resi}",
            build_citation_block(rec, part, "RESI", vol, media_uid)
        ])

    if b_date or b_place:
        indi.append("1 BIRT")
        if b_date:
            indi.append(f"2 DATE {b_date}")
        if b_place:
            indi.append(f"2 PLAC {b_place}")
        indi.append(build_citation_block(rec, part, "BIRT", vol, media_uid, get_proof_status(b_date)))

    if d_date or d_place:
        indi.append("1 DEAT")
        if d_date:
            indi.append(f"2 DATE {d_date}")
        if d_place:
            indi.append(f"2 PLAC {d_place}")
        if age:
            indi.append(f"2 AGE {age}")
        indi.append(build_citation_block(rec, part, "DEAT", vol, media_uid, get_proof_status(d_date)))

    # Link events based on primary role
    if is_primary and not is_marriage:
        tag = "CHR" if is_christening else ("BAPM" if is_baptism else "BURI")
        indi.extend([f"1 {tag}", f"2 DATE {format_gedcom_date(raw_event_date)}" if raw_event_date else "",
            f"2 PLAC {clean_val(rec.get('event_place')) or CONFIG['default_location']}"
        ])
        if age:
            indi.append(f"2 AGE {age}")
        
        for p in rec.get('participants', []):
            if f"{p.get('role_number', '')}" != "1":
                indi.append(
                    f"2 _SHAR @I{generate_uid(rec, p, vol)}@\n"
                    f"3 ROLE {get_role_name(p, False, tag)}"
                )
        indi.append(build_citation_block(rec, part, tag, vol, media_uid, get_proof_status(raw_event_date)))

    # Link families
    if get_by_role(rec, "1"):
        f_uid = generate_fam_uid(rec, vol)
        r_num = f"{part.get('role_number', '')}"
        
        has_g_parents = is_marriage and any(
            f"{p.get('role_number', '')}" in ["2", "3"] for p in rec.get('participants', []))
        has_b_parents = is_marriage and any(
            f"{p.get('role_number', '')}" in ["5", "6"] for p in rec.get('participants', []))

        if is_marriage:
            if r_num == "1":
                indi.append(f"1 FAMS @F{f_uid}@")
                if has_g_parents:
                    indi.append(f"1 FAMC @F{f_uid}G@")
            elif r_num == "4":
                indi.append(f"1 FAMS @F{f_uid}@")
                if has_b_parents:
                    indi.append(f"1 FAMC @F{f_uid}B@")
            elif r_num in ["2", "3"]:
                indi.append(f"1 FAMS @F{f_uid}G@")
            elif r_num in ["5", "6"]:
                indi.append(f"1 FAMS @F{f_uid}B@")
        else:
            if r_num == "1":
                indi.append(f"1 FAMC @F{f_uid}@")
            elif r_num in ["2", "3"]:
                indi.append(f"1 FAMS @F{f_uid}@")

    return [line for line in indi if line], task_block, needs_review, folder_name

def build_family(rec: dict, vol: str, media_uid: str) -> list:
    """Builds FAM records for the primary couple and their parents if present."""
    primary = get_by_role(rec, "1")
    if not primary:
        return []
    
    fams = []
    fam_uid = generate_fam_uid(rec, vol)
    fam_tag = [f"0 @F{fam_uid}@ FAM"]
    
    def add_parent_family(dad_role: str, mom_role: str, suffix: str, child: dict) -> None:
        """Helper to deduplicate parent family record generation."""
        p_dad = get_by_role(rec, dad_role)
        p_mom = get_by_role(rec, mom_role)
        if p_dad or p_mom:
            p_fam = [f"0 @F{fam_uid}{suffix}@ FAM"]
            if p_dad:
                p_fam.append(f"1 HUSB @I{generate_uid(rec, p_dad, vol)}@")
            if p_mom:
                p_fam.append(f"1 WIFE @I{generate_uid(rec, p_mom, vol)}@")
            p_fam.append(f"1 CHIL @I{generate_uid(rec, child, vol)}@")
            fams.append("\n".join(p_fam))
    
    if f"{rec.get('record_type_code', '')}" == "2":
        bride = get_by_role(rec, "4")
        if primary:
            fam_tag.append(f"1 HUSB @I{generate_uid(rec, primary, vol)}@")
        if bride:
            fam_tag.append(f"1 WIFE @I{generate_uid(rec, bride, vol)}@")
        
        event_date = format_gedcom_date(clean_val(rec.get('event_date')))
        fam_tag.extend(["1 MARR", f"2 DATE {event_date}" if event_date else "",
            f"2 PLAC {clean_val(rec.get('event_place')) or CONFIG['default_location']}"
        ])
        
        if clean_val(primary.get('age')):
            fam_tag.append(f"2 HUSB\n3 AGE {clean_val(primary.get('age'))}")
        if bride and clean_val(bride.get('age')):
            fam_tag.append(f"2 WIFE\n3 AGE {clean_val(bride.get('age'))}")

        for p in rec.get('participants', []):
            if f"{p.get('role_number', '')}" not in ["1", "4"]:
                fam_tag.append(
                    f"2 _SHAR @I{generate_uid(rec, p, vol)}@\n"
                    f"3 ROLE {get_role_name(p, True, 'MARR')}"
                )
        
        fam_tag.append(build_citation_block(rec, primary, "MARR", vol, media_uid, get_proof_status(event_date)))
        
        # Utilize the helper to construct parent families for both Bride and Groom
        add_parent_family("2", "3", "G", primary)
        if bride:
            add_parent_family("5", "6", "B", bride)
    
    else:
        dad = get_by_role(rec, "2")
        mom = get_by_role(rec, "3")
        if dad:
            fam_tag.append(f"1 HUSB @I{generate_uid(rec, dad, vol)}@")
        if mom:
            fam_tag.append(f"1 WIFE @I{generate_uid(rec, mom, vol)}@")
        fam_tag.append(f"1 CHIL @I{generate_uid(rec, primary, vol)}@")
        
    fams.insert(0, "\n".join([line for line in fam_tag if line]))
    return fams

def get_source_root() -> list:
    """Generates the static organization-level source record."""
    return [
        f"0 {CONFIG['root_source_id']} SOUR",
        f"1 TITL {CONFIG['org_name']}", "1 AUTH",
        f"1 PUBL Researcher: {CONFIG['researcher']}.", "1 _WEBTAG", "2 NAME Facebook Group",
        f"2 URL {CONFIG['mgs_group_url']}", "1 _WEBTAG", "2 NAME Ancestry Group",
        f"2 URL {CONFIG['ancestry_group_url']}"
    ]

def get_volume_sources(volumes_used: set) -> list:
    """Generates register-specific source records dynamically based on volumes used."""
    loc_full = CONFIG['parish_location']
    sour_lines = []
    
    for vol in sorted(list(volumes_used)):
        s_id = get_dynamic_source_id(vol)
        v_clause = f", Volume {vol}" if vol else ""
        v_title = f"{CONFIG['volume_title']}{v_clause}"
        
        block = [f"0 {s_id} SOUR",
            f"1 ABBR {CONFIG['parish_name']} {v_title}",
            f"1 REFN {s_id.replace('@S', '').replace('@', '')}",
            f"1 TITL {CONFIG['parish_name']}, , {CONFIG['register_name']}{v_clause} ; "
            f"{v_title}, {CONFIG['parish_name']}, {loc_full}.",
            f"1 _SUBQ {CONFIG['parish_name']} - {loc_full}.", f"1 _BIBL {CONFIG['parish_name']}. {v_title}. "
                                                              f"{CONFIG['parish_name']}, {loc_full}.", "1 _TMPLT",
            "2 TID 355",
            "2 FIELD", "3 NAME Church_Author", f"3 VALUE {CONFIG['parish_name']}", "2 FIELD", "3 NAME Title",
            f"3 VALUE {CONFIG['volume_title']}",
            "2 FIELD", "3 NAME Location", f"3 VALUE {loc_full}", "2 FIELD", "3 NAME ChurchLoc",
            f"3 VALUE {CONFIG['parish_name']}",
            "2 FIELD", "3 NAME RegisterName", f"3 VALUE {CONFIG['register_name']}"
        ]
        
        if vol:
            block.extend(["2 FIELD", "3 NAME Volume", f"3 VALUE Volume {vol}"])
            
        block.extend(["2 FIELD", "3 NAME ArchRegNo", f"3 VALUE {CONFIG['call_number']}",
            "2 FIELD", "3 NAME Repository", f"3 VALUE {CONFIG['repository']}", "2 FIELD", "3 NAME RepositoryLoc",
            f"3 VALUE {CONFIG['repository_loc']}", "1 _WEBTAG", f"2 NAME {CONFIG['collection_name']}",
            f"2 URL {CONFIG['collection_url']}"
        ])
        sour_lines.extend(block)
        
    return sour_lines

def build_gedcom(json_data: dict) -> str:
    """
    Main builder orchestrating the conversion from the loaded JSON
    to a raw GEDCOM string syntax.
    """
    now = datetime.datetime.now()
    gedcom_date = now.strftime('%d %b %Y').upper()
    
    ged = [
        "0 HEAD",
        f"1 FILE {CONFIG['parish_file_name']}.ged",
        f"1 SOUR {CONFIG['software_name']}",
        f"2 VERS {CONFIG['software_vers']}",
        f"2 NAME {CONFIG['software_name']}",
        f"2 CORP {CONFIG['software_name']}, Inc.",
        f"1 DEST {CONFIG['software_name']}",
        f"1 DATE {gedcom_date}",
        f"2 TIME {now.strftime('%H:%M:%S')}",
        "1 SUBM @SUBM1@", f"1 COPR Copyright (c) {CONFIG['copyright_start']}-{now.year} "
                          f"{CONFIG['org_name']}. All rights reserved.",
        f"1 NOTE {CONFIG['gedcom_note']}",
        f"2 CONC {CONFIG['gedcom_conc']}",
        "1 GEDC",
        "2 VERS 5.5.1",
        "2 FORM LINEAGE-LINKED",
        "1 CHAR UTF-8",
        "0 @SUBM1@ SUBM",
        f"1 NAME {CONFIG['researcher']}",
        f"2 ADDR {CONFIG['subm_address']}",
        f"1 NOTE {CONFIG['org_name']}"
    ]

    fam_recs, task_recs, media_recs = [], [], []
    printed_indis, printed_media, vols_used = set(), set(), set()
    folder_tasks = {}
    review_count = 0

    # Process all sheets and records
    for sheet in json_data.get('sheets', []):
        meta = sheet.get('document_metadata', {}) or {}
        vol = extract_volume(sheet)
        vols_used.add(vol)
        
        media_uid = generate_media_uid(meta, vol)
        file_name = clean_val(meta.get('file_name'))
        media_title = (f"{CONFIG['parish_name_short']} - Vol {vol or 'Unknown'} - "
                       f"Page {clean_val(meta.get('pages')) or 'X'}")
        
        # Build media object if new
        if media_uid not in printed_media:
            file_path = os.path.join(CONFIG.get('image_dir', ''), file_name)
            form_type = clean_val(meta.get('file_type', 'jpg')).lower()
            if form_type == 'jpeg':
                form_type = 'jpg'
            
            media_block = [f"0 @{media_uid}@ OBJE", f"1 FILE {file_path}",
                f"2 FORM {form_type}",
                f"2 TITL {media_title}"
            ]
            media_recs.append("\n".join(media_block))
            printed_media.add(media_uid)

        # Build Individuals and Families
        for rec in sheet.get('records', []):
            any_review = any(p.get('review') for p in rec.get('participants', []))
            
            for part in rec.get('participants', []):
                uid = generate_uid(rec, part, vol)
                if uid in printed_indis:
                    continue
                printed_indis.add(uid)
                
                indi_block, t_block, flagged, folder = build_individual(uid, rec, part, vol, media_uid, gedcom_date,
                    any_review
                )
                
                ged.extend(indi_block)
                if t_block and folder:
                    task_recs.append(t_block)
                    folder_tasks.setdefault(folder, []).append(f"1 _TASK @T{uid}@")
                if flagged:
                    review_count += 1
                
            fam_recs.extend(build_family(rec, vol, media_uid))

    # Compile the final GEDCOM file layout
    ged.extend(fam_recs)
    ged.extend(task_recs)
    
    for folder, tasks in folder_tasks.items():
        ged.append(f"0 _FOLDER {folder}")
        ged.extend(tasks)
        
    ged.extend(media_recs)
    ged.extend(get_source_root())
    ged.extend(get_volume_sources(vols_used))
    ged.append("0 TRLR")
    
    if review_count > 0:
        print(f"Flagged {review_count} individual(s) with priority-based "
              f"Tasks and Folders.")
        
    return "\n".join(ged)

if __name__ == "__main__":
    if os.path.exists(CONFIG['master_db']):
        with open(CONFIG['master_db'], 'r', encoding='utf-8') as f:
            gedcom_data = build_gedcom(json.load(f))
            
        out_dir = CONFIG.get('gedcom_out_path', '')
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        
        final_path = os.path.join(out_dir, CONFIG.get('gedcom_out_name', 'Assumption_Register.ged'))
        
        with open(final_path, 'w', encoding='utf-8') as f:
            f.write(gedcom_data)
            
        print(f"Successfully generated {final_path}")
    else:
        print(f"Error: {CONFIG['master_db']} not found. Ensure the path is correct.")