"""
RootsMagic Duplicate Finder & Task Generator.

Reads a RootsMagic 11 (.rmtree) SQLite database, uses fuzzy matching to find 
duplicates, and directly creates Task and Folder records in the database.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pandas as pd
from thefuzz import fuzz

# ==========================================
# CONFIGURATION
# ==========================================
RM_DATABASE_PATH: str = (
    r"C:\Users\Jason Cole\Documents\Genealogy\RootsMagic 11"
    r"\Trees\Pembina - North Dakota.rmtree"
)

# Threshold for people WITH birth years.
FUZZY_THRESHOLD: int = 82
# Max age gap for Pass 1.
MAX_AGE_GAP: int = 5
# Strict threshold for people WITHOUT birth years.
FUZZY_THRESHOLD_STRICT: int = 95
# Threshold for verifying if relatives' names match.
FAMILY_MATCH_THRESHOLD: int = 75

FOLDER_NAME: str = "!Duplicate Review"
# Color Set 2 (RootsMagic uses 0-indexed color sets)
COLOR_SET: int = 1
# 27 = Slate
COLOR_VALUE: int = 27


# ==========================================
# 1. DATABASE EXTRACTION
# ==========================================
def rmnocase(s1: Optional[str], s2: Optional[str]) -> int:
    """
    Mock collation sequence for RootsMagic's custom RMNOCASE.
    
    Provides case-insensitive sorting compatible with RootsMagic's internal 
    expectations to prevent SQLite operational errors.

    Args:
        s1 (Optional[str]): The first string to compare.
        s2 (Optional[str]): The second string to compare.

    Returns:
        int: 0 if equal, -1 if s1 < s2, 1 if s1 > s2.
    """
    s1_clean = s1.lower() if s1 else ""
    s2_clean = s2.lower() if s2 else ""
    if s1_clean == s2_clean:
        return 0
    return -1 if s1_clean < s2_clean else 1


def extract_people_from_rm(db_path: str) -> pd.DataFrame:
    """
    Connects to the RM 11 SQLite database and extracts primary names, 
    birth years, and a list of immediate family members to provide context 
    for deduplication.

    Args:
        db_path (str): The absolute path to the RootsMagic database.

    Returns:
        pd.DataFrame: A DataFrame containing cleaned person records and 
                      their associated relative names.
                      
    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"Could not find RM database at {db_path}")

    print("Connecting to RootsMagic 11 database (Read-Only)...")
    
    # Connect in read-only mode to prevent accidental modifications during read
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.create_collation("RMNOCASE", rmnocase)
    
    query = """
        SELECT 
            OwnerID AS PersonID,
            Given,
            Surname,
            BirthYear
        FROM NameTable
        WHERE IsPrimary = 1
    """
    
    df = pd.read_sql_query(query, conn)
    
    # Clean and standardize the extracted text data
    df['Given'] = df['Given'].fillna('').astype(str).str.strip()
    df['Surname'] = df['Surname'].fillna('').astype(str).str.strip()
    df['FullName'] = df['Given'] + " " + df['Surname']
    
    # Coerce birth years to integers, defaulting to 0 for unknown
    df['BirthYear'] = (
        pd.to_numeric(df['BirthYear'], errors='coerce')
        .fillna(0)
        .astype(int)
    )
    
    # --- EXTRACT FAMILY CONTEXT ---
    print("Extracting family relationship contexts...")
    relatives_map: Dict[int, Set[int]] = {}
    
    try:
        # 1. Spouses (Couples)
        couples_df = pd.read_sql_query(
            "SELECT FatherID, MotherID FROM FamilyTable "
            "WHERE FatherID > 0 AND MotherID > 0", 
            conn
        )
        for _, row in couples_df.iterrows():
            f, m = row['FatherID'], row['MotherID']
            relatives_map.setdefault(f, set()).add(m)
            relatives_map.setdefault(m, set()).add(f)
            
        # 2. Parents & Children
        children_query = (
            "SELECT F.FatherID, F.MotherID, C.ChildID "
            "FROM FamilyTable F "
            "JOIN ChildTable C ON F.FamilyID = C.FamilyID"
        )
        children_df = pd.read_sql_query(children_query, conn)
        
        for _, row in children_df.iterrows():
            f, m, c = row['FatherID'], row['MotherID'], row['ChildID']
            if f > 0 and c > 0:
                relatives_map.setdefault(f, set()).add(c)
                relatives_map.setdefault(c, set()).add(f)
            if m > 0 and c > 0:
                relatives_map.setdefault(m, set()).add(c)
                relatives_map.setdefault(c, set()).add(m)
                
    except Exception as e:
        print(
            f"Warning: Could not fully extract family tables. "
            f"Proceeding with basic matching. Error: {e}"
        )
    finally:
        conn.close()
        
    # Map PersonID to FullName for fast translation during list comprehension
    id_to_name = dict(zip(df['PersonID'], df['FullName']))
    
    # Build a list of relatives' names for each person
    df['RelativesList'] = df['PersonID'].map(
        lambda pid: [
            id_to_name.get(rid) 
            for rid in relatives_map.get(pid, set()) 
            if id_to_name.get(rid)
        ]
    )
    
    print(
        f"Extracted {len(df)} individuals and their family contexts "
        f"from the database."
    )
    return df


# ==========================================
# 2. FUZZY MATCHING
# ==========================================
def find_fuzzy_duplicates(
    df: pd.DataFrame, 
    run_pass_two: bool = False, 
    test_limit: Optional[int] = None
) -> pd.DataFrame:
    """
    Uses fuzzy string matching to find individuals with similar names and 
    birth years. Optimized by converting DataFrames to dictionaries for faster 
    O(N^2) traversal.

    Args:
        df (pd.DataFrame): The DataFrame of extracted person records.
        run_pass_two (bool): Whether to run the strict pass for unknown ages.
        test_limit (Optional[int]): Limit the number of outer loop iterations 
                                    for testing.

    Returns:
        pd.DataFrame: A DataFrame of identified matches and their scores.
    """
    print("Running fuzzy matching (this may take a minute)...")
    matches: List[Dict[str, Any]] = []
    
    # Separate datasets for the two passes and convert to dicts for speed
    df_known_age = (
        df[df['BirthYear'] > 0]
        .copy()
        .sort_values('BirthYear')
        .reset_index(drop=True)
    )
    df_unknown_age = df[df['BirthYear'] == 0].copy()
    
    known_records = df_known_age.to_dict('records')
    unknown_records = df_unknown_age.to_dict('records')
    all_records = df.to_dict('records')
    
    total_compared = 0
    
    # PASS 1: Known Age vs Known Age
    total_known = len(known_records)
    pass_1_limit = (
        test_limit if test_limit and test_limit < total_known 
        else total_known
    )
    
    print(
        f" - Pass 1: Comparing {pass_1_limit} records with known "
        f"birth years..."
    )
    
    for i in range(pass_1_limit):
        p1 = known_records[i]
        p1_name = p1['FullName']
        p1_yr = p1['BirthYear']
        p1_id = p1['PersonID']
        p1_rels = p1['RelativesList']
        
        # In-place progress update (carriage return prevents console spam)
        print(
            f"\r   Processing {i+1}/{pass_1_limit}: {p1_name[:30]:<30}", 
            end="", 
            flush=True
        )
        
        for j in range(i + 1, total_known):
            p2 = known_records[j]
            age_diff = abs(p1_yr - p2['BirthYear'])
            
            # Break early utilizing sorted dataset (sliding window optimization)
            if age_diff > MAX_AGE_GAP:
                break
                
            total_compared += 1
            score = fuzz.token_set_ratio(p1_name, p2['FullName'])
            
            if score >= FUZZY_THRESHOLD:
                p2_rels = p2['RelativesList']
                family_conflict = False
                family_match = False
                
                # --- FAMILY CONTEXT FILTER ---
                if p1_rels and p2_rels:
                    for r1 in p1_rels:
                        for r2 in p2_rels:
                            rel_score = fuzz.token_set_ratio(r1, r2)
                            if rel_score >= FAMILY_MATCH_THRESHOLD:
                                family_match = True
                                break
                        if family_match:
                            break
                    
                    if not family_match:
                        # They both have relatives, but none match. 
                        # High chance of false positive!
                        family_conflict = True
                        
                if family_conflict:
                    continue  # Skip this match entirely.
                
                matches.append({
                    'Score': score,
                    'Family_Verified': family_match,
                    'ID_1': p1_id,
                    'Given_1': p1['Given'],
                    'Surname_1': p1['Surname'],
                    'Name_1': p1_name,
                    'BirthYear_1': p1_yr,
                    'ID_2': p2['PersonID'],
                    'Given_2': p2['Given'],
                    'Surname_2': p2['Surname'],
                    'Name_2': p2['FullName'],
                    'BirthYear_2': p2['BirthYear'],
                    'Age_Gap': age_diff
                })
    print() 

    # PASS 2: Unknown Age vs All
    if run_pass_two and not test_limit:
        total_unknown = len(unknown_records)
        print(
            f" - Pass 2: Comparing {total_unknown} records missing "
            f"birth years (strict name matching)..."
        )
        for i in range(total_unknown):
            p1 = unknown_records[i]
            p1_name = p1['FullName']
            p1_id = p1['PersonID']
            p1_rels = p1['RelativesList']
            
            print(
                f"\r   Processing {i+1}/{total_unknown}: {p1_name[:30]:<30}", 
                end="", 
                flush=True
            )
            
            for j in range(len(all_records)):
                p2 = all_records[j]
                
                # Prevent matching a person to themselves and duplicate pairs
                if p1_id >= p2['PersonID']:
                    continue
                    
                total_compared += 1
                score = fuzz.token_set_ratio(p1_name, p2['FullName'])
                
                if score >= FUZZY_THRESHOLD_STRICT:
                    p2_rels = p2['RelativesList']
                    family_conflict = False
                    family_match = False
                    
                    # --- FAMILY CONTEXT FILTER ---
                    if p1_rels and p2_rels:
                        for r1 in p1_rels:
                            for r2 in p2_rels:
                                rel_score = fuzz.token_set_ratio(r1, r2)
                                if rel_score >= FAMILY_MATCH_THRESHOLD:
                                    family_match = True
                                    break
                            if family_match:
                                break
                        
                        if not family_match:
                            family_conflict = True
                            
                    if family_conflict:
                        continue  # Skip this match entirely.
                        
                    matches.append({
                        'Score': score,
                        'Family_Verified': family_match,
                        'ID_1': p1_id,
                        'Given_1': p1['Given'],
                        'Surname_1': p1['Surname'],
                        'Name_1': p1_name,
                        'BirthYear_1': 'Unknown',
                        'ID_2': p2['PersonID'],
                        'Given_2': p2['Given'],
                        'Surname_2': p2['Surname'],
                        'Name_2': p2['FullName'],
                        'BirthYear_2': (
                            p2['BirthYear'] if p2['BirthYear'] > 0 else 'Unknown'
                        ),
                        'Age_Gap': 'Unknown'
                    })
        print()
    elif test_limit:
        print(" - Pass 2: Skipped due to Test Mode.")
    else:
        print(" - Pass 2: Skipped based on user selection.")

    print(f"Compared {total_compared} plausible pairs.")
    return pd.DataFrame(matches)


# ==========================================
# 3. DIRECT DATABASE WRITER
# ==========================================
def write_tasks_to_db(matches_df: pd.DataFrame, db_path: str) -> None:
    """
    Writes the tasks directly into the RootsMagic database Task tables.
    Also handles deduplication of Folders and clean-up of previous script runs.

    Args:
        matches_df (pd.DataFrame): DataFrame containing matched duplicates.
        db_path (str): The absolute path to the RootsMagic database.
    """
    if matches_df.empty:
        return
        
    print("Writing tasks directly to the RootsMagic database...")
    
    # Wait 1 second to ensure filesystem releases any lingering read locks
    time.sleep(1)
    
    conn = sqlite3.connect(db_path)
    conn.create_collation("RMNOCASE", rmnocase)
    cursor = conn.cursor()

    # --- NEW FOLDER DEDUPLICATION LOGIC ---
    # Find folders with the exact same name to merge them
    cursor.execute('''
        SELECT TagName 
        FROM TagTable 
        WHERE TagType = 1 
        GROUP BY TagName 
        HAVING COUNT(TagID) > 1
    ''')
    duplicate_folder_names = cursor.fetchall()
    
    if duplicate_folder_names:
        print(
            f"Found {len(duplicate_folder_names)} folder name(s) "
            f"with duplicates. Merging..."
        )
        
        for (f_name,) in duplicate_folder_names:
            # Get all TagIDs for this exact folder name, sorted to keep oldest
            cursor.execute(
                'SELECT TagID FROM TagTable '
                'WHERE TagType = 1 AND TagName = ? ORDER BY TagID', 
                (f_name,)
            )
            ids = [row[0] for row in cursor.fetchall()]
            
            survivor_id = ids[0]
            duplicate_ids = ids[1:]
            
            for dupe_id in duplicate_ids:
                # Move tasks from duplicate folder to survivor folder
                cursor.execute('''
                    UPDATE TaskLinkTable 
                    SET OwnerID = ? 
                    WHERE OwnerType = 18 AND OwnerID = ?
                ''', (survivor_id, dupe_id))
                
                # Delete the duplicate folder
                cursor.execute('DELETE FROM TagTable WHERE TagID = ?', (dupe_id,))
                
        print("Folder merge complete!")
    
    # --- CLEAN UP PREVIOUS MATCH TASKS ---
    print("Clearing out previous auto-generated match tasks...")
    
    # 1. Clear assigned colors for anyone previously linked to an MGS match task
    cursor.execute(f'''
        UPDATE PersonTable 
        SET Color{COLOR_SET} = 0
        WHERE PersonID IN (
            SELECT OwnerID FROM TaskLinkTable 
            WHERE OwnerType = 0 AND TaskID IN (
                SELECT TaskID FROM TaskTable WHERE RefNumber LIKE 'MGS-%'
            )
        )
    ''')
    
    # 2. Delete the links for the match tasks
    cursor.execute('''
        DELETE FROM TaskLinkTable 
        WHERE TaskID IN (
            SELECT TaskID FROM TaskTable WHERE RefNumber LIKE 'MGS-%'
        )
    ''')
    
    # 3. Delete the match tasks themselves
    cursor.execute("DELETE FROM TaskTable WHERE RefNumber LIKE 'MGS-%'")

    # Clean up ghost folder mistakenly created in TaskTable from older runs
    try:
        cursor.execute(
            "DELETE FROM TaskTable WHERE TaskType = 1 AND Name = ?", 
            (FOLDER_NAME,)
        )
    except sqlite3.OperationalError:
        pass
    
    utc_mod_date = 0.0 
    
    # Ensure the folder exists in TagTable (TagType = 1 = Task Folder)
    cursor.execute(
        "SELECT TagID FROM TagTable WHERE TagType = 1 AND TagName = ?", 
        (FOLDER_NAME,)
    )
    folder = cursor.fetchone()
    
    if folder:
        folder_id = folder[0]
    else:
        cursor.execute('''
            INSERT INTO TagTable 
            (TagType, TagValue, TagName, Description, UTCModDate) 
            VALUES (1, 0, ?, '', ?)
        ''', (FOLDER_NAME, utc_mod_date))
        folder_id = cursor.lastrowid
        
    tasks_added = 0
    for idx, row in matches_df.iterrows():
        score = row['Score']
        is_verified = row['Family_Verified']
        
        # Calculate priority (0 is highest, 4 is lowest)
        if score == 100:
            priority = 0
        elif score > 95:
            priority = 1
        elif score > 90:
            priority = 2
        elif score > 85:
            priority = 3
        else:
            priority = 4
            
        # Boost priority if family confirmed, demote if lacking data
        if is_verified:
            priority = max(0, priority - 1)
            fam_str = "YES"
        else:
            priority = min(4, priority + 1)
            fam_str = "Unverified (Missing Data)"
        
        task_name = f"Review Merge: {row['Name_1']} & {row['Name_2']}"
        task_details = (
            f"Match Score: {score}%. Age Gap: {row['Age_Gap']}. "
            f"Family Verified: {fam_str}. "
            f"Compare IDs: {row['ID_1']} & {row['ID_2']}"
        )
        ref_num = f"MGS-{row['ID_1']}-{row['ID_2']}"
        
        # Insert single main Task (TaskType = 2 based on user's DB schema)
        cursor.execute('''
            INSERT INTO TaskTable (
                TaskType, RefNumber, Name, Status, Priority, 
                Date1, Date2, Date3, SortDate1, SortDate2, SortDate3, 
                Filename, Details, Results, UTCModDate, Exclude
            )
            VALUES (2, ?, ?, 0, ?, '', '', '', 0, 0, 0, '', ?, '', ?, 0)
        ''', (ref_num, task_name, priority, task_details, utc_mod_date))
        
        task_id = cursor.lastrowid
        
        # Bundle links for Person 1 (0), Person 2 (0), and Tag/Folder (18)
        links = [
            (task_id, 0, row['ID_1'], utc_mod_date),
            (task_id, 0, row['ID_2'], utc_mod_date),
            (task_id, 18, folder_id, utc_mod_date)
        ]
        
        cursor.executemany('''
            INSERT INTO TaskLinkTable (TaskID, OwnerType, OwnerID, UTCModDate) 
            VALUES (?, ?, ?, ?)
        ''', links)
        
        # Apply Slate color to both individuals in Color Set 2
        for p_id in (row['ID_1'], row['ID_2']):
            cursor.execute(f'''
                UPDATE PersonTable 
                SET Color{COLOR_SET} = ? 
                WHERE PersonID = ?
            ''', (COLOR_VALUE, p_id))
        
        tasks_added += 1
        
    conn.commit()
    conn.close()
    print(f"Successfully wrote {tasks_added} tasks directly to the database!")


# ==========================================
# MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    # flush=True ensures the UI instantly receives the print statements
    print("==========================================", flush=True)
    print("RootsMagic Duplicate Finder", flush=True)
    print("==========================================", flush=True)
    print("Select run mode:", flush=True)
    print(
        " 1. Pass 1 only (Compare records with known birth years - FASTER)", 
        flush=True
    )
    print(
        " 2. Pass 1 & 2 (Include strict comparisons for missing birth years "
        "- SLOWER)", 
        flush=True
    )
    print(
        " 3. TEST MODE (Only process the first 100 individuals to quickly "
        "test database writing)", 
        flush=True
    )
    choice = input("Enter 1, 2, or 3 (default 1): ").strip()
    print("==========================================\n", flush=True)
    
    run_pass_two = (choice == "2")
    test_limit = 100 if choice == "3" else None

    people_df = extract_people_from_rm(RM_DATABASE_PATH)
    suggestions_df = find_fuzzy_duplicates(
        people_df, 
        run_pass_two=run_pass_two, 
        test_limit=test_limit
    )
    
    if not suggestions_df.empty:
        suggestions_df = suggestions_df.sort_values('Score', ascending=False)
        
        # Backup warning/confirmation before writing
        print("\n" + "!" * 60)
        print("WARNING: This will write directly to your database.")
        print("1. Ensure you have a backup of your .rmtree file!")
        print("2. CRITICAL: RootsMagic MUST BE CLOSED completely.")
        print("   If RM is open, it will overwrite and erase these changes.")
        print("!" * 60)
        
        while True:
            confirm = input(
                "Type 'yes' to write tasks to the database, or 'no' to abort: "
            ).strip().lower()
            
            if confirm == 'yes':
                write_tasks_to_db(suggestions_df, RM_DATABASE_PATH)
                print(
                    f"\nSUCCESS! Found and linked {len(suggestions_df)} "
                    f"potential duplicates."
                )
                print(
                    "Tasks have been attached directly to the individuals "
                    "in RootsMagic."
                )
                print("You can now open RootsMagic and check the Task List!")
                break
            elif confirm == 'no':
                print("\nDatabase write aborted. No changes were made.")
                break
            else:
                print("Invalid input. Please type 'yes' or 'no'.")
    else:
        print(
            "\nNo fuzzy duplicates found based on the current threshold.", 
            flush=True
        )