import os
import pandas as pd
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Force override system environment variables with the .env file contents
load_dotenv(override=True)

# ==========================================
# GLOBAL VARIABLES
# ==========================================
ORG_NAME = os.getenv("ORG_NAME")
RESEARCHER = os.getenv("RESEARCHER")

CENSUS_YEAR = int(os.getenv("CENSUS_YEAR"))
STATE = os.getenv("STATE")
COUNTY = os.getenv("COUNTY")
TOWNSHIP = os.getenv("TOWNSHIP")
FILM_NUMBER = os.getenv("FILM_NUMBER")
ROLL_NUMBER = os.getenv("ROLL_NUMBER")
CSV_FILE = os.getenv("CSV_FILE")

ANCESTRY_START_RECORD_ID = int(os.getenv("ANCESTRY_START_RECORD_ID"))
APID_DB = os.getenv("APID_DB", "")
ANCESTRY_IMAGE_BASE_ID = os.getenv("ANCESTRY_IMAGE_BASE_ID")

# Society Group Links & IDs
MGS_GROUP_URL = os.getenv("MGS_GROUP_URL")
ANCESTRY_GROUP_URL = os.getenv("ANCESTRY_GROUP_URL")
ROOT_SOURCE_ID = os.getenv("ROOT_SOURCE_ID")
REVIEW_COLOR = os.getenv("REVIEW_COLOR")

# Publisher Information
CENSUS_PUBLISHER = os.getenv("CENSUS_PUBLISHER")
CENSUS_PUB_LOC = os.getenv("CENSUS_PUB_LOC")

# Constants & Formatting
CURRENT_DATE = datetime.now().strftime('%d %b %Y').upper()
LOC = f"{TOWNSHIP}, {COUNTY}, {STATE}"
BASE_ID = ANCESTRY_IMAGE_BASE_ID.rstrip('_-')

# ==========================================
# Directories & Output
# ==========================================
PROGRAM_DIR = Path(os.environ["PROGRAM_DIR"])

RM_DIR = PROGRAM_DIR / os.environ["RM_DIR"].lstrip(r"\/")
FTM_DIR = PROGRAM_DIR / os.environ["FTM_DIR"].lstrip(r"\/")
CSV_DIR = PROGRAM_DIR / os.environ["CSV_DIR"].lstrip(r"\/")
GEDCOM_OUTPUT_PATH = PROGRAM_DIR / os.environ["GEDCOM_OUTPUT_PATH"]
GEDCOM_OUTPUT_NAME = PROGRAM_DIR / os.getenv("GEDCOM_OUTPUT_NAME", f"{CSV_FILE.replace('.csv', '.ged')}")
IMAGE_DIR = PROGRAM_DIR / os.environ["IMAGE_DIR"].lstrip(r"\/")
IMAGE_EXTENSION = os.environ["IMAGE_EXTENSION"].lstrip(".").lower()
FORM_TYPE = IMAGE_EXTENSION

# Resolve Input CSV
INPUT_CSV = Path(CSV_FILE)

if not INPUT_CSV.is_file():
    INPUT_CSV = CSV_DIR / INPUT_CSV.name

if not INPUT_CSV.is_file():
    raise FileNotFoundError(f"CSV file not found: {INPUT_CSV}")

# ==========================================
# Family-Inference Tuning Knobs
# ==========================================
MIN_MARRIAGE_AGE = int(os.getenv("MIN_MARRIAGE_AGE", "12"))
MAX_SPOUSE_AGE_GAP = int(os.getenv("MAX_SPOUSE_AGE_GAP", "25"))

HUSBAND_CHILD_AGE_GAP = (int(os.getenv("HUSBAND_CHILD_AGE_GAP_MIN", "14")), int(os.getenv("HUSBAND_CHILD_AGE_GAP_MAX", "60")))
WIFE_CHILD_AGE_GAP = (int(os.getenv("WIFE_CHILD_AGE_GAP_MIN", "12")), int(os.getenv("WIFE_CHILD_AGE_GAP_MAX", "50")))

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def clean_str(val):
    """Safely convert values to stripped strings."""
    if isinstance(val, pd.Series):
        raise TypeError(
            "clean_str() received a pandas Series instead of a scalar value. "
            "Did you mean row['ColumnName']?"
        )
    return "" if pd.isna(val) else str(val).strip()

def get_gender(val):
    """
    Accepts either a scalar gender value or a pandas Series (row).
    """
    if isinstance(val, pd.Series):
        val = val.get("Gender", "")
    v = clean_str(val).upper()
    if v.startswith(("M","MALE")):
        return "M"
    if v.startswith(("F","FEMALE")):
        return "F"
    return "U"

def get_age(row):
    return CENSUS_YEAR - float(row['Birth Year']) if pd.notna(row.get('Birth Year')) else (float(row['Age']) if pd.notna(row.get('Age')) else -1)

# ==========================================
# HOUSEHOLD PARSING
# ==========================================
REVIEW_CONFIDENCE_THRESHOLD = 0.6

def spouse_evaluation(a, b):
    g_a, g_b = get_gender(a['Gender']), get_gender(b['Gender'])
    if 'U' in (g_a, g_b) or g_a == g_b: return False, 0.0, "gender mismatch or unknown"

    age_a, age_b = get_age(a), get_age(b)
    if (age_a != -1 and age_a < MIN_MARRIAGE_AGE) or (age_b != -1 and age_b < MIN_MARRIAGE_AGE): return False, 0.0, "below minimum marriage age"
    if age_a != -1 and age_b != -1 and abs(age_a - age_b) >= MAX_SPOUSE_AGE_GAP: return False, 0.0, "spousal age gap too large"

    sur_a, sur_b = clean_str(a.get('Surname')), clean_str(b.get('Surname'))
    if sur_a and sur_b: return (True, 0.9, "matching surnames") if sur_a == sur_b else (False, 0.0, "surnames differ")

    return True, 0.4, "surname missing for one party -- unverified pairing"

def child_evaluation(unit, member):
    h, w = unit['husband'], unit['wife']
    if h is None and w is None: return False, 0.0, "no parents in unit"

    m_age, m_sur = get_age(member), clean_str(member.get('Surname'))
    u_sur = clean_str(h.get('Surname') if h is not None else w.get('Surname'))

    if m_sur and u_sur and m_sur != u_sur: return False, 0.0, "surname mismatch"
    surname_conf = 0.9 if (m_sur and u_sur and m_sur == u_sur) else 0.5

    if m_age == -1: return True, min(surname_conf, 0.5), "no age data for child -- unverified"

    def in_rng(parent, gap_rng):
        if parent is None: return None
        p_age = get_age(parent)
        return gap_rng[0] <= (p_age - m_age) <= gap_rng[1] if p_age != -1 else None

    checks = [v for v in (in_rng(h, HUSBAND_CHILD_AGE_GAP), in_rng(w, WIFE_CHILD_AGE_GAP)) if v is not None]
    
    if checks:
        if not any(checks): return False, 0.0, "age gap outside plausible range for both parents"
        if not all(checks): surname_conf = min(surname_conf, 0.8) 

    return True, surname_conf, "age and surname consistent with parentage"

def find_parent(units, member):
    best = None
    for i in sorted({0, len(units) - 1}, reverse=True):
        plausible, confidence, reason = child_evaluation(units[i], member)
        if plausible and (best is None or confidence > best[1]): best = (i, confidence, reason)
    return best

def parse_household(group):
    members = [group.iloc[i] for i in range(len(group))]
    n = len(members)
    flags, units, unrelated, consumed = [], [], [], set()

    def make_unit(m1, m2=None, anchor=None):
        h, w = (m2, m1) if get_gender(m1['Gender']) == 'F' else (m1, m2)
        if m2 is None: h, w = (None, m1) if get_gender(m1['Gender']) == 'F' else (m1, None)
        return {'husband': h, 'wife': w, 'children': [], 'anchor': anchor}

    head = members[0]
    i = 1
    
    if n > 1:
        m1 = members[1]
        plausible, confidence, reason = spouse_evaluation(head, m1)
        
        # Stepmother / Stepfather Override Bypass
        head_gender = get_gender(head)
        m1_gender = get_gender(m1)

        if not plausible and head_gender != m1_gender and 'U' not in (head_gender, m1_gender):
            m1_age = get_age(m1)
            if m1_age != -1 and any(get_age(x) > m1_age for x in members[2:]):
                plausible, confidence, reason = True, 0.8, "spouse age gap overridden; opposite-gender adult younger than subsequent household members (step-parent pattern)"

        if plausible:
            units.append(make_unit(head, m1))
            if confidence < REVIEW_CONFIDENCE_THRESHOLD:
                flags.append({'person': m1, 'reason': f"Possible spouse of head of household: {reason}", 'confidence': confidence})
            consumed.update({0, 1})
            i = 2
            
    if 0 not in consumed:
        units.append(make_unit(head))
        consumed.add(0)

    while i < n:
        if i in consumed:
            i += 1
            continue
        
        m = members[i]
        if i + 1 < n and (i + 1) not in consumed:
            nxt = members[i + 1]
            plausible, sp_conf, sp_reason = spouse_evaluation(m, nxt)
            
            if plausible:
                fit_m, fit_nxt = find_parent(units, m), find_parent(units, nxt)
                m_is_child = fit_m and fit_m[1] >= REVIEW_CONFIDENCE_THRESHOLD
                nxt_is_child = fit_nxt and fit_nxt[1] >= REVIEW_CONFIDENCE_THRESHOLD

                if m_is_child and nxt_is_child:
                    m_unit_idx = fit_m[0]
                    last_child_age = get_age(units[m_unit_idx]['children'][-1]) if units[m_unit_idx]['children'] else -1
                    m_age = get_age(m)
                    
                    if last_child_age != -1 and m_age != -1 and m_age > last_child_age:
                        anchor = m if clean_str(m.get('Surname')) == clean_str(head.get('Surname')) else nxt
                        units[m_unit_idx]['children'].append(anchor)
                        units.append(make_unit(m, nxt, anchor=anchor))
                        flags.append({'person': nxt if anchor is m else m, 'reason': f"Paired as spouse of {clean_str(anchor.get('Given Name'))} due to age-sequence break (older than preceding child).", 'confidence': 0.8})
                        consumed.update({i, i + 1})
                        i += 2
                        continue
                    else:
                        flags.append({'person': nxt, 'reason': f"Classified as a child of the household (age/surname fit as sibling of {clean_str(m.get('Given Name'))} {clean_str(m.get('Surname'))}), but the same evidence is equally consistent with being {clean_str(m.get('Given Name'))}'s spouse rather than a sibling -- confirm which", 'confidence': 0.5})
                elif m_is_child or nxt_is_child:
                    anchor, (anchor_idx, anchor_conf, anchor_reason) = (m, fit_m) if m_is_child else (nxt, fit_nxt)
                    units[anchor_idx]['children'].append(anchor)
                    if anchor_conf < REVIEW_CONFIDENCE_THRESHOLD:
                        flags.append({'person': anchor, 'reason': f"Possible child placement: {anchor_reason}", 'confidence': anchor_conf})
                    units.append(make_unit(m, nxt, anchor=anchor))
                    flags.append({'person': nxt if anchor is m else m, 'reason': f"Paired as spouse of {clean_str(anchor.get('Given Name'))} {clean_str(anchor.get('Surname'))} based on age/surname fit -- could instead be a sibling rather than a spouse; confirm which ({sp_reason})", 'confidence': min(sp_conf, 0.5)})
                    consumed.update({i, i + 1})
                    i += 2
                    continue
                else:
                    units.append(make_unit(m, nxt))
                    flags.append({'person': nxt, 'reason': f"Possible couple with no confirmed link to household head: {sp_reason}", 'confidence': min(sp_conf, 0.4)})
                    consumed.update({i, i + 1})
                    i += 2
                    continue

        if match := find_parent(units, m):
            idx, confidence, reason = match
            units[idx]['children'].append(m)
            if confidence < REVIEW_CONFIDENCE_THRESHOLD:
                flags.append({'person': m, 'reason': f"Possible child placement: {reason}", 'confidence': confidence})
        else:
            m_sur, h_sur = clean_str(m.get('Surname')), clean_str(head.get('Surname'))
            if m_sur == h_sur and m_sur:
                units[0]['children'].append(m)
                flags.append({'person': m, 'reason': "Shares head-of-household surname but doesn't fit as a child by age -- possible sibling/parent/other relative, relationship unconfirmed", 'confidence': 0.3})
            else:
                unrelated.append(m)
                flags.append({'person': m, 'reason': f"Unrelated household member: {'no surname and no plausible parent/spouse match in household' if not m_sur else 'different surname, no plausible family fit -- likely boarder/lodger'}", 'confidence': 0.2})
        consumed.add(i)
        i += 1

    for u in units:
        if u['husband'] is not None and u['wife'] is not None and u['children']:
            h_age, w_age = get_age(u['husband']), get_age(u['wife'])
            max_c_age = max((get_age(c) for c in u['children'] if get_age(c) != -1), default=-1)
            
            if w_age != -1 and max_c_age != -1 and w_age < max_c_age:
                flags.append({'person': u['wife'], 'reason': f"Wife (age {int(w_age)}) is younger than the oldest child (age {int(max_c_age)}) -- likely a stepmother.", 'confidence': 0.8})
            elif h_age != -1 and max_c_age != -1 and h_age < max_c_age:
                flags.append({'person': u['husband'], 'reason': f"Husband (age {int(h_age)}) is younger than the oldest child (age {int(max_c_age)}) -- likely a stepfather.", 'confidence': 0.8})

    if flags and head.name not in {f['person'].name for f in flags}:
        flags.append({'person': head, 'reason': f"{len({f['person'].name for f in flags})} household member(s) have uncertain relationships needing review", 'confidence': 0.5})

    return units, unrelated, flags

# ==========================================
# GEDCOM BLOCK BUILDERS
# ==========================================
def build_citation_block(row, rec_id, m_id, image_name, image_url_id, real_page, target_software):
    giv, sur = clean_str(row.get('Given Name')), clean_str(row.get('Surname'))
    person_str = f"{giv} {sur}".strip()
    
    note_cols = ['Quality', 'Real Estate Value', 'Personal Estate Value', 'Cannot Read, Write', 'Disability Condition', 'Deaf Dumb Blind Insane', 'Idiotic Pauper Convict']
    notes_list = [f"{c}: {clean_str(row[c])}" for c in note_cols if c in row and clean_str(row[c])]
            
    cit = [f"2 SOUR @S_{CENSUS_YEAR}_CENSUS@"]

    if target_software == "RM":
        rm_data_text = f"Ancestry Image ID: {image_name}" + ("; " + "; ".join(notes_list) if notes_list else "")
        cit.extend([
            f"3 PAGE {ROLL_NUMBER}; {TOWNSHIP}; {real_page}; {clean_str(row.get('Family Number'))}; {person_str}",
            "3 _TMPLT", 
            f"4 FIELD\n5 NAME RollNo\n5 VALUE {ROLL_NUMBER}",
            f"4 FIELD\n5 NAME CivilDivision\n5 VALUE {TOWNSHIP}",
            f"4 FIELD\n5 NAME PageID\n5 VALUE {real_page}",
            f"4 FIELD\n5 NAME HouseholdID\n5 VALUE {clean_str(row.get('Family Number'))}",
            f"4 FIELD\n5 NAME PersonOfInterest\n5 VALUE {person_str}",
            f"3 NAME {sur}, {giv}: Page: {real_page}, Line: {clean_str(row.get('Line Number', 'X'))}, Dwelling: {clean_str(row.get('Dwelling Number'))}, Family: {clean_str(row.get('Family Number'))}",
            "3 DATA",
            f"4 TEXT {rm_data_text}",
            "3 _WEBTAG", 
            "4 NAME Ancestry.com", 
            f"4 URL https://www.ancestry.com/imageviewer/collections/{APID_DB}/images/{image_url_id}?pId={rec_id}", 
            f"3 OBJE {m_id}"
        ])
    else:
        cit.extend([
            f"3 PAGE {person_str}; p. {real_page}, dwell. {clean_str(row.get('Dwelling Number'))}, fam. {clean_str(row.get('Family Number'))}; {TOWNSHIP}; {COUNTY}; {STATE}; Roll {ROLL_NUMBER}; Film {FILM_NUMBER}",
            "3 QUAY 3"
        ])
        if notes_list: cit.extend(["3 DATA", f"4 TEXT {'; '.join(notes_list)}"])
        cit.extend([
            f"3 _APID 1,{APID_DB}::{rec_id}", 
            f"3 _LINK https://www.ancestry.com/search/collections/{APID_DB}/records/{rec_id}", 
            f"4 NAME {sur}, {giv} - {CENSUS_YEAR} US Federal Census", 
            f"3 OBJE {m_id}"
        ])
        
    return cit

def build_relationship_task(rec_id, giv, sur, record_label, reasons, media_path):
    task_id = f"@T{rec_id}@"
    summary = "; ".join(r for r, _ in reasons)
    priority = 1 if (min_c := min((c for _, c in reasons), default=1.0)) < 0.3 else (2 if min_c < REVIEW_CONFIDENCE_THRESHOLD else 3)

    return (
        [f"1 _COLOR {REVIEW_COLOR}", f"1 _TASK {task_id}"],
        [
            f"0 {task_id} _TASK",
            f"1 DESC {sur or '[No Surname]'}, {giv or '[No Given Name]'} ({record_label}): {summary}",
            f"1 REFN {rec_id}",
            "1 TYPE 2",
            f"1 DATE {CURRENT_DATE}",
            f"1 _LDATE {CURRENT_DATE}",
            f"1 NOTE {summary}",
            f"1 FILE {media_path}",
            "1 STAT NEW",
            f"1 PRTY {priority}",
        ]
    )

def get_master_sources(target_software):
    if target_software == "RM":
        return [
            f"0 @S_{CENSUS_YEAR}_CENSUS@ SOUR", 
            f"1 ABBR {CENSUS_YEAR} U.S. Federal Census",
            f"1 TITL , {CENSUS_YEAR} U.S. Federal Census, {COUNTY}, {STATE}, Population, , ; on-line image {FILM_NUMBER}, .",
            f"1 _SUBQ , {CENSUS_YEAR} U.S. Federal Census, {COUNTY}, {STATE}, Population, , .",
            f"1 _BIBL {COUNTY}, {STATE}, {CENSUS_YEAR} U.S. Federal Census, Population. on-line image {FILM_NUMBER}. {CENSUS_PUB_LOC}: Ancestry.com.",
            "1 _TMPLT", "2 TID 48",
            f"2 FIELD\n3 NAME CensusID\n3 VALUE {CENSUS_YEAR} U.S. Federal Census", 
            f"2 FIELD\n3 NAME Jurisdiction\n3 VALUE {COUNTY}, {STATE}",
            "2 FIELD\n3 NAME Schedule\n3 VALUE Population", 
            f"2 FIELD\n3 NAME PublishLoc\n3 VALUE {CENSUS_PUB_LOC}",
            f"2 FIELD\n3 NAME Publisher\n3 VALUE Ancestry.com", 
            "2 FIELD\n3 NAME PubType\n3 VALUE on-line image",
            f"2 FIELD\n3 NAME FilmID\n3 VALUE {FILM_NUMBER}",
            f"0 {ROOT_SOURCE_ID} SOUR", 
            f"1 TITL {ORG_NAME}", 
            "1 AUTH", 
            f"1 PUBL Researcher: {RESEARCHER}.",
            "1 _WEBTAG", "2 NAME Facebook Group", f"2 URL {MGS_GROUP_URL}",
            "1 _WEBTAG", "2 NAME Ancestry Group", f"2 URL {ANCESTRY_GROUP_URL}"
        ]
    else: 
        return [
            f"0 @S_{CENSUS_YEAR}_CENSUS@ SOUR", 
            f"1 TITL {CENSUS_YEAR} U.S. Federal Census", 
            f"1 PUBL {CENSUS_PUB_LOC}: {CENSUS_PUBLISHER}", 
            f"1 _APID 1,{APID_DB}::0",
            f"0 {ROOT_SOURCE_ID} SOUR", 
            f"1 TITL {ORG_NAME}", 
            f"1 AUTH Research conducted by {RESEARCHER}.", 
            f"1 _LINK {MGS_GROUP_URL}", "2 NAME Facebook Group", 
            f"1 _LINK {ANCESTRY_GROUP_URL}", "2 NAME Ancestry Group"
        ]

# ==========================================
# MAIN ORCHESTRATOR
# ==========================================
def build_gedcom(df, target_software):
    ged = [f"0 HEAD", f"1 SOUR {ORG_NAME}", "1 GEDC", "2 VERS 5.5.1", "2 FORM LINEAGE-LINKED", "1 CHAR UTF-8", f"1 DATE {CURRENT_DATE}"]

    fam_links = {idx: [] for idx in df.index}
    fam_blocks, media_dict, task_blocks = [], {}, []

    # PASS 1: Generate Families
    review_flags = {}
    for _, group in df.groupby('Family Number'):
        units, unrelated, flags = parse_household(group)

        for flag in flags: review_flags.setdefault(flag['person'].name, []).append((flag['reason'], flag['confidence']))
        for person in unrelated: review_flags.setdefault(person.name, [])

        for u in units:
            h, w, c, anc = u['husband'], u['wife'], u['children'], u['anchor']
            anchor_row = h if h is not None else (w if w is not None else anc)
            if anchor_row is None: continue

            f_id = f"@F{ANCESTRY_START_RECORD_ID + anchor_row.name}@"
            fam_blocks.append(f"0 {f_id} FAM")

            if h is not None: fam_blocks.append(f"1 HUSB @I{ANCESTRY_START_RECORD_ID + h.name}@"); fam_links[h.name].append(f"1 FAMS {f_id}")
            if w is not None: fam_blocks.append(f"1 WIFE @I{ANCESTRY_START_RECORD_ID + w.name}@"); fam_links[w.name].append(f"1 FAMS {f_id}")
            for child in c: fam_blocks.append(f"1 CHIL @I{ANCESTRY_START_RECORD_ID + child.name}@"); fam_links[child.name].append(f"1 FAMC {f_id}")

            if (h is not None and pd.notna(h.get('Married Within Year'))) or (w is not None and pd.notna(w.get('Married Within Year'))):
                fam_blocks.extend(["1 MARR", f"2 DATE EST {CENSUS_YEAR}"])

    # PASS 2: Generate Individuals
    for idx, row in df.iterrows():
        rec_id = ANCESTRY_START_RECORD_ID + idx
        giv, sur, gen = clean_str(row.get('Given Name')), clean_str(row.get('Surname')), get_gender(row.get('Gender'))
        
        page = clean_str(row.get('Page'))
        real_page = clean_str(row.get('Real Page', row.get('Real_Page', page)))
        
        image_name = f"{BASE_ID}_{page.zfill(5)}"
        m_id = f"@M{image_name}@"
        
        if page not in media_dict:
            media_dict[page] = {'id': m_id, 'img': IMAGE_DIR / f"{image_name}{IMAGE_EXTENSION}", 'title': f"{CENSUS_YEAR} Census, {COUNTY}, Page {real_page}"}

        cit = build_citation_block(row, rec_id, m_id, image_name, f"{BASE_ID}-{page.zfill(5)}", real_page, target_software)

        ged.extend([f"0 @I{rec_id}@ INDI", f"1 REFN {rec_id}", f"1 NAME {giv} /{sur}/"] + cit + [f"1 SEX {gen}", f"1 SOUR {ROOT_SOURCE_ID}"])

        if person_flags := review_flags.get(idx, []):
            if target_software == "RM":
                indi_task_lines, task_record_lines = build_relationship_task(rec_id, giv, sur, f"{CENSUS_YEAR}, Fam {clean_str(row.get('Family Number'))}, p.{real_page}", person_flags, media_dict[page]['img'])
                ged.extend(indi_task_lines)
                task_blocks.extend(task_record_lines)
            else:
                ged.extend([f"1 NOTE NEEDS REVIEW ({confidence:.0%} confidence): {reason}" for reason, confidence in person_flags])
        
        b_yr, age = row.get('Birth Year'), row.get('Age')
        birth_year = b_yr if pd.notna(b_yr) else (CENSUS_YEAR - age if pd.notna(age) else None)
        
        if pd.notna(birth_year): 
            b_plac = clean_str(row.get('Birth Place'))
            ged.extend([f"1 BIRT", f"2 DATE {int(birth_year)}"] + ([f"2 PLAC {b_plac}"] if b_plac else []) + cit)
            
        if occ := clean_str(row.get('Occupation')): ged.extend([f"1 OCCU {occ}", f"2 DATE {CENSUS_YEAR}", f"2 PLAC {LOC}"] + cit)
        if race := clean_str(row.get('Race')): ged.extend([f"1 FACT {race}", "2 TYPE Race", f"2 DATE {CENSUS_YEAR}"] + cit)
        if clean_str(row.get('Attended School')): ged.extend([f"1 EDUC", f"2 DATE {CENSUS_YEAR}", f"2 PLAC {LOC}"] + cit)

        cens_notes = []
        if CENSUS_YEAR == 1860:
            if val := clean_str(row.get('Personal Estate Value')): cens_notes.append(f"Personal Estate: {val}")
            if val := clean_str(row.get('Deaf Dumb Blind Insane')): cens_notes.append(f"Condition: {val}")
            if val := clean_str(row.get('Idiotic Pauper Convict')): cens_notes.append(f"Condition: {val}")
        elif CENSUS_YEAR == 1870:
            if clean_str(row.get('Father Foreign Born')): cens_notes.append("Father of foreign birth.")
            if clean_str(row.get('Mother Foreign Born')): cens_notes.append("Mother of foreign birth.")
            if clean_str(row.get('Male Citizen Over 21')): cens_notes.append("Male citizen of the United States of 21 years of age and upwards.")
            if clean_str(row.get('Voting Rights Denied')): cens_notes.append("Male citizen of 21 years of age and upwards whose right to vote is denied or abridged on grounds other than rebellion or other crime.")

        ged.extend([f"1 CENS", f"2 DATE {CENSUS_YEAR}", f"2 PLAC {LOC}"] + cit)
        if cens_notes: ged.append(f"2 NOTE {' | '.join(cens_notes)}")
        ged.extend(fam_links[idx])

    ged.extend(fam_blocks)
    ged.extend(task_blocks)
    ged.extend(get_master_sources(target_software))
    
    ged.extend([line for m in media_dict.values() for line in (f"0 {m['id']} OBJE", f"1 FILE {m['img']}", f"2 FORM {FORM_TYPE}", f"1 TITL {m['title']}")])

    if target_software == "RM":
        ged.extend(["0 _EVDEF Race\n1 TYPE P\n1 TITL Race\n1 ABBR Race\n1 SENT [person] was of [Desc] ethnicity.\n1 PLAC N\n1 DATE Y\n1 DESC Y", "0 _EVDEF EDUC\n1 TYPE P\n1 TITL Education\n1 ABBR Education\n1 SENT [person] was being educated < [Desc]>< [Date]>< [PlaceDetails]>< [Place].\n1 PLAC Y\n1 DATE Y\n1 DESC Y"])

    ged.append("0 TRLR")
    
    base_name, ext = Path(GEDCOM_OUTPUT_NAME).stem, Path(GEDCOM_OUTPUT_NAME).suffix
    out_dir = Path(GEDCOM_OUTPUT_PATH) if GEDCOM_OUTPUT_PATH else (RM_DIR if target_software == "RM" else FTM_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    output_path = out_dir / f"{base_name} - {target_software}{ext}"
    output_path.write_text("\n".join(ged), encoding="utf-8")
    print(f"Success! {len(df)} individuals converted and saved to {output_path}")

if __name__ == "__main__":
    df = pd.read_csv(INPUT_CSV)
    for col in ['Age', 'Birth Year']:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce')
        
    for software in ["RM", "FTM"]: build_gedcom(df, software)