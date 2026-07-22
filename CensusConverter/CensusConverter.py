"""
Census to GEDCOM Converter Script.

This script converts a CSV file containing census data into a GEDCOM file,
tailored for either RootsMagic (RM) or Family Tree Maker (FTM) software.
It reads configuration variables from the environment (or a .env file),
parses the census data using pandas, and generates a GEDCOM 5.5.1
compliant file with custom extensions for RM/FTM.
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
from dotenv import load_dotenv

# Setting override=False creates a strict cascade:
# 1. Variables already in Memory (passed dynamically from CensusExtractor) win.
# 2. Variables present in the .env file fill the blanks.
# 3. If neither exist, the hardcoded defaults in this file are used.
load_dotenv(override=False)


def get_env_int(key: str, default: int) -> int:
	val = os.getenv(key, "")
	try:
		return int(str(val).strip()) if val and str(val).strip() else default
	except ValueError:
		return default


# ==========================================
# GLOBAL VARIABLES
# ==========================================
ORG_NAME = os.getenv("ORG_NAME", "")
RESEARCHER = os.getenv("RESEARCHER", "")
SOFTWARE_NAME = os.getenv("SOFTWARE_NAME", "RootsMagic")
SOFTWARE_VERS = os.getenv("SOFTWARE_VERS", "11.0")
COPYRIGHT_START = os.getenv("COPYRIGHT_START", "2026")
GEDCOM_NOTE = os.getenv("GEDCOM_NOTE", "")
GEDCOM_CONC = os.getenv("GEDCOM_CONC", "")
REVIEW_COLOR = os.getenv("REVIEW_COLOR", "1")
SUBM_ADDRESS = os.getenv("SUBM_ADDRESS", "")

CENSUS_YEAR = get_env_int("CENSUS_YEAR", 0)
CSV_FILE = os.getenv("CSV_FILE", "")

ANCESTRY_START_RECORD_ID = get_env_int("ANCESTRY_START_RECORD_ID", 0)
APID_DB = os.getenv("APID_DB", "")
ANCESTRY_IMAGE_BASE_ID = os.getenv("ANCESTRY_IMAGE_BASE_ID", "")

STATE = os.getenv("STATE", "")
COUNTY = os.getenv("COUNTY", "")
TOWNSHIP = os.getenv("TOWNSHIP", "")
FILM_NUMBER = os.getenv("FILM_NUMBER", "")
ROLL_NUMBER = os.getenv("ROLL_NUMBER", "")

MGS_GROUP_URL = os.getenv("MGS_GROUP_URL", "")
ANCESTRY_GROUP_URL = os.getenv("ANCESTRY_GROUP_URL", "")
ROOT_SOURCE_ID = os.getenv("ROOT_SOURCE_ID", "")

CALL_NUMBER = os.getenv("CALL_NUMBER", "")
REPOSITORY = os.getenv("REPOSITORY", "Ancestry.com")
REPOSITORY_LOC = os.getenv("REPOSITORY_LOC", "")
COLLECTION_URL = os.getenv("COLLECTION_URL", "")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "")

CENSUS_PUBLISHER = os.getenv("CENSUS_PUBLISHER", "")
CENSUS_PUB_LOC = os.getenv("CENSUS_PUB_LOC", "")

CURRENT_DATE = datetime.now().strftime('%d %b %Y').upper()
BASE_ID = ANCESTRY_IMAGE_BASE_ID.rstrip('_-')


def get_census_era(year: int) -> str:
	if year <= 1840: return "pre1850"
	if year <= 1870: return "heuristic"
	return "relationship"


CENSUS_ERA = get_census_era(CENSUS_YEAR)

# ==========================================
# Directories & Output
# ==========================================
PROGRAM_DIR = os.getenv("PROGRAM_DIR", "")


def safe_path(base: str, p: str) -> str | Path:
	if not p: return ""
	return Path(p) if os.path.isabs(p) else Path(base) / p


RM_DIR = safe_path(PROGRAM_DIR, os.getenv("RM_DIR", ""))
FTM_DIR = safe_path(PROGRAM_DIR, os.getenv("FTM_DIR", ""))
CSV_DIR = safe_path(PROGRAM_DIR, os.getenv("CSV_DIR", ""))
GEDCOM_OUTPUT_PATH = safe_path(PROGRAM_DIR, os.getenv("GEDCOM_OUTPUT_PATH", ""))
GEDCOM_OUTPUT_NAME = os.getenv("GEDCOM_OUTPUT_NAME", f"{CSV_FILE.replace('.csv', '.ged')}")
IMAGE_DIR = safe_path(PROGRAM_DIR, os.getenv("IMAGE_DIR", ""))
IMAGE_EXTENSION = os.getenv("IMAGE_EXTENSION", "").lstrip(".").lower()
FORM_TYPE = IMAGE_EXTENSION

# ==========================================
# Family-Inference Tuning Knobs
# ==========================================
MIN_MARRIAGE_AGE = get_env_int("MIN_MARRIAGE_AGE", 12)
MAX_SPOUSE_AGE_GAP = get_env_int("MAX_SPOUSE_AGE_GAP", 25)
HUSBAND_CHILD_AGE_GAP = (get_env_int("HUSBAND_CHILD_AGE_GAP_MIN", 14), get_env_int("HUSBAND_CHILD_AGE_GAP_MAX", 60),)
WIFE_CHILD_AGE_GAP = (get_env_int("WIFE_CHILD_AGE_GAP_MIN", 12), get_env_int("WIFE_CHILD_AGE_GAP_MAX", 50),)
REVIEW_THRESHOLD = 0.6


def clean_str(val: Any) -> str:
	if val is None or pd.isna(val):
		return ""
	if isinstance(val, float) and val.is_integer():
		return str(int(val))
	return str(val).strip()


def get_gender(val: Any) -> str:
	if isinstance(val, (pd.Series, dict)):
		str_val = clean_str(val.get("Gender", ""))
	else:
		str_val = clean_str(val)
	if not str_val: return "U"
	v = str_val.upper()
	if v.startswith(("M", "MALE")):
		return "M"
	elif v.startswith(("F", "FEMALE")):
		return "F"
	return "U"


def get_age(row: pd.Series) -> float:
	def parse_num(val: Any) -> Optional[float]:
		try:
			if pd.notna(val) and val is not None:
				val_str = str(val).strip()
				if val_str: return float(val_str)
		except ValueError:
			pass
		return None
	
	b_yr = parse_num(row.get('Birth Year'))
	if b_yr is not None:
		return float(CENSUS_YEAR) - b_yr
	
	age = parse_num(row.get('Age'))
	return age if age is not None else -1.0


def spouse_evaluation(a: pd.Series, b: pd.Series) -> Tuple[bool, float, str]:
	g_a = get_gender(a.get('Gender', ''))
	g_b = get_gender(b.get('Gender', ''))
	if 'U' in (g_a, g_b) or g_a == g_b:
		return False, 0.0, "gender mismatch or unknown"
	age_a, age_b = get_age(a), get_age(b)
	if (age_a != -1 and age_a < MIN_MARRIAGE_AGE) or (age_b != -1 and age_b < MIN_MARRIAGE_AGE):
		return False, 0.0, "below minimum marriage age"
	if age_a != -1 and age_b != -1 and abs(age_a - age_b) >= MAX_SPOUSE_AGE_GAP:
		return False, 0.0, "spousal age gap too large"
	sur_a, sur_b = clean_str(a.get('Surname')), clean_str(b.get('Surname'))
	if sur_a and sur_b:
		if sur_a == sur_b:
			return True, 0.9, "matching surnames"
		else:
			return False, 0.0, "surnames differ"
	return True, 0.4, "surname missing for one party -- unverified pairing"


def child_evaluation(unit: Dict[str, Any], member: pd.Series) -> Tuple[bool, float, str]:
	h = unit.get('husband')
	w = unit.get('wife')
	if h is None and w is None:
		return False, 0.0, "no parents in unit"
	m_age = get_age(member)
	m_sur = clean_str(member.get('Surname'))
	u_sur = clean_str(h.get('Surname')) if h is not None else clean_str(w.get('Surname'))
	if m_sur and u_sur and m_sur != u_sur:
		return False, 0.0, "surname mismatch"
	surname_conf = 0.9 if (m_sur and u_sur and m_sur == u_sur) else 0.5
	if m_age == -1:
		return True, min(surname_conf, 0.5), "no age data for child -- unverified"
	
	def in_rng(parent: Any, gap_rng: Tuple[int, int]) -> Optional[bool]:
		if parent is None: return None
		p_age = get_age(parent)
		if p_age != -1: return gap_rng[0] <= (p_age - m_age) <= gap_rng[1]
		return None
	
	checks = []
	h_check = in_rng(h, HUSBAND_CHILD_AGE_GAP)
	if h_check is not None: checks.append(h_check)
	w_check = in_rng(w, WIFE_CHILD_AGE_GAP)
	if w_check is not None: checks.append(w_check)
	
	if checks:
		if not any(checks): return False, 0.0, "age gap outside plausible range for both parents"
		if not all(checks): surname_conf = min(surname_conf, 0.8)
	return True, surname_conf, "age and surname consistent with parentage"


def find_parent(units: List[Dict[str, Any]], member: pd.Series) -> Optional[Tuple[int, float, str]]:
	best: Optional[Tuple[int, float, str]] = None
	for i in range(len(units) - 1, -1, -1):
		plausible, match_conf, match_rsn = child_evaluation(units[i], member)
		if plausible:
			if best is None:
				best = (i, match_conf, match_rsn)
			else:
				_, best_conf, _ = best
				if match_conf > best_conf: best = (i, match_conf, match_rsn)
	return best


def parse_household(group: pd.DataFrame) -> Tuple[List[Dict[str, Any]], List[pd.Series], List[Dict[str, Any]]]:
	members = [row for _, row in group.iterrows()]
	n = len(members)
	flags: List[Dict[str, Any]] = []
	units: List[Dict[str, Any]] = []
	unrelated: List[pd.Series] = []
	consumed = set()
	
	def add_flag(person: pd.Series, flag_reason: str, flag_conf: float) -> None:
		if flag_conf < REVIEW_THRESHOLD:
			flags.append({'person': person, 'reason': flag_reason, 'confidence': flag_conf})
	
	def make_unit(mem1: pd.Series, mem2: Optional[pd.Series] = None, anchor_person: Optional[pd.Series] = None) -> Dict[
		str, Any]:
		m1_gen = get_gender(mem1)
		h_unit, w_unit = (None, mem1) if m1_gen == 'F' else (mem1, None)
		if mem2 is not None: h_unit, w_unit = (mem2, mem1) if m1_gen == 'F' else (mem1, mem2)
		return {'husband': h_unit, 'wife': w_unit, 'children': [], 'anchor': anchor_person, 'type': 'main'}
	
	if not members: return units, unrelated, flags
	
	head = members[0]
	i = 1
	if n > 1:
		sp_mem = members[1]
		plausible, sp_conf, sp_reason = spouse_evaluation(head, sp_mem)
		head_gender = get_gender(head)
		sp_gender = get_gender(sp_mem)
		if not plausible and head_gender != sp_gender and 'U' not in (head_gender, sp_gender):
			sp_age = get_age(sp_mem)
			if sp_age != -1 and any(get_age(x) > sp_age for x in members[2:]):
				plausible, sp_conf, sp_reason = True, 0.8, "step-parent pattern"
		if plausible:
			units.append(make_unit(head, sp_mem))
			add_flag(sp_mem, f"Possible spouse: {sp_reason}", sp_conf)
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
			sub_plausible, sub_sp_conf, _ = spouse_evaluation(m, nxt)
			if sub_plausible:
				fit_m = find_parent(units, m)
				fit_nxt = find_parent(units, nxt)
				m_is_child = False
				m_unit_idx = -1
				if fit_m is not None:
					m_unit_idx, m_conf, _ = fit_m
					m_is_child = (m_conf >= REVIEW_THRESHOLD)
				nxt_is_child = False
				if fit_nxt is not None:
					_, nxt_conf, _ = fit_nxt
					nxt_is_child = (nxt_conf >= REVIEW_THRESHOLD)
				
				if m_is_child and nxt_is_child:
					m_unit = units[m_unit_idx]
					last_c_age = -1
					if m_unit['children']: last_c_age = get_age(m_unit['children'][-1])
					m_age = get_age(m)
					if last_c_age != -1 and m_age != -1 and m_age > last_c_age and m_age >= MIN_MARRIAGE_AGE:
						anc_member = m if clean_str(m.get('Surname')) == clean_str(head.get('Surname')) else nxt
						units[m_unit_idx]['children'].append(anc_member)
						units.append(make_unit(m, nxt, anchor_person=anc_member))
						flagged_person = nxt if anc_member is m else m
						add_flag(flagged_person, "Paired as spouse due to age-sequence break.", 0.5)
					else:
						units[m_unit_idx]['children'].extend([m, nxt])
					consumed.update({i, i + 1})
					i += 2
					continue
				elif m_is_child or nxt_is_child:
					if m_is_child and fit_m is not None:
						anc_member = m
						anchor_idx, anchor_conf, anchor_rsn = fit_m
					elif fit_nxt is not None:
						anc_member = nxt
						anchor_idx, anchor_conf, anchor_rsn = fit_nxt
					else:
						continue
					
					units[anchor_idx]['children'].append(anc_member)
					add_flag(anc_member, f"Possible child: {anchor_rsn}", anchor_conf)
					units.append(make_unit(m, nxt, anchor_person=anc_member))
					consumed.update({i, i + 1})
					i += 2
					continue
		
		match = find_parent(units, m)
		if match:
			idx, match_conf, match_rsn = match
			units[idx]['children'].append(m)
			add_flag(m, f"Possible child: {match_rsn}", match_conf)
		else:
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
# Relationship Sets & Normalization
# ==========================================
RELATIONSHIP_COLUMN_CANDIDATES = ('Relationship to Head', 'Relationship', 'Relation to Head', 'Relation')
REL_HEAD = {'head', 'head of household', 'head of family', 'self', 'self head', 'housekeeper', 'partner'}
REL_SPOUSE = {'wife', 'husband', 'spouse'}
REL_CHILD = {'son', 'daughter', 'child', 'stepson', 'stepdaughter', 'step son', 'step daughter', 'adopted son',
             'adopted daughter', 'foster son', 'foster daughter'}
REL_SIBLING = {'brother', 'sister', 'half brother', 'half sister', 'stepbrother', 'stepsister', 'step brother',
               'step sister'}
REL_PARENT = {'father', 'mother', 'stepfather', 'stepmother', 'step father', 'step mother'}
REL_INLAW_SIBLING = {'brother in law', 'sister in law'}
REL_INLAW_PARENT = {'father in law', 'mother in law'}
REL_INLAW_CHILD = {'son in law', 'daughter in law'}
REL_GRANDCHILD = {'grandson', 'granddaughter', 'grandchild', 'great grandson', 'great granddaughter',
                  'great grandchild', 'step grandson', 'step granddaughter'}
REL_NIBLING = {'nephew', 'niece'}


def normalize_relationship(val: Any) -> str:
	v = clean_str(val).lower()
	v = v.replace('-', ' ')  # Convert hyphens to spaces before stripping non-alpha characters
	v = re.sub(r'[^a-z ]', '', v)
	return re.sub(r'\s+', ' ', v).strip()


def is_relationship_column(col_name: str) -> bool:
	norm = re.sub(r'[^a-z]', '', col_name.lower())
	return ('relation' in norm) and ('head' in norm)


def find_relationship_column(columns: Any) -> Optional[str]:
	# Exact match first (fast path for the known-common headers)
	exact = next((c for c in RELATIONSHIP_COLUMN_CANDIDATES if c in columns), None)
	if exact: return exact
	# Fuzzy fallback: real-world exports vary the header ("Relation to Head of House",
	# "Relationship to Head of Household", etc.) -- match on substance, not exact string,
	# so relationship data isn't silently dropped to the age/surname heuristic.
	return next((c for c in columns if is_relationship_column(str(c))), None)


def resolve_cross_family_links(units: List[Dict[str, Any]], unrelated: List[pd.Series], flags: List[Dict[str, Any]]) -> \
Tuple[List[Dict[str, Any]], List[pd.Series], List[Dict[str, Any]]]:
	"""
	Consolidates split family units by inferring maiden names from in-law relationships
	and linking them to a plausible parent/head of household with that surname.
	"""
	for unit in units:
		if unit.get('type') == 'spouse_parents':
			wife = unit.get('anchor')
			if wife is None or not isinstance(wife, pd.Series):
				continue
			
			unit_children = unit.get('children')
			if not isinstance(unit_children, list):
				continue
			
			# Separate the wife from her siblings (who share her maiden surname)
			siblings = [c for c in unit_children if c is not wife]
			if not siblings or not isinstance(siblings[0], pd.Series):
				continue
			
			maiden_surname = clean_str(siblings[0].get('Surname'))
			if not maiden_surname:
				continue
			
			wife_age = get_age(wife)
			
			# Look for a plausible parent in the other household units
			plausible_parent_unit = None
			parent_name = ""
			for other_unit in units:
				if other_unit is unit:
					continue
				
				# Check both potential husband (father) and wife (mother) in the unit
				for parent_key, age_gap_range in [('husband', HUSBAND_CHILD_AGE_GAP), ('wife', WIFE_CHILD_AGE_GAP)]:
					pp = other_unit.get(parent_key)
					if isinstance(pp, pd.Series):
						pp_sur = clean_str(pp.get('Surname'))
						pp_age = get_age(pp)
						
						# If the surname matches the maiden name and the age gap is plausible
						if pp_sur == maiden_surname:
							if pp_age == -1 or wife_age == -1 or (
									age_gap_range[0] <= (pp_age - wife_age) <= age_gap_range[1]):
								plausible_parent_unit = other_unit
								parent_name = clean_str(pp.get('Given Name'))
								break
				if plausible_parent_unit:
					break
			
			# If a matching parent unit is found, migrate the children
			if plausible_parent_unit:
				parent_children = plausible_parent_unit.get('children')
				if isinstance(parent_children, list):
					if not any(wife is c for c in parent_children):
						parent_children.append(wife)
						flags.append({'person': wife,
						              'reason': f"Inferred as child of {parent_name} {maiden_surname} via in-law links",
						              'confidence': 0.85})
					
					for sib in siblings:
						if not any(sib is c for c in parent_children):
							parent_children.append(sib)
							flags.append({'person': sib,
							              'reason': f"Inferred as child of {parent_name} {maiden_surname} via sibling link to head's spouse",
							              'confidence': 0.85})
				
				# Clear migrated individuals from the spouse_parents unit
				unit['children'] = [c for c in unit_children if c is not wife and c not in siblings]
				if unit.get('husband') is None and unit.get('wife') is None and not unit.get('children'):
					unit['type'] = 'merged'
	
	# Filter out any units we just emptied and merged
	units = [u for u in units if u.get('type') != 'merged']
	return units, unrelated, flags


def append_unit_if_not_empty(units: List[Dict[str, Any]], unit: Optional[Dict[str, Any]]) -> None:
	if unit and (unit['husband'] is not None or unit['wife'] is not None or len(unit['children']) > 1):
		units.append(unit)


def parse_household_relational(group: pd.DataFrame) -> Tuple[
	List[Dict[str, Any]], List[pd.Series], List[Dict[str, Any]]]:
	rel_col = find_relationship_column(group.columns)
	if rel_col is None: return parse_household(group)
	
	# Verify that a Head of Household exists before proceeding with relational logic.
	# If no head is found, fallback to heuristic logic so families don't collapse.
	has_head = any(normalize_relationship(m.get(rel_col, '')) in REL_HEAD for _, m in group.iterrows())
	if not has_head:
		return parse_household(group)
	
	flags: List[Dict[str, Any]] = []
	unrelated: List[pd.Series] = []
	units: List[Dict[str, Any]] = []
	
	current_unit: Optional[Dict[str, Any]] = None
	primary_head: Optional[pd.Series] = None
	primary_spouse: Optional[pd.Series] = None
	last_child_inlaw_unit: Optional[Dict[str, Any]] = None
	
	head_parents_unit: Optional[Dict[str, Any]] = None
	spouse_parents_unit: Optional[Dict[str, Any]] = None
	
	for _, m in group.iterrows():
		rel = normalize_relationship(m.get(rel_col, ''))
		
		# Flush and reset families if a new Head is encountered (e.g., Multi-family dwelling)
		if rel in REL_HEAD or current_unit is None or head_parents_unit is None or spouse_parents_unit is None:
			append_unit_if_not_empty(units, current_unit)
			append_unit_if_not_empty(units, head_parents_unit)
			append_unit_if_not_empty(units, spouse_parents_unit)
			
			current_unit = {'husband': None, 'wife': None, 'children': [], 'anchor': m, 'type': 'main'}
			head_parents_unit = {'husband': None, 'wife': None, 'children': [], 'anchor': m, 'type': 'head_parents'}
			spouse_parents_unit = {'husband': None, 'wife': None, 'children': [], 'anchor': None,
			                       'type': 'spouse_parents'}
			
			primary_head = None
			primary_spouse = None
			last_child_inlaw_unit = None
			
			if rel in REL_HEAD:
				if get_gender(m) == 'F':
					current_unit['wife'] = m
				else:
					current_unit['husband'] = m
				
				primary_head = m
				head_children = head_parents_unit.get('children')
				if isinstance(head_children, list):
					head_children.append(m)
			else:
				unrelated.append(m)
				flags.append(
					{'person': m, 'reason': f"First person in household not listed as Head (rel: {rel.title()})",
					 'confidence': 0.3})
			continue
		
		if rel in REL_SPOUSE and current_unit is not None:
			if get_gender(m) == 'F':
				current_unit['wife'] = m
			else:
				current_unit['husband'] = m
			
			if primary_spouse is None and primary_head is not None and spouse_parents_unit is not None:
				primary_spouse = m
				spouse_parents_unit['anchor'] = m
				spouse_children = spouse_parents_unit.get('children')
				if isinstance(spouse_children, list):
					spouse_children.append(m)
		
		elif rel in REL_CHILD and current_unit is not None:
			curr_children = current_unit.get('children')
			if isinstance(curr_children, list):
				curr_children.append(m)
		
		elif rel in REL_INLAW_CHILD and current_unit is not None:
			spouse_candidate = None
			m_gender = get_gender(m)
			target_gender = 'F' if m_gender == 'M' else ('M' if m_gender == 'F' else None)
			
			curr_children = current_unit.get('children')
			if isinstance(curr_children, list):
				for child in reversed(curr_children):
					if target_gender is None or get_gender(child) == target_gender:
						spouse_candidate = child
						break
			
			if spouse_candidate is not None:
				h_sub = m if m_gender == 'M' else spouse_candidate
				w_sub = spouse_candidate if m_gender == 'M' else m
				last_child_inlaw_unit = {'husband': h_sub, 'wife': w_sub, 'children': [], 'anchor': m, 'type': 'sub'}
				units.append(last_child_inlaw_unit)
				flags.append({'person': m, 'reason': f"Paired as in-law spouse in sub-family (rel: {rel.title()})",
				              'confidence': 0.8})
			else:
				h_sub = m if m_gender == 'M' else None
				w_sub = None if m_gender == 'M' else m
				last_child_inlaw_unit = {'husband': h_sub, 'wife': w_sub, 'children': [], 'anchor': m, 'type': 'sub'}
				units.append(last_child_inlaw_unit)
				flags.append(
					{'person': m, 'reason': f"In-law listed without matching spouse child (rel: {rel.title()})",
					 'confidence': 0.5})
		
		elif rel in REL_GRANDCHILD and current_unit is not None:
			if last_child_inlaw_unit is not None:
				last_children = last_child_inlaw_unit.get('children')
				if isinstance(last_children, list):
					last_children.append(m)
			else:
				curr_children = current_unit.get('children')
				if isinstance(curr_children, list):
					curr_children.append(m)
				flags.append({'person': m, 'reason': f"Grandchild attached to head family unit (rel: {rel.title()})",
				              'confidence': 0.6})
		
		elif rel in REL_SIBLING and primary_head is not None and head_parents_unit is not None:
			head_children = head_parents_unit.get('children')
			if isinstance(head_children, list):
				head_children.append(m)
		
		elif rel in REL_PARENT and primary_head is not None and head_parents_unit is not None:
			if get_gender(m) == 'F':
				head_parents_unit['wife'] = m
			else:
				head_parents_unit['husband'] = m
		
		elif rel in REL_INLAW_SIBLING and primary_spouse is not None and spouse_parents_unit is not None:
			spouse_children = spouse_parents_unit.get('children')
			if isinstance(spouse_children, list):
				spouse_children.append(m)
		
		elif rel in REL_INLAW_PARENT and primary_spouse is not None and spouse_parents_unit is not None:
			if get_gender(m) == 'F':
				spouse_parents_unit['wife'] = m
			else:
				spouse_parents_unit['husband'] = m
		
		elif rel in REL_NIBLING and current_unit is not None:
			curr_children = current_unit.get('children')
			if isinstance(curr_children, list):
				curr_children.append(m)
			flags.append({'person': m, 'reason': f"Niece/Nephew attached to household unit (rel: {rel.title()})",
			              'confidence': 0.5})
		
		elif rel:
			unrelated.append(m)
			flags.append({'person': m,
			              'reason': f"Stated relationship to head: {rel.title()} -- review for correct family placement",
			              'confidence': 0.5})
		else:
			unrelated.append(m)
			flags.append({'person': m, 'reason': "No relationship-to-head recorded -- review household placement",
			              'confidence': 0.2})
	
	append_unit_if_not_empty(units, current_unit)
	append_unit_if_not_empty(units, head_parents_unit)
	append_unit_if_not_empty(units, spouse_parents_unit)
	
	# Hook the resolution pass into the final return
	return resolve_cross_family_links(units, unrelated, flags)


def get_row_val(r: pd.Series, cols: List[str], default: str) -> str:
	if default: return default
	for c in cols:
		if c in r.index:
			val = clean_str(r.get(c))
			if val: return val
	return ""


def build_citation_block(row: pd.Series, rec_id: str, m_id: str, real_page: str, target_software: str, row_town: str,
                         row_county: str, row_state: str, row_country: str, row_roll: str, row_film: str) -> List[str]:
	giv = clean_str(row.get('Given Name'))
	sur = clean_str(row.get('Surname'))
	person_str = f"{giv} {sur}".strip()
	
	fam_num = get_row_val(row, ['Family Number', 'Family', 'Household Number', 'Household'], '')
	dwell_num = get_row_val(row, ['Dwelling Number', 'Dwelling', 'House Number'], '')
	line_num = get_row_val(row, ['Line Number', 'Line'], 'X')
	
	cit = [f"2 SOUR @S_{CENSUS_YEAR}_CENSUS@"]
	
	if target_software == "RM":
		cit.extend([f"3 PAGE {row_roll}; {row_town}; {real_page}; {fam_num}; {person_str}", "3 _TMPLT", "4 FIELD",
			"5 NAME RollNo", f"5 VALUE {row_roll}", "4 FIELD", "5 NAME CivilDivision", f"5 VALUE {row_town}", "4 FIELD",
			"5 NAME PageID", f"5 VALUE {real_page}", "4 FIELD", "5 NAME HouseholdID", f"5 VALUE {fam_num}", "4 FIELD",
			"5 NAME PersonOfInterest", f"5 VALUE {person_str}",
			f"3 NAME {sur}, {giv}: Page: {real_page}, Line: {line_num}, Dwelling: {dwell_num}, Family: {fam_num}",
			"3 DATA", f"3 _APID 1,{APID_DB}::{rec_id}", "3 _WEBTAG",
			f"4 NAME {COLLECTION_NAME or f'{CENSUS_YEAR} United States Federal Census'}",
			f"4 URL https://www.ancestry.com/search/collections/{APID_DB}/records/{rec_id}", f"3 OBJE {m_id}"])
	else:
		cit.extend(
			[f"3 PAGE {person_str}; p. {real_page}, dwell. {dwell_num}, fam. {fam_num}; {row_town}; {row_county}; {row_state}; Roll {row_roll}; Film {row_film}",
				"3 QUAY 3", f"3 _APID 1,{APID_DB}::{rec_id}",
				f"3 _LINK https://www.ancestry.com/search/collections/{APID_DB}/records/{rec_id}",
				f"4 NAME {sur}, {giv} - {CENSUS_YEAR} US Federal Census", f"3 OBJE {m_id}"])
	return cit


def get_census_notes(row: pd.Series) -> List[str]:
	note_cols = ['Quality', 'Real Estate Value', 'Personal Estate Value', 'Cannot Read, Write', 'Disability Condition',
	             'Deaf Dumb Blind Insane', 'Idiotic Pauper Convict']
	notes = [f"{c}: {clean_str(row[c])}" for c in note_cols if c in row and clean_str(row[c])]
	if CENSUS_YEAR == 1870:
		flags = {'Father Foreign Born': "Father of foreign birth.", 'Mother Foreign Born': "Mother of foreign birth.",
		         'Male Citizen Over 21': "Male citizen of the United States of 21 years of age and upwards.",
		         'Voting Rights Denied': "Male citizen of 21 years of age and upwards whose right to vote is denied or abridged on grounds other than rebellion or other crime."}
		notes.extend([msg for flag_col, msg in flags.items() if clean_str(row.get(flag_col))])
	return notes


CORE_COLUMNS = {'given name', 'surname', 'gender', 'sex', 'age', 'birth year', 'birth month', 'month of birth', 'page',
                'page_number', 'real page', 'real_page', 'family number', 'household number', 'family', 'household',
                'dwelling number', 'dwelling', 'line number', 'line', 'household_id', 'row_index_id', 'birth place',
                'birthplace', 'occupation', 'occupation category', 'industry', 'trade or profession', 'race', 'color',
                'attended school', 'highest grade of school completed', 'highest grade completed',
                'married within year', 'relationship to head', 'relationship', 'relation to head', 'relation',
                'quality', 'real estate value', 'personal estate value', 'cannot read, write', 'disability condition',
                'deaf dumb blind insane', 'idiotic pauper convict', 'father foreign born', 'mother foreign born',
                'male citizen over 21', 'voting rights denied', 'image_id', 'country', 'state', 'county', 'city',
                'place_details', 'roll', 'film', 'extracted_url', 'pid', 'street', 'street address', 'address',
                'house number'}

DYNAMIC_EVENT_RULES = [(re.compile(r'immigrat', re.I), 'IMMI'),
                       (re.compile(r'year of naturali[sz]', re.I), 'NATU_DATE'),
                       (re.compile(r'naturali[sz]ation status|^naturali[sz]', re.I), 'NATU'),
                       (re.compile(r'veteran|military|regiment|survivor of union|confederate|which war|pension', re.I),
                        'MILITARY'), (re.compile(r'residence', re.I), 'RESI'),
                       (re.compile(r'religio|church affiliation', re.I), 'RELI'),
                       (re.compile(r'maiden name', re.I), 'MAIDEN')]

PRE1850_ALLOWED_TAGS = {'MILITARY'}
MONTH_ABBR = {'1': 'JAN', '01': 'JAN', 'JAN': 'JAN', 'JANUARY': 'JAN', '2': 'FEB', '02': 'FEB', 'FEB': 'FEB',
              'FEBRUARY': 'FEB', '3': 'MAR', '03': 'MAR', 'MAR': 'MAR', 'MARCH': 'MAR', '4': 'APR', '04': 'APR',
              'APR': 'APR', 'APRIL': 'APR', '5': 'MAY', '05': 'MAY', 'MAY': 'MAY', '6': 'JUN', '06': 'JUN',
              'JUN': 'JUN', 'JUNE': 'JUN', '7': 'JUL', '07': 'JUL', 'JUL': 'JUL', 'JULY': 'JUL', '8': 'AUG',
              '08': 'AUG', 'AUG': 'AUG', 'AUGUST': 'AUG', '9': 'SEP', '09': 'SEP', 'SEP': 'SEP', 'SEPT': 'SEP',
              'SEPTEMBER': 'SEP', '10': 'OCT', 'OCT': 'OCT', 'OCTOBER': 'OCT', '11': 'NOV', 'NOV': 'NOV',
              'NOVEMBER': 'NOV', '12': 'DEC', 'DEC': 'DEC', 'DECEMBER': 'DEC'}
RESIDENCE_YEAR_PATTERN = re.compile(r'(1[89]\d{2})')
RESIDENCE_RELATIVE_PATTERN = re.compile(r'(\d+)\s*year', re.I)


def get_occupation_value(row: pd.Series) -> str:
	parts = [clean_str(row[c]) for c in ('Occupation', 'Occupation Category', 'Trade or Profession') if
	         c in row and clean_str(row[c])]
	if industry := clean_str(row.get('Industry', '')): parts.append(f"({industry})")
	return " ".join(parts).strip()


def get_education_value(row: pd.Series) -> Optional[str]:
	grade = clean_str(row.get('Highest Grade of School Completed', row.get('Highest Grade Completed', '')))
	if grade: return grade
	if clean_str(row.get('Attended School')): return ''
	return None


def get_birth_date(row: pd.Series, birth_year: float) -> str:
	month_str = get_row_val(row, ['Birth Month', 'Birth month', 'Month of Birth'], '')
	abbr = MONTH_ABBR.get(month_str.upper())
	return f"{abbr} {int(birth_year)}" if abbr else str(int(birth_year))


def build_residence_event(col_name: str, val: str, cit: List[str], loc: str, street: str = "") -> List[str]:
	year_match = RESIDENCE_YEAR_PATTERN.search(col_name)
	if year_match:
		date_val = year_match.group(1)
	else:
		rel_match = RESIDENCE_RELATIVE_PATTERN.search(col_name)
		date_val = str(CENSUS_YEAR - int(rel_match.group(1))) if rel_match else str(CENSUS_YEAR)
	
	evt = [f"1 RESI", f"2 DATE {date_val}", f"2 PLAC {val or loc}"]
	if street: evt.append(f"2 ADDR {street}")
	evt.extend(["2 _PROOF proven"] + cit)
	return evt


def build_dynamic_events_and_notes(row: pd.Series, cit: List[str], giv: str, columns: List[str], loc: str,
                                   street: str) -> Tuple[List[str], List[str]]:
	events: List[str] = []
	notes: List[str] = []
	for col_str in columns:
		if col_str.strip().lower() in CORE_COLUMNS: continue
		if is_relationship_column(col_str): continue
		val = clean_str(row.get(col_str))
		if not val: continue
		tag = next((t for pat, t in DYNAMIC_EVENT_RULES if pat.search(col_str)), None)
		if CENSUS_ERA == 'pre1850' and tag not in PRE1850_ALLOWED_TAGS: tag = None
		
		if tag == 'IMMI':
			year_match = re.search(r'\d{4}', val)
			events.extend([f"1 IMMI", f"2 DATE {year_match.group(0) if year_match else val}", "2 _PROOF proven"] + cit)
		elif tag == 'NATU_DATE':
			year_match = re.search(r'\d{4}', val)
			events.extend([f"1 NATU", f"2 DATE {year_match.group(0) if year_match else val}", "2 _PROOF proven"] + cit)
		elif tag == 'NATU':
			events.extend([f"1 NATU", f"2 DATE {CENSUS_YEAR}", f"2 NOTE {col_str}: {val}", "2 _PROOF proven"] + cit)
		elif tag == 'MILITARY':
			events.extend([f"1 EVEN", "2 TYPE Military Service", f"2 DATE {CENSUS_YEAR}", f"2 NOTE {col_str}: {val}",
			               "2 _PROOF proven"] + cit)
		elif tag == 'RESI':
			events.extend(build_residence_event(col_str, val, cit, loc, street))
		elif tag == 'RELI':
			events.extend([f"1 RELI {val}", "2 _PROOF proven"] + cit)
		elif tag == 'MAIDEN':
			events.extend([f"1 NAME {giv} /{val}/", "2 TYPE maiden"] + cit)
			notes.append(f"Maiden name recorded ({val}) -- review for parental FAMC link")
		else:
			notes.append(f"{col_str}: {val}")
	return events, notes


def build_relationship_task(rec_id: str, giv: str, sur: str, record_label: str, reasons: List[Tuple[str, float]],
                            citation_block: List[str], media_path: Union[str, Path], media_title: str) -> Tuple[
	List[str], str]:
	task_id = f"@T{rec_id}@"
	summary = "; ".join(r for r, _ in reasons)
	folder_raw = reasons[0][0]
	folder_name = re.sub(r"\(age \d+\)", "", folder_raw)
	folder_name = re.sub(r"of \w+", "", folder_name).replace("--", ":")
	folder_name = re.sub(r"\s+", " ", folder_name).strip(" :")
	min_c = min((c for _, c in reasons), default=1.0)
	priority = 1 if min_c < 0.3 else (2 if min_c < REVIEW_THRESHOLD else 3)
	
	task_citation = []
	if citation_block and citation_block[0].startswith("2 SOUR"):
		for line in citation_block:
			parts = line.split(" ", 1)
			if len(parts) == 2 and parts[0].isdigit():
				task_citation.append(f"{int(parts[0]) - 1} {parts[1]}")
			else:
				task_citation.append(line)
	
	task_records = [f"0 {task_id} _TASK",
		               f"1 DESC {sur or '[No Surname]'}, {giv or '[No Given Name]'} ({record_label}): {summary}",
		               f"1 REFN {rec_id}", f"1 _LINK @I{rec_id}@", "1 TYPE 2", f"1 DATE {CURRENT_DATE}",
		               f"1 _LDATE {CURRENT_DATE}", f"1 NOTE {summary}", "1 STAT NEW", f"1 PRTY {priority}",
		               f"1 _COLOR {REVIEW_COLOR}", "1 _WEBTAG",
		               f"2 NAME {COLLECTION_NAME or f'{CENSUS_YEAR} United States Federal Census'}",
		               f"2 URL https://www.ancestry.com/search/collections/{APID_DB}/records/{rec_id}"] + task_citation + [
		               "1 OBJE", f"2 FILE {media_path}", "2 FORM jpg", f"2 TITL {media_title}", "2 _TYPE PHOTO"]
	return task_records, folder_name


def get_master_sources(target_software: str) -> List[str]:
	if target_software == "RM":
		return [f"0 @S_{CENSUS_YEAR}_CENSUS@ SOUR", f"1 ABBR {CENSUS_YEAR} U.S. Federal Census",
		        f"1 TITL , {CENSUS_YEAR} U.S. Federal Census, {COUNTY}, {STATE}, Population, , ; on-line image {FILM_NUMBER}, .",
		        f"1 _SUBQ , {CENSUS_YEAR} U.S. Federal Census, {COUNTY}, {STATE}, Population, , .",
		        f"1 _BIBL {COUNTY}, {STATE}, {CENSUS_YEAR} U.S. Federal Census, Population. on-line image {FILM_NUMBER}. {CENSUS_PUB_LOC}: {CENSUS_PUBLISHER}.",
		        "1 REPO @R1@", "1 _TMPLT", "2 TID 48", "2 FIELD", "3 NAME CensusID",
		        f"3 VALUE {CENSUS_YEAR} U.S. Federal Census", "2 FIELD", "3 NAME Jurisdiction",
		        f"3 VALUE {COUNTY}, {STATE}", "2 FIELD", "3 NAME Schedule", "3 VALUE Population", "2 FIELD",
		        "3 NAME PublishLoc", f"3 VALUE {CENSUS_PUB_LOC}", "2 FIELD", "3 NAME Publisher",
		        f"3 VALUE {CENSUS_PUBLISHER}", "2 FIELD", "3 NAME PubType", "3 VALUE on-line image", "2 FIELD",
		        "3 NAME FilmID", f"3 VALUE {FILM_NUMBER}", f"0 {ROOT_SOURCE_ID} SOUR", f"1 TITL {ORG_NAME}", "1 AUTH",
		        f"1 PUBL Researcher: {RESEARCHER}.", "1 _WEBTAG", "2 NAME Facebook Group", f"2 URL {MGS_GROUP_URL}",
		        "1 _WEBTAG", "2 NAME Ancestry Group", f"2 URL {ANCESTRY_GROUP_URL}"]
	else:
		return [f"0 @S_{CENSUS_YEAR}_CENSUS@ SOUR", f"1 TITL {CENSUS_YEAR} U.S. Federal Census",
		        f"1 PUBL {CENSUS_PUB_LOC}: {CENSUS_PUBLISHER}", "1 REPO @R1@", f"1 _APID 1,{APID_DB}::0",
		        f"0 {ROOT_SOURCE_ID} SOUR", f"1 TITL {ORG_NAME}", f"1 AUTH Research conducted by {RESEARCHER}.",
		        f"1 _LINK {MGS_GROUP_URL}", "2 NAME Facebook Group", f"1 _LINK {ANCESTRY_GROUP_URL}",
		        "2 NAME Ancestry Group"]


def get_location_string(row: pd.Series) -> str:
	row_state = get_row_val(row, ['State', 'State/Province'], STATE)
	row_county = get_row_val(row, ['County', 'Parish'], COUNTY)
	row_town = get_row_val(row, ['City', 'Township', 'Town', 'Civil Division', 'Ward'], TOWNSHIP)
	row_country = get_row_val(row, ['Country'], 'USA')
	
	# We filter none to prune trailing/leading commas, and empty items.
	return ", ".join(filter(None, [row_town, row_county, row_state, row_country]))


def build_gedcom(df_in: pd.DataFrame, target_software: str) -> None:
	df = df_in.copy()
	
	# Dynamically search for the correct Household/Family column based on the CSV format
	fam_col = next((c for c in ['Family Number', 'Family', 'Household Number', 'Household'] if c in df.columns), None)
	if not fam_col:
		fam_col = next((c for c in ['Dwelling Number', 'Dwelling', 'House Number'] if c in df.columns), None)
	
	if fam_col:
		df['Household_ID'] = (df[fam_col] != df[fam_col].shift()).cumsum()
	else:
		# Ultimate fallback if no family numbers exist in the census (treat every row individually)
		df['Household_ID'] = range(len(df))
	
	str_columns: List[str] = list(df.columns.astype(str))
	
	ged = ["0 HEAD", f"1 SOUR {SOFTWARE_NAME}", f"2 VERS {SOFTWARE_VERS}", f"2 CORP {ORG_NAME}", "1 GEDC",
	       "2 VERS 5.5.1", "2 FORM LINEAGE-LINKED", "1 CHAR UTF-8", f"1 DATE {CURRENT_DATE}",
	       f"1 COPR Copyright {COPYRIGHT_START}", "1 SUBM @SUB1@"]
	if GEDCOM_NOTE:
		ged.append(f"1 NOTE {GEDCOM_NOTE}")
		if GEDCOM_CONC: ged.append(f"2 CONC {GEDCOM_CONC}")
	
	fam_links: Dict[int, List[str]] = {i: [] for i in df.index}
	fam_blocks: List[str] = []
	used_fam_ids = set()
	media_dict: Dict[str, Dict[str, Any]] = {}
	task_blocks: List[str] = []
	folder_tasks: Dict[str, List[str]] = {}
	review_flags: Dict[int, List[Tuple[str, float]]] = {}
	
	for _, group in df.groupby('Household_ID'):
		if CENSUS_ERA == 'pre1850':
			units, unrelated, flags = [], [], []
		elif CENSUS_ERA == 'heuristic':
			units, unrelated, flags = parse_household(group)
		else:
			units, unrelated, flags = parse_household_relational(group)
		
		for flag in flags:
			flag_person = flag.get('person')
			if isinstance(flag_person, pd.Series):
				review_flags.setdefault(flag_person.name, []).append(
					(str(flag.get('reason', '')), float(flag.get('confidence', 0.0))))
		
		for person in unrelated: review_flags.setdefault(person.name, [])
		
		for u in units:
			h = u.get('husband')
			w = u.get('wife')
			children_list = u.get('children', [])
			anc = u.get('anchor')
			u_type = u.get('type', 'main')
			
			if h is None and w is None and not children_list: continue
			
			anchor_row = h if isinstance(h, pd.Series) else (w if isinstance(w, pd.Series) else (
				anc if isinstance(anc, pd.Series) else (children_list[0] if children_list else None)))
			if not isinstance(anchor_row, pd.Series): continue
			
			anchor_pid = clean_str(anchor_row.get('PID')) or str(ANCESTRY_START_RECORD_ID + anchor_row.name)
			
			base_f_id = f"@F{anchor_pid}_{u_type}@" if u_type != 'main' else f"@F{anchor_pid}@"
			f_id = base_f_id
			counter = 1
			while f_id in used_fam_ids:
				f_id = f"{base_f_id[:-1]}_{counter}@"
				counter += 1
			
			used_fam_ids.add(f_id)
			
			fam_blocks.append(f"0 {f_id} FAM")
			
			if isinstance(h, pd.Series):
				h_idx = clean_str(h.get('PID')) or str(ANCESTRY_START_RECORD_ID + h.name)
				fam_blocks.append(f"1 HUSB @I{h_idx}@")
				fam_links[h.name].append(f"1 FAMS {f_id}")
			if isinstance(w, pd.Series):
				w_idx = clean_str(w.get('PID')) or str(ANCESTRY_START_RECORD_ID + w.name)
				fam_blocks.append(f"1 WIFE @I{w_idx}@")
				fam_links[w.name].append(f"1 FAMS {f_id}")
			if isinstance(children_list, list):
				for child in children_list:
					if isinstance(child, pd.Series):
						c_idx = clean_str(child.get('PID')) or str(ANCESTRY_START_RECORD_ID + child.name)
						fam_blocks.append(f"1 CHIL @I{c_idx}@")
						fam_links[child.name].append(f"1 FAMC {f_id}")
			
			if (isinstance(h, pd.Series) and pd.notna(h.get('Married within Year'))) or (
					isinstance(w, pd.Series) and pd.notna(w.get('Married within Year'))):
				fam_blocks.extend(["1 MARR", f"2 DATE EST {CENSUS_YEAR}", "2 _PROOF proven"])
	
	for idx, row in df.iterrows():
		csv_pid = clean_str(row.get('PID', row.get('pid', '')))
		rec_id = csv_pid if csv_pid else str(ANCESTRY_START_RECORD_ID + idx)
		giv = clean_str(row.get('Given Name'))
		sur = clean_str(row.get('Surname'))
		gen = get_gender(row)
		
		# Used location refactoring here
		row_loc = get_location_string(row)
		
		# Storing extracted vars so that they can be used when generating the citation block.
		row_state = get_row_val(row, ['State', 'State/Province'], STATE)
		row_county = get_row_val(row, ['County', 'Parish'], COUNTY)
		row_town = get_row_val(row, ['City', 'Township', 'Town', 'Civil Division', 'Ward'], TOWNSHIP)
		row_country = get_row_val(row, ['Country'], 'USA')
		row_street = get_row_val(row, ['Street', 'Street Address', 'Address', 'House Number'], '')
		
		row_roll = get_row_val(row, ['Roll', 'Roll Number', 'NARA Roll'], ROLL_NUMBER)
		row_film = get_row_val(row, ['Film', 'FHL Film Number', 'Microfilm'], FILM_NUMBER)
		
		page = get_row_val(row, ['Page', 'Page_Number', 'Page Number'], '')
		real_page = get_row_val(row, ['Real Page', 'Real_Page', 'Page', 'Page_Number'], '')
		
		image_id_val = clean_str(row.get('Image_ID', ''))
		if not image_id_val: image_id_val = f"{BASE_ID}_{page.zfill(5)}"
		
		m_id = f"@M{image_id_val}@"
		image_name = image_id_val
		
		if image_name not in media_dict:
			img_path = Path(str(IMAGE_DIR)) / f"{image_name}.{IMAGE_EXTENSION}"
			media_dict[image_name] = {'id': m_id, 'img': img_path,
			                          'title': f"{CENSUS_YEAR} Census, {row_county}, Image {image_name}"}
		
		cit = build_citation_block(row, rec_id, m_id, real_page, target_software, row_town, row_county, row_state,
		                           row_country, row_roll, row_film)
		
		ged.extend([f"0 @I{rec_id}@ INDI", f"1 REFN {rec_id}", f"1 NAME {giv} /{sur}/"] + cit + [f"1 SEX {gen}",
			f"1 SOUR {ROOT_SOURCE_ID}", f"2 NAME Researcher: {RESEARCHER}", f"2 _TITL Researcher: {RESEARCHER}"])
		
		if person_flags := review_flags.get(idx, []):
			if target_software == "RM":
				fam_lbl_num = get_row_val(row, ['Family Number', 'Family', 'Household Number', 'Household'], '')
				lbl = f"{CENSUS_YEAR}, Fam {fam_lbl_num}, p.{real_page}"
				task_records, folder = build_relationship_task(rec_id, giv, sur, lbl, person_flags, cit,
				                                               media_dict[image_name]['img'],
				                                               str(media_dict[image_name]['title']))
				task_blocks.extend(task_records)
				folder_tasks.setdefault(folder, []).append(f"1 _TASK @T{rec_id}@")
				ged.extend([f"1 _TASK @T{rec_id}@", f"1 _COLOR {REVIEW_COLOR}"])
			else:
				for rev_reason, rev_conf in person_flags:
					ged.extend([f"1 NOTE NEEDS REVIEW ({rev_conf:.0%} conf): {rev_reason}"])
		
		b_yr = row.get('Birth Year')
		age = row.get('Age')
		birth_year = None
		if pd.notna(b_yr):
			try:
				birth_year = float(b_yr)
			except ValueError:
				pass
		elif pd.notna(age):
			try:
				birth_year = float(CENSUS_YEAR) - float(age)
			except ValueError:
				pass
		
		birth_place = clean_str(row.get('Birth Place', row.get('Birthplace', '')))
		
		if birth_year is not None or birth_place:
			ged.append("1 BIRT")
			if birth_year is not None:
				ged.append(f"2 DATE {get_birth_date(row, birth_year)}")
			if birth_place:
				ged.append(f"2 PLAC {birth_place}")
			ged.extend(cit)
		
		if occ := get_occupation_value(row):
			ged.extend([f"1 OCCU {occ}", f"2 DATE {CENSUS_YEAR}", f"2 PLAC {row_loc}", "2 _PROOF proven"] + cit)
		if race := clean_str(row.get('Race', row.get('Color', ''))):
			ged.extend([f"1 FACT {race}", "2 TYPE Race", f"2 DATE {CENSUS_YEAR}", "2 _PROOF proven"] + cit)
		edu_val = get_education_value(row)
		if edu_val is not None:
			ged.extend([f"1 EDUC" + (f" {edu_val}" if edu_val else ""), f"2 DATE {CENSUS_YEAR}", f"2 PLAC {row_loc}",
			            "2 _PROOF proven"] + cit)
		
		dyn_events, dyn_notes = build_dynamic_events_and_notes(row, cit, giv, str_columns, row_loc, row_street)
		ged.extend(dyn_events)
		
		cens_evt = [f"1 CENS", f"2 DATE {CENSUS_YEAR}", f"2 PLAC {row_loc}"]
		if row_street: cens_evt.append(f"2 ADDR {row_street}")
		cens_evt.extend([f"2 _PROOF proven"] + cit)
		ged.extend(cens_evt)
		
		if notes := (get_census_notes(row) + dyn_notes):
			ged.append(f"2 NOTE {' | '.join(notes)}")
		ged.extend(fam_links.get(idx, []))
	
	ged.extend(fam_blocks)
	ged.extend(task_blocks)
	
	for folder, tasks in folder_tasks.items():
		ged.append(f"0 _FOLDER {folder}")
		ged.extend(tasks)
	
	ged.extend(get_master_sources(target_software))
	for m in media_dict.values():
		ged.extend([f"0 {m['id']} OBJE", f"1 FILE {m['img']}", f"2 FORM {FORM_TYPE}", f"1 TITL {m['title']}"])
	
	if target_software == "RM":
		ged.extend(
			["0 _EVDEF Race", "1 TYPE P", "1 TITL Race", "1 ABBR Race", "1 SENT [person] was of [Desc] ethnicity.",
			 "1 PLAC N", "1 DATE Y", "1 DESC Y", "0 _EVDEF EDUC", "1 TYPE P", "1 TITL Education", "1 ABBR Education",
			 "1 SENT [person] was being educated < [Desc]>< [Date]>< [PlaceDetails]>< [Place].", "1 PLAC Y", "1 DATE Y",
			 "1 DESC Y", "0 _EVDEF Military Service", "1 TYPE P", "1 TITL Military Service", "1 ABBR Mil. Service",
			 "1 SENT [person] had military service.< [Desc]>< [Date]>.", "1 PLAC N", "1 DATE Y", "1 DESC Y"])
	
	ged.extend(["0 @SUB1@ SUBM", f"1 NAME {RESEARCHER}", f"1 ADDR {SUBM_ADDRESS}", f"1 NOTE {ORG_NAME}", "0 @R1@ REPO",
		f"1 NAME {REPOSITORY}", f"1 ADDR {REPOSITORY_LOC}", f"1 CALN {CALL_NUMBER}", "2 MEDI Electronic",
		f"2 _URL {COLLECTION_URL}", "0 TRLR"])
	
	base_name, ext = Path(GEDCOM_OUTPUT_NAME).stem, Path(GEDCOM_OUTPUT_NAME).suffix
	out_dir = Path(str(GEDCOM_OUTPUT_PATH)) if GEDCOM_OUTPUT_PATH else (
		Path(str(RM_DIR)) if target_software == "RM" else Path(str(FTM_DIR)))
	out_dir.mkdir(parents=True, exist_ok=True)
	
	output_path = out_dir / f"{base_name} - {target_software}{ext}"
	output_path.write_text("\n".join(ged), encoding="utf-8")
	print(f"Success! {len(df)} individuals converted and saved to {output_path}")


def get_csv_fallback(df: pd.DataFrame, columns: List[str], current: str) -> str:
	if current and str(current).strip() and str(current).strip() != '0':
		return str(current)
	for c in columns:
		if c in df.columns:
			valid_vals = df[df[c].astype(str).str.strip() != ''][c]
			if not valid_vals.empty:
				modes = valid_vals.mode()
				if not modes.empty:
					return clean_str(modes.iloc[0])
	return ""


if __name__ == "__main__":
	INPUT_CSV = Path(CSV_FILE) if os.path.isabs(CSV_FILE) else Path(str(CSV_DIR)) / CSV_FILE
	if not INPUT_CSV.is_file(): raise FileNotFoundError(f"CSV file not found: {INPUT_CSV}")
	
	# Read all columns as string to prevent pandas from coercing Dwelling/Family numbers to floats with .0
	census_df = pd.read_csv(INPUT_CSV, dtype=str, keep_default_na=False)
	
	for numeric_col in ['Age', 'Birth Year']:
		if numeric_col in census_df.columns:
			census_df[numeric_col] = pd.to_numeric(census_df[numeric_col], errors='coerce')
	
	# Fallback to CSV columns if environment variables are missing or 0
	STATE = get_csv_fallback(census_df, ['State', 'State/Province'], STATE)
	COUNTY = get_csv_fallback(census_df, ['County', 'Parish'], COUNTY)
	TOWNSHIP = get_csv_fallback(census_df, ['City', 'Township', 'Town', 'Civil Division', 'Ward'], TOWNSHIP)
	ROLL_NUMBER = get_csv_fallback(census_df, ['Roll', 'Roll Number', 'NARA Roll'], ROLL_NUMBER)
	FILM_NUMBER = get_csv_fallback(census_df, ['Film', 'FHL Film Number', 'Microfilm'], FILM_NUMBER)
	
	# Extended fallbacks for metadata passed from the Extractor
	CENSUS_YEAR_STR = get_csv_fallback(census_df, ['Census Year', 'Year', 'Census_Year'], str(CENSUS_YEAR))
	CENSUS_YEAR = int(CENSUS_YEAR_STR) if CENSUS_YEAR_STR and CENSUS_YEAR_STR.isdigit() else 0
	CENSUS_ERA = get_census_era(CENSUS_YEAR)
	
	APID_DB = get_csv_fallback(census_df, ['APID_DB', 'APID', 'Database ID', 'dbid'], APID_DB)
	COLLECTION_NAME = get_csv_fallback(census_df, ['Collection Name', 'Collection_Name', 'Collection'], COLLECTION_NAME)
	COLLECTION_URL = get_csv_fallback(census_df, ['Collection URL', 'Collection_URL', 'URL'], COLLECTION_URL)
	CENSUS_PUBLISHER = get_csv_fallback(census_df, ['Publisher', 'Census Publisher'], CENSUS_PUBLISHER)
	CENSUS_PUB_LOC = get_csv_fallback(census_df, ['Publisher Location', 'Pub Loc'], CENSUS_PUB_LOC)
	
	for software in ["RM", "FTM"]:
		build_gedcom(census_df, software)
