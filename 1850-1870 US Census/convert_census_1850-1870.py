"""
Census to GEDCOM Converter Script.

This script converts a CSV file containing census data into a GEDCOM file,
tailored for either RootsMagic (RM) or Family Tree Maker (FTM) software.
It reads configuration variables from the environment (or a .env file),
parses the census data using pandas, attempts to infer family relationships
(spouses and children) based on heuristics (age, gender, surname), and
generates a GEDCOM 5.5.1 compliant file with custom extensions for RM/FTM.
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

# Load environment variables from a .env file if present, overriding existing
# environment variables with the values from the file.
load_dotenv(override=True)

# ==========================================
# GLOBAL VARIABLES
# ==========================================
# Extracting configuration from environment variables with default fallbacks.
ORG_NAME = os.getenv("ORG_NAME", "")
RESEARCHER = os.getenv("RESEARCHER", "")
SOFTWARE_NAME = os.getenv("SOFTWARE_NAME", "RootsMagic")
SOFTWARE_VERS = os.getenv("SOFTWARE_VERS", "11.0")
COPYRIGHT_START = os.getenv("COPYRIGHT_START", "2026")
GEDCOM_NOTE = os.getenv("GEDCOM_NOTE", "")
GEDCOM_CONC = os.getenv("GEDCOM_CONC", "")
REVIEW_COLOR = os.getenv("REVIEW_COLOR", "1")
SUBM_ADDRESS = os.getenv("SUBM_ADDRESS", "")

CENSUS_YEAR = int(os.getenv("CENSUS_YEAR", "0"))
STATE = os.getenv("STATE", "")
COUNTY = os.getenv("COUNTY", "")
TOWNSHIP = os.getenv("TOWNSHIP", "")
FILM_NUMBER = os.getenv("FILM_NUMBER", "")
ROLL_NUMBER = os.getenv("ROLL_NUMBER", "")
CSV_FILE = os.getenv("CSV_FILE", "")

ANCESTRY_START_RECORD_ID = int(os.getenv("ANCESTRY_START_RECORD_ID", "0"))
APID_DB = os.getenv("APID_DB", "")
ANCESTRY_IMAGE_BASE_ID = os.getenv("ANCESTRY_IMAGE_BASE_ID", "")

# Society Group Links & IDs
MGS_GROUP_URL = os.getenv("MGS_GROUP_URL", "")
ANCESTRY_GROUP_URL = os.getenv("ANCESTRY_GROUP_URL", "")
ROOT_SOURCE_ID = os.getenv("ROOT_SOURCE_ID", "")

# Repository Information
CALL_NUMBER = os.getenv("CALL_NUMBER", "")
REPOSITORY = os.getenv("REPOSITORY", "Ancestry.com")
REPOSITORY_LOC = os.getenv("REPOSITORY_LOC", "")
COLLECTION_URL = os.getenv("COLLECTION_URL", "")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "")

# Publisher Information
CENSUS_PUBLISHER = os.getenv("CENSUS_PUBLISHER", "")
CENSUS_PUB_LOC = os.getenv("CENSUS_PUB_LOC", "")

# Constants & Formatting
CURRENT_DATE = datetime.now().strftime('%d %b %Y').upper()
LOC = f"{TOWNSHIP}, {COUNTY}, {STATE}".strip(", ")
BASE_ID = ANCESTRY_IMAGE_BASE_ID.rstrip('_-')


# ==========================================
# Directories & Output
# ==========================================
PROGRAM_DIR = os.getenv("PROGRAM_DIR", "")


def safe_path(base: str, p: str) -> str | Path:
    """
    Safely handle relative vs absolute Path resolution.

    Args:
        base: The base directory path.
        p: The path to resolve, which can be absolute or relative.

    Returns:
        An empty string if `p` is empty, otherwise a Path object representing
        the resolved path.
    """
    if not p:
        return ""
    return Path(p) if os.path.isabs(p) else Path(base) / p


# Resolve various directory and file paths using the safe_path helper.
RM_DIR = safe_path(PROGRAM_DIR, os.getenv("RM_DIR", ""))
FTM_DIR = safe_path(PROGRAM_DIR, os.getenv("FTM_DIR", ""))
CSV_DIR = safe_path(PROGRAM_DIR, os.getenv("CSV_DIR", ""))
GEDCOM_OUTPUT_PATH = safe_path(PROGRAM_DIR, os.getenv("GEDCOM_OUTPUT_PATH", ""))
GEDCOM_OUTPUT_NAME = os.getenv(
    "GEDCOM_OUTPUT_NAME", f"{CSV_FILE.replace('.csv', '.ged')}"
)
IMAGE_DIR = safe_path(PROGRAM_DIR, os.getenv("IMAGE_DIR", ""))
IMAGE_EXTENSION = os.getenv("IMAGE_EXTENSION", "").lstrip(".").lower()
FORM_TYPE = IMAGE_EXTENSION

# ==========================================
# Family-Inference Tuning Knobs
# ==========================================
# Thresholds used for heuristic-based family relationship inference.
MIN_MARRIAGE_AGE = int(os.getenv("MIN_MARRIAGE_AGE", "12"))
MAX_SPOUSE_AGE_GAP = int(os.getenv("MAX_SPOUSE_AGE_GAP", "25"))
HUSBAND_CHILD_AGE_GAP = (
    int(os.getenv("HUSBAND_CHILD_AGE_GAP_MIN", "14")),
    int(os.getenv("HUSBAND_CHILD_AGE_GAP_MAX", "60")),
)
WIFE_CHILD_AGE_GAP = (
    int(os.getenv("WIFE_CHILD_AGE_GAP_MIN", "12")),
    int(os.getenv("WIFE_CHILD_AGE_GAP_MAX", "50")),
)
REVIEW_THRESHOLD = 0.6


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def clean_str(val: Any) -> str:
    """
    Ensure the value is a cleanly formatted string or an empty string.

    Args:
        val: The value to clean (e.g., from a pandas DataFrame cell).

    Returns:
        The stripped string representation of the value, or an empty string if
        the value is NaN or None.

    Raises:
        TypeError: If the input is a pandas Series instead of a scalar.
    """
    if isinstance(val, pd.Series):
        raise TypeError(
            "clean_str() received a pandas Series instead of a scalar value."
        )
    return str(val).strip() if pd.notna(val) else ""


def get_gender(val: Any) -> str:
    """
    Parse the gender value and return a standardized 'M', 'F', or 'U'.

    Args:
        val: The gender value (string) or a pandas Series/dict containing a
            'Gender' key.

    Returns:
        'M' for male, 'F' for female, or 'U' for unknown.
    """
    # If the input is a Series or dict, attempt to extract the 'Gender' key.
    if isinstance(val, pd.Series) or isinstance(val, dict):
        val = val.get("Gender", "")
    
    v = clean_str(val).upper()
    if v.startswith(("M", "MALE")):
        return "M"
    elif v.startswith(("F", "FEMALE")):
        return "F"
    return "U"


def get_age(row: pd.Series) -> float:
    """
    Calculate or parse the age of an individual from census row data.

    Attempts to calculate age from 'Birth Year' and 'CENSUS_YEAR', falling back
    to the 'Age' column if calculation fails.

    Args:
        row: A pandas Series representing a row of census data for an individual.

    Returns:
        The calculated or parsed age as a float, or -1.0 if no valid age data
        is found.
    """
    def parse_num(val: Any) -> Optional[float]:
        try:
            if pd.notna(val) and str(val).strip():
                return float(val)
        except ValueError:
            pass
        return None

    # Try calculating age from birth year first
    b_yr = parse_num(row.get('Birth Year'))
    if b_yr is not None:
        return float(CENSUS_YEAR) - b_yr

    # Fall back to the literal 'Age' column
    age = parse_num(row.get('Age'))
    return age if age is not None else -1.0


def spouse_evaluation(
    a: pd.Series, b: pd.Series
) -> Tuple[bool, float, str]:
    """
    Evaluate if two individuals are plausibly spouses.

    Uses heuristics based on gender mismatch, age, and surname to determine
    if a spousal relationship is likely.

    Args:
        a: Data row for the first individual.
        b: Data row for the second individual.

    Returns:
        A tuple containing:
            - A boolean indicating if they are plausible spouses.
            - A confidence score (0.0 to 1.0).
            - A string describing the reason for the evaluation.
    """
    g_a = get_gender(a.get('Gender', ''))
    g_b = get_gender(b.get('Gender', ''))
    
    # Must have different known genders
    if 'U' in (g_a, g_b) or g_a == g_b:
        return False, 0.0, "gender mismatch or unknown"
        
    age_a, age_b = get_age(a), get_age(b)
    
    # Both must meet minimum marriage age requirements
    if (age_a != -1 and age_a < MIN_MARRIAGE_AGE) or \
       (age_b != -1 and age_b < MIN_MARRIAGE_AGE):
        return False, 0.0, "below minimum marriage age"
        
    # The age difference cannot exceed the maximum allowed gap
    if age_a != -1 and age_b != -1 and \
       abs(age_a - age_b) >= MAX_SPOUSE_AGE_GAP:
        return False, 0.0, "spousal age gap too large"
        
    sur_a, sur_b = clean_str(a.get('Surname')), clean_str(b.get('Surname'))
    
    if sur_a and sur_b:
        if sur_a == sur_b:
            return True, 0.9, "matching surnames"
        else:
            return False, 0.0, "surnames differ"
            
    return True, 0.4, "surname missing for one party -- unverified pairing"


def child_evaluation(
    unit: Dict[str, Any], member: pd.Series
) -> Tuple[bool, float, str]:
    """
    Evaluate if a member is plausibly a child of the given family unit.

    Examines age gaps between the member and potential parents in the unit,
    as well as surname matches.

    Args:
        unit: A dictionary representing a family unit (must have 'husband'
            and 'wife' keys, which can be None).
        member: Data row for the potential child.

    Returns:
        A tuple containing:
            - A boolean indicating if the member is plausibly a child.
            - A confidence score (0.0 to 1.0).
            - A string describing the reason for the evaluation.
    """
    h, w = unit.get('husband'), unit.get('wife')
    
    # Cannot be a child if there are no parents
    if h is None and w is None:
        return False, 0.0, "no parents in unit"
        
    m_age = get_age(member)
    m_sur = clean_str(member.get('Surname'))
    
    # Determine the surname of the family unit (preferring husband's)
    u_sur = ""
    if h is not None:
        u_sur = clean_str(h.get('Surname'))
    elif w is not None:
        u_sur = clean_str(w.get('Surname'))

    # If both surnames are known, they must match
    if m_sur and u_sur and m_sur != u_sur:
        return False, 0.0, "surname mismatch"
        
    surname_conf = 0.9 if (m_sur and u_sur and m_sur == u_sur) else 0.5
    
    # If the child's age is unknown, we can't verify the age gap
    if m_age == -1:
        return True, min(surname_conf, 0.5), "no age data for child -- unverified"

    def in_rng(parent: Optional[pd.Series], gap_rng: Tuple[int, int]) -> Optional[bool]:
        """Check if the age gap between parent and child is within range."""
        if parent is None:
            return None
        p_age = get_age(parent)
        if p_age != -1:
            return gap_rng[0] <= (p_age - m_age) <= gap_rng[1]
        return None

    # Check age gaps for available parents
    checks = []
    h_check = in_rng(h, HUSBAND_CHILD_AGE_GAP)
    if h_check is not None:
        checks.append(h_check)
        
    w_check = in_rng(w, WIFE_CHILD_AGE_GAP)
    if w_check is not None:
        checks.append(w_check)

    if checks:
        # If neither parent is within the plausible age gap, reject
        if not any(checks):
            return False, 0.0, "age gap outside plausible range for both parents"
        # If only one parent is within range (e.g., step-parent scenario), lower confidence
        if not all(checks):
            surname_conf = min(surname_conf, 0.8)
            
    return True, surname_conf, "age and surname consistent with parentage"


def find_parent(
    units: List[Dict[str, Any]], member: pd.Series
) -> Optional[Tuple[int, float, str]]:
    """
    Find the best matching parent unit for a child from a list of units.

    Iterates through family units in reverse order (to prefer more recently
    formed units) and finds the one with the highest confidence score for
    the potential child.

    Args:
        units: A list of dictionaries representing constructed family units.
        member: Data row for the potential child.

    Returns:
        A tuple containing the index of the best unit, the confidence score,
        and the reason, or None if no plausible unit is found.
    """
    best = None
    # Iterate backwards through units to match with the most recent family first
    for i in range(len(units) - 1, -1, -1):
        plausible, confidence, reason = child_evaluation(units[i], member)
        if plausible and (best is None or confidence > best[1]):
            best = (i, confidence, reason)
    return best


def parse_household(
    group: pd.DataFrame
) -> Tuple[List[Dict[str, Any]], List[pd.Series], List[Dict[str, Any]]]:
    """
    Parse a household group into distinct family units and unrelated members.

    This function applies heuristics to group household members into family
    structures (parents and children), flags individuals who don't fit
    cleanly, and identifies unrelated individuals.

    Args:
        group: A pandas DataFrame containing rows for a single household.

    Returns:
        A tuple containing:
            - A list of family unit dictionaries.
            - A list of data rows for unrelated individuals.
            - A list of dictionaries containing review flags for specific
              individuals.
    """
    members = [row for _, row in group.iterrows()]
    n = len(members)
    flags: List[Dict[str, Any]] = []
    units: List[Dict[str, Any]] = []
    unrelated: List[pd.Series] = []
    consumed = set()

    def add_flag(person: pd.Series, reason: str, confidence: float) -> None:
        """Helper to append a review flag if confidence is below threshold."""
        if confidence < REVIEW_THRESHOLD:
            flags.append(
                {'person': person, 'reason': reason, 'confidence': confidence}
            )

    def make_unit(
        m1: pd.Series, m2: Optional[pd.Series] = None, anchor: Optional[pd.Series] = None
    ) -> Dict[str, Any]:
        """Helper to construct a new family unit dictionary."""
        m1_gen = get_gender(m1)
        h, w = (None, m1) if m1_gen == 'F' else (m1, None)
        if m2 is not None:
            # If m1 is female, m2 must be the husband, else m2 is the wife
            h, w = (m2, m1) if m1_gen == 'F' else (m1, m2)
        return {'husband': h, 'wife': w, 'children': [], 'anchor': anchor}

    if not members:
        return units, unrelated, flags

    head = members[0]
    i = 1
    
    # Process the head of household and potential spouse
    if n > 1:
        m1 = members[1]
        plausible, confidence, reason = spouse_evaluation(head, m1)
        head_gender = get_gender(head)
        m1_gender = get_gender(m1)
        
        # Check for step-parent pattern if they are not plausible spouses initially
        if not plausible and head_gender != m1_gender and \
           'U' not in (head_gender, m1_gender):
            m1_age = get_age(m1)
            # If m1 is older than other members, they might be a step-parent
            if m1_age != -1 and any(get_age(x) > m1_age for x in members[2:]):
                plausible, confidence, reason = True, 0.8, "step-parent pattern"
                
        if plausible:
            units.append(make_unit(head, m1))
            add_flag(m1, f"Possible spouse: {reason}", confidence)
            consumed.update({0, 1})
            i = 2

    # If head wasn't paired, create a unit with just the head
    if 0 not in consumed:
        units.append(make_unit(head))
        consumed.add(0)

    # Process remaining household members
    while i < n:
        if i in consumed:
            i += 1
            continue
            
        m = members[i]
        
        # Check if the current and next member form a sub-family (e.g., married child)
        if i + 1 < n and (i + 1) not in consumed:
            nxt = members[i + 1]
            plausible, sp_conf, sp_reason = spouse_evaluation(m, nxt)
            
            if plausible:
                fit_m = find_parent(units, m)
                fit_nxt = find_parent(units, nxt)
                m_is_child = (fit_m and fit_m[1] >= REVIEW_THRESHOLD)
                nxt_is_child = (fit_nxt and fit_nxt[1] >= REVIEW_THRESHOLD)

                # Both appear to be children (e.g., siblings married to each other - unlikely, or just siblings)
                if m_is_child and nxt_is_child:
                    # Type checking assertion for mypy/linter
                    assert fit_m is not None
                    
                    m_unit_idx = fit_m[0]
                    m_unit = units[m_unit_idx]
                    
                    last_c_age = -1
                    if m_unit['children']:
                        last_c_age = get_age(m_unit['children'][-1])
                        
                    m_age = get_age(m)
                    
                    # If there's an age sequence break, they might be a separate family
                    if last_c_age != -1 and m_age != -1 and \
                       m_age > last_c_age and m_age >= MIN_MARRIAGE_AGE:
                        # Determine which one carries the family surname
                        if clean_str(m.get('Surname')) == clean_str(head.get('Surname')):
                            anchor = m
                        else:
                            anchor = nxt
                            
                        units[m_unit_idx]['children'].append(anchor)
                        units.append(make_unit(m, nxt, anchor=anchor))
                        
                        flagged_person = nxt if anchor is m else m
                        add_flag(
                            flagged_person,
                            "Paired as spouse due to age-sequence break.",
                            0.5
                        )
                    else:
                        # Otherwise, just add them as regular children
                        units[m_unit_idx]['children'].extend([m, nxt])
                        
                    consumed.update({i, i + 1})
                    i += 2
                    continue
                    
                # One of them is a child of the main unit, bringing a spouse
                elif m_is_child or nxt_is_child:
                    if m_is_child:
                        assert fit_m is not None
                        anchor, (anchor_idx, anchor_conf, anchor_reason) = m, fit_m
                    else:
                        assert fit_nxt is not None
                        anchor, (anchor_idx, anchor_conf, anchor_reason) = nxt, fit_nxt
                        
                    units[anchor_idx]['children'].append(anchor)
                    add_flag(anchor, f"Possible child: {anchor_reason}", anchor_conf)
                    units.append(make_unit(m, nxt, anchor=anchor))
                    consumed.update({i, i + 1})
                    i += 2
                    continue

        # Try to find a parent for the current member
        match = find_parent(units, m)
        if match:
            idx, confidence, reason = match
            units[idx]['children'].append(m)
            add_flag(m, f"Possible child: {reason}", confidence)
        else:
            # If no parent found, check for surname match with head
            m_sur = clean_str(m.get('Surname'))
            h_sur = clean_str(head.get('Surname'))
            
            if m_sur == h_sur and m_sur:
                units[0]['children'].append(m)
                add_flag(m, "Head-surname match; no age fit", 0.3)
            else:
                unrelated.append(m)
                add_flag(m, "Unrelated household member", 0.2)
                
        consumed.add(i)
        i += 1
        
    return units, unrelated, flags


# ==========================================
# GEDCOM BUILDER
# ==========================================
def build_citation_block(
    row: pd.Series,
    rec_id: int,
    m_id: str,
    image_name: str,
    real_page: str,
    target_software: str
) -> List[str]:
    """
    Construct the GEDCOM source citation block tailored to RM or FTM format.

    Args:
        row: The pandas Series representing the individual's data row.
        rec_id: The integer ID assigned to the individual record.
        m_id: The media object ID string.
        image_name: The name of the image file.
        real_page: The parsed real page number.
        target_software: Either "RM" for RootsMagic or "FTM" for Family Tree Maker.

    Returns:
        A list of strings representing the GEDCOM citation lines.
    """
    giv = clean_str(row.get('Given Name'))
    sur = clean_str(row.get('Surname'))
    person_str = f"{giv} {sur}".strip()
    
    # Initialize the citation block with the source ID
    cit = [f"2 SOUR @S_{CENSUS_YEAR}_CENSUS@"]

    if target_software == "RM":
        # RootsMagic specific citation template structure
        cit.extend([
            f"3 PAGE {ROLL_NUMBER}; {TOWNSHIP}; {real_page}; {clean_str(row.get('Family Number'))}; {person_str}",
            "3 _TMPLT",
            "4 FIELD", "5 NAME RollNo", f"5 VALUE {ROLL_NUMBER}",
            "4 FIELD", "5 NAME CivilDivision", f"5 VALUE {TOWNSHIP}",
            "4 FIELD", "5 NAME PageID", f"5 VALUE {real_page}",
            "4 FIELD", "5 NAME HouseholdID", f"5 VALUE {clean_str(row.get('Family Number'))}",
            "4 FIELD", "5 NAME PersonOfInterest", f"5 VALUE {person_str}",
            f"3 NAME {sur}, {giv}: Page: {real_page}, Line: {clean_str(row.get('Line Number', 'X'))}, Dwelling: {clean_str(row.get('Dwelling Number'))}, Family: {clean_str(row.get('Family Number'))}",
            "3 DATA",
            "3 _WEBTAG",
            f"4 NAME {COLLECTION_NAME or REPOSITORY}",
            f"4 URL https://www.ancestry.com/search/collections/{APID_DB}/records/{rec_id}",
            f"3 OBJE {m_id}"
        ])
    else:
        # Family Tree Maker specific citation structure
        cit.extend([
            f"3 PAGE {person_str}; p. {real_page}, dwell. {clean_str(row.get('Dwelling Number'))}, fam. {clean_str(row.get('Family Number'))}; {TOWNSHIP}; {COUNTY}; {STATE}; Roll {ROLL_NUMBER}; Film {FILM_NUMBER}",
            "3 QUAY 3",
            f"3 _APID 1,{APID_DB}::{rec_id}",
            f"3 _LINK https://www.ancestry.com/search/collections/{APID_DB}/records/{rec_id}",
            f"4 NAME {sur}, {giv} - {CENSUS_YEAR} US Federal Census",
            f"3 OBJE {m_id}"
        ])
        
    return cit


def get_census_notes(row: pd.Series) -> List[str]:
    """
    Extract special census columns into a list of notes.

    Includes formatting condition flags specific to certain census years,
    such as the 1870 census.

    Args:
        row: The pandas Series representing the individual's data row.

    Returns:
        A list of string notes describing special conditions or values.
    """
    note_cols = [
        'Quality', 'Real Estate Value', 'Personal Estate Value',
        'Cannot Read, Write', 'Disability Condition',
        'Deaf Dumb Blind Insane', 'Idiotic Pauper Convict'
    ]
    
    # Extract general notes for columns that exist and have values
    notes = [
        f"{c}: {clean_str(row[c])}"
        for c in note_cols
        if c in row and clean_str(row[c])
    ]

    # Handle special flags for the 1870 census
    if CENSUS_YEAR == 1870:
        flags = {
            'Father Foreign Born': "Father of foreign birth.",
            'Mother Foreign Born': "Mother of foreign birth.",
            'Male Citizen Over 21': "Male citizen of the United States of 21 years of age and upwards.",
            'Voting Rights Denied': "Male citizen of 21 years of age and upwards whose right to vote is denied or abridged on grounds other than rebellion or other crime."
        }
        
        notes.extend([
            msg for col, msg in flags.items()
            if clean_str(row.get(col))
        ])
        
    return notes


def build_relationship_task(
    row: pd.Series,
    rec_id: int,
    giv: str,
    sur: str,
    record_label: str,
    reasons: List[Tuple[str, float]],
    m_id: str,
    citation_block: List[str],
    media_path: str | Path,
    media_title: str
) -> Tuple[List[str], str]:
    """
    Build a custom task block for manual review.

    This function attaches flags, links the task to the individual, and ensures
    level-shift consistency on citation blocks so they parse cleanly in RootsMagic.

    Args:
        row: The individual's data row.
        rec_id: The individual's record ID.
        giv: Given name.
        sur: Surname.
        record_label: A descriptive label for the record.
        reasons: A list of tuples containing the review reason and confidence.
        m_id: Media ID associated with the task.
        citation_block: The base citation block to adapt for the task.
        media_path: Path to the media file.
        media_title: Title for the media file.

    Returns:
        A tuple containing:
            - A list of GEDCOM strings representing the task block.
            - A string representing the folder name for categorizing the task.
    """
    task_id = f"@T{rec_id}@"
    summary = "; ".join(r for r, _ in reasons)

    # Sanitize and simplify the reason to create a folder name
    folder_raw = reasons[0][0]
    folder_name = re.sub(r"\(age \d+\)", "", folder_raw)
    folder_name = re.sub(r"of \w+", "", folder_name)
    folder_name = folder_name.replace("--", ":")
    folder_name = re.sub(r"\s+", " ", folder_name).strip(" :")

    # Determine task priority based on minimum confidence score
    min_c = min((c for _, c in reasons), default=1.0)
    priority = 1 if min_c < 0.3 else (2 if min_c < REVIEW_THRESHOLD else 3)

    task_citation = []
    
    # Adjust citation levels to fit within the task block hierarchy
    if citation_block and citation_block[0].startswith("2 SOUR"):
        for line in citation_block:
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[0].isdigit():
                new_level = int(parts[0]) - 1
                task_citation.append(f"{new_level} {parts[1]}")
            else:
                task_citation.append(line)

    # Construct the task records
    task_records = [
        f"0 {task_id} _TASK",
        f"1 DESC {sur or '[No Surname]'}, {giv or '[No Given Name]'} ({record_label}): {summary}",
        f"1 REFN {rec_id}",
        f"1 _LINK @I{rec_id}@",
        "1 TYPE 2",
        f"1 DATE {CURRENT_DATE}",
        f"1 _LDATE {CURRENT_DATE}",
        f"1 NOTE {summary}",
        "1 STAT NEW",
        f"1 PRTY {priority}",
        f"1 _COLOR {REVIEW_COLOR}",
        "1 _WEBTAG",
        f"2 NAME {COLLECTION_NAME or REPOSITORY}",
        f"2 URL https://www.ancestry.com/search/collections/{APID_DB}/records/{rec_id}",
    ] + task_citation + [
        "1 OBJE",
        f"2 FILE {media_path}",
        "2 FORM jpg",
        f"2 TITL {media_title}",
        "2 _TYPE PHOTO"
    ]
    
    return task_records, folder_name


def get_master_sources(target_software: str) -> List[str]:
    """
    Generate the master source blocks dependent on the chosen software type.

    Args:
        target_software: "RM" or "FTM".

    Returns:
        A list of strings representing the GEDCOM master source definitions.
    """
    if target_software == "RM":
        # RootsMagic master sources use custom template (_TMPLT) tags
        return [
            f"0 @S_{CENSUS_YEAR}_CENSUS@ SOUR",
            f"1 ABBR {CENSUS_YEAR} U.S. Federal Census",
            f"1 TITL , {CENSUS_YEAR} U.S. Federal Census, {COUNTY}, {STATE}, Population, , ; on-line image {FILM_NUMBER}, .",
            f"1 _SUBQ , {CENSUS_YEAR} U.S. Federal Census, {COUNTY}, {STATE}, Population, , .",
            f"1 _BIBL {COUNTY}, {STATE}, {CENSUS_YEAR} U.S. Federal Census, Population. on-line image {FILM_NUMBER}. {CENSUS_PUB_LOC}: {CENSUS_PUBLISHER}.",
            "1 REPO @R1@",
            "1 _TMPLT",
            "2 TID 48",
            "2 FIELD", "3 NAME CensusID", f"3 VALUE {CENSUS_YEAR} U.S. Federal Census",
            "2 FIELD", "3 NAME Jurisdiction", f"3 VALUE {COUNTY}, {STATE}",
            "2 FIELD", "3 NAME Schedule", "3 VALUE Population",
            "2 FIELD", "3 NAME PublishLoc", f"3 VALUE {CENSUS_PUB_LOC}",
            "2 FIELD", "3 NAME Publisher", f"3 VALUE {CENSUS_PUBLISHER}",
            "2 FIELD", "3 NAME PubType", "3 VALUE on-line image",
            "2 FIELD", "3 NAME FilmID", f"3 VALUE {FILM_NUMBER}",
            f"0 {ROOT_SOURCE_ID} SOUR",
            f"1 TITL {ORG_NAME}",
            f"2 NAME Researcher: {RESEARCHER}.",
            f"2 _TITL Researcher: {RESEARCHER}.",
            "1 AUTH",
            "1 PUBL",
            "1 _WEBTAG",
            "2 NAME Facebook Group",
            f"2 URL {MGS_GROUP_URL}",
            "1 _WEBTAG",
            "2 NAME Ancestry Group",
            f"2 URL {ANCESTRY_GROUP_URL}"
        ]
    else:
        # Family Tree Maker master sources
        return [
            f"0 @S_{CENSUS_YEAR}_CENSUS@ SOUR",
            f"1 TITL {CENSUS_YEAR} U.S. Federal Census",
            f"1 PUBL {CENSUS_PUB_LOC}: {CENSUS_PUBLISHER}",
            "1 REPO @R1@",
            f"1 _APID 1,{APID_DB}::0",
            f"0 {ROOT_SOURCE_ID} SOUR",
            f"1 TITL {ORG_NAME}",
            f"1 AUTH Research conducted by {RESEARCHER}.",
            f"1 _LINK {MGS_GROUP_URL}",
            "2 NAME Facebook Group",
            f"1 _LINK {ANCESTRY_GROUP_URL}",
            "2 NAME Ancestry Group"
        ]


def build_gedcom(df: pd.DataFrame, target_software: str) -> None:
    """
    Main builder orchestrating the conversion from DataFrame to GEDCOM.

    Constructs the GEDCOM header, iterates through the parsed families and
    individuals to build their respective records, appends media and source
    definitions, and writes the final string to a file.

    Args:
        df: The pandas DataFrame containing the cleaned census data.
        target_software: The target software format ("RM" or "FTM").
    """
    # Initialize the GEDCOM header
    ged = [
        "0 HEAD",
        f"1 SOUR {SOFTWARE_NAME}",
        f"2 VERS {SOFTWARE_VERS}",
        f"2 CORP {ORG_NAME}",
        "1 GEDC",
        "2 VERS 5.5.1",
        "2 FORM LINEAGE-LINKED",
        "1 CHAR UTF-8",
        f"1 DATE {CURRENT_DATE}",
        f"1 COPR Copyright {COPYRIGHT_START}",
        "1 SUBM @SUB1@"
    ]
    
    if GEDCOM_NOTE:
        ged.append(f"1 NOTE {GEDCOM_NOTE}")
        if GEDCOM_CONC:
            ged.append(f"2 CONC {GEDCOM_CONC}")

    # Data structures to hold intermediate record blocks
    fam_links: Dict[int, List[str]] = {idx: [] for idx in df.index}
    fam_blocks: List[str] = []
    media_dict: Dict[str, Dict[str, Any]] = {}
    task_blocks: List[str] = []
    folder_tasks: Dict[str, List[str]] = {}
    review_flags: Dict[int, List[Tuple[str, float]]] = {}

    # Identify distinct households by changes in Family Number
    df['Household_ID'] = (
        df['Family Number'] != df['Family Number'].shift()
    ).cumsum()

    # Process each household to infer family units and generate family records
    for _, group in df.groupby('Household_ID'):
        units, unrelated, flags = parse_household(group)
        
        # Store review flags indexed by the individual's DataFrame index
        for flag in flags:
            person_idx = flag['person'].name
            review_flags.setdefault(person_idx, []).append(
                (flag['reason'], flag['confidence'])
            )
            
        for person in unrelated:
            review_flags.setdefault(person.name, [])

        # Build Family (FAM) records for each inferred unit
        for u in units:
            h, w, c, anc = u['husband'], u['wife'], u['children'], u['anchor']
            
            # Determine the anchor individual to base the family ID on
            anchor_row = h if h is not None else (w if w is not None else anc)
            if anchor_row is None:
                continue
                
            f_id = f"@F{ANCESTRY_START_RECORD_ID + anchor_row.name}@"
            fam_blocks.append(f"0 {f_id} FAM")
            
            # Add Husband link
            if h is not None:
                h_id = ANCESTRY_START_RECORD_ID + h.name
                fam_blocks.append(f"1 HUSB @I{h_id}@")
                fam_links[h.name].append(f"1 FAMS {f_id}")
                
            # Add Wife link
            if w is not None:
                w_id = ANCESTRY_START_RECORD_ID + w.name
                fam_blocks.append(f"1 WIFE @I{w_id}@")
                fam_links[w.name].append(f"1 FAMS {f_id}")
                
            # Add Children links
            for child in c:
                c_id = ANCESTRY_START_RECORD_ID + child.name
                fam_blocks.append(f"1 CHIL @I{c_id}@")
                fam_links[child.name].append(f"1 FAMC {f_id}")
                
            # Add estimated marriage date if indicated by census
            if (h is not None and pd.notna(h.get('Married within Year'))) or \
               (w is not None and pd.notna(w.get('Married within Year'))):
                fam_blocks.extend(["1 MARR", f"2 DATE EST {CENSUS_YEAR}"])

    # Process each individual row to build Individual (INDI) records
    for idx, row in df.iterrows():
        rec_id = ANCESTRY_START_RECORD_ID + idx
        giv = clean_str(row.get('Given Name'))
        sur = clean_str(row.get('Surname'))
        gen = get_gender(row.get('Gender'))
        page = clean_str(row.get('Page'))
        real_page = clean_str(row.get('Real Page', row.get('Real_Page', page)))
        
        image_name = f"{BASE_ID}_{page.zfill(5)}"
        m_id = f"@M{image_name}@"

        # Register media if not already seen
        if page not in media_dict:
            img_path = Path(str(IMAGE_DIR)) / f"{image_name}.{IMAGE_EXTENSION}"
            media_dict[page] = {
                'id': m_id,
                'img': img_path,
                'title': f"{CENSUS_YEAR} Census, {COUNTY}, Page {real_page}"
            }

        cit = build_citation_block(
            row, rec_id, m_id, image_name, real_page, target_software
        )
        
        # Start individual record
        ged.extend([
            f"0 @I{rec_id}@ INDI",
            f"1 REFN {rec_id}",
            f"1 NAME {giv} /{sur}/"
        ] + cit + [
            f"1 SEX {gen}",
            f"1 SOUR {ROOT_SOURCE_ID}"
        ])

        # Handle review flags (tasks for RM, notes for FTM)
        if person_flags := review_flags.get(idx, []):
            if target_software == "RM":
                lbl = f"{CENSUS_YEAR}, Fam {clean_str(row.get('Family Number'))}, p.{real_page}"
                task_records, folder = build_relationship_task(
                    row, rec_id, giv, sur, lbl, person_flags, m_id, cit,
                    media_dict[page]['img'], media_dict[page]['title']
                )
                task_blocks.extend(task_records)
                folder_tasks.setdefault(folder, []).append(f"1 _TASK @T{rec_id}@")
                ged.extend([f"1 _TASK @T{rec_id}@", f"1 _COLOR {REVIEW_COLOR}"])
            else:
                for reason, confidence in person_flags:
                    ged.extend([
                        f"1 NOTE NEEDS REVIEW ({confidence:.0%} confidence): {reason}"
                    ])

        # Add birth information
        b_yr = row.get('Birth Year')
        age = row.get('Age')
        birth_year = b_yr if pd.notna(b_yr) else \
                     (CENSUS_YEAR - age if pd.notna(age) else None)
                     
        if pd.notna(birth_year):
            ged.extend([f"1 BIRT", f"2 DATE {int(birth_year)}"])
            if birth_place := clean_str(row.get('Birth Place')):
                ged.append(f"2 PLAC {birth_place}")
            ged.extend(cit)
            
        # Add occupation
        if occ := clean_str(row.get('Occupation')):
            ged.extend([
                f"1 OCCU {occ}",
                f"2 DATE {CENSUS_YEAR}",
                f"2 PLAC {LOC}"
            ] + cit)
            
        # Add race (using custom FACT for GEDCOM compatibility)
        if race := clean_str(row.get('Race')):
            ged.extend([
                f"1 FACT {race}",
                "2 TYPE Race",
                f"2 DATE {CENSUS_YEAR}"
            ] + cit)
            
        # Add education
        if clean_str(row.get('Attended School')):
            ged.extend([
                f"1 EDUC",
                f"2 DATE {CENSUS_YEAR}",
                f"2 PLAC {LOC}"
            ] + cit)

        # Add the primary census event
        ged.extend([
            f"1 CENS",
            f"2 DATE {CENSUS_YEAR}",
            f"2 PLAC {LOC}",
            f"2 _PROOF proven"
        ] + cit)
        
        # Append any census-specific notes
        if notes := get_census_notes(row):
            ged.append(f"2 NOTE {' | '.join(notes)}")
            
        # Link individual to their families
        ged.extend(fam_links[idx])

    # Assemble all major blocks into the final GEDCOM structure
    ged.extend(fam_blocks)
    ged.extend(task_blocks)
    
    # Append task folders for RootsMagic
    for folder, tasks in folder_tasks.items():
        ged.append(f"0 _FOLDER {folder}")
        ged.extend(tasks)

    # Append sources and media objects
    ged.extend(get_master_sources(target_software))
    for m in media_dict.values():
        ged.extend([
            f"0 {m['id']} OBJE",
            f"1 FILE {m['img']}",
            f"2 FORM {FORM_TYPE}",
            f"1 TITL {m['title']}"
        ])

    # Append custom event definitions for RootsMagic
    if target_software == "RM":
        ged.extend([
            "0 _EVDEF Race", "1 TYPE P", "1 TITL Race", "1 ABBR Race",
            "1 SENT [person] was of [Desc] ethnicity.", "1 PLAC N",
            "1 DATE Y", "1 DESC Y",
            "0 _EVDEF EDUC", "1 TYPE P", "1 TITL Education", "1 ABBR Education",
            "1 SENT [person] was being educated < [Desc]>< [Date]>< [PlaceDetails]>< [Place].",
            "1 PLAC Y", "1 DATE Y", "1 DESC Y"
        ])

    # Append submitter and repository blocks, and the trailer
    ged.extend([
        "0 @SUB1@ SUBM",
        f"1 NAME {RESEARCHER}",
        f"1 ADDR {SUBM_ADDRESS}",
        "0 @R1@ REPO",
        f"1 NAME {REPOSITORY}",
        f"1 ADDR {REPOSITORY_LOC}",
        f"1 CALN {CALL_NUMBER}",
        "2 MEDI Electronic",
        f"2 _URL {COLLECTION_URL}",
        "0 TRLR"
    ])

    # Determine output path and save the file
    base_name = Path(GEDCOM_OUTPUT_NAME).stem
    ext = Path(GEDCOM_OUTPUT_NAME).suffix
    
    if GEDCOM_OUTPUT_PATH:
        out_dir = Path(str(GEDCOM_OUTPUT_PATH))
    else:
        out_dir = Path(str(RM_DIR)) if target_software == "RM" else Path(str(FTM_DIR))
        
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{base_name} - {target_software}{ext}"
    
    output_path.write_text("\n".join(ged), encoding="utf-8")
    print(f"Success! {len(df)} individuals converted and saved to {output_path}")


if __name__ == "__main__":
    INPUT_CSV = Path(CSV_FILE) if os.path.isabs(CSV_FILE) else Path(str(CSV_DIR)) / CSV_FILE
    
    if not INPUT_CSV.is_file():
        raise FileNotFoundError(f"CSV file not found: {INPUT_CSV}")

    # Load the CSV data
    census_df = pd.read_csv(INPUT_CSV)

    # Ensure necessary columns are numeric if they exist
    for col in ['Age', 'Birth Year']:
        if col in census_df.columns:
            census_df[col] = pd.to_numeric(census_df[col], errors='coerce')

    # Build files for both targeted software types
    for software in ["RM", "FTM"]:
        build_gedcom(census_df, software)