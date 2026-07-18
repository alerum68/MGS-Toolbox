"""
RootsMagic Historical County Fixer.

This script updates a RootsMagic database by analyzing geocoded timeline markers
and comparing them against historical shapefiles (like the Newberry Atlas).
It intelligently forks existing places, updating their display names to reflect
the correct historical county or territory boundaries active on the date of
the event, while preserving original FamilySearch and Ancestry.com tracking IDs and coordinates.
"""

import calendar
import datetime
import os
import re
import shutil
import sqlite3
import warnings
from typing import Any, Dict, List, Optional, Tuple

import geopandas as gpd
from dotenv import load_dotenv
from shapely.geometry import Point
from tqdm import tqdm

# Load environment variables from a .env file if present, overriding any
# existing environment variables with the values from the file.
load_dotenv(override=True)


# ==========================================
# CONFIGURATION
# ==========================================
PROGRAM_DIR = os.getenv("PROGRAM_DIR", "")

# Resolve database path
_rm_db = os.getenv(
    "COUNTY_RM_DATABASE",
    "RootsMagic 11/Trees/Pembina - North Dakota.rmtree"
)
RM_DATABASE = _rm_db if os.path.isabs(_rm_db) else os.path.join(PROGRAM_DIR, _rm_db)

# Resolve shapefile path
_shape = os.getenv(
    "COUNTY_SHAPEFILE",
    "Python/County FIx/US_AtlasHCB_Counties/US_HistCounties_Shapefile/US_HistCounties.shp"
)
SHAPEFILE_PATH = _shape if os.path.isabs(_shape) else os.path.join(PROGRAM_DIR, _shape)

# Boolean configuration flags
DEBUG_MODE = str(
    os.getenv("COUNTY_DEBUG_MODE", "True")
).lower() in ('true', '1', 'yes')
CREATE_BACKUP = str(
    os.getenv("COUNTY_CREATE_BACKUP", "False")
).lower() in ('true', '1', 'yes')


# ==========================================
# GLOBALS & COMPILED REGEX
# ==========================================
# Global variables for tracking progress across functions without UI bounce
_progress_bar: Optional[tqdm] = None
_updated_count: int = 0

# RootsMagic specific date string formats
DATE_PATTERN_D = re.compile(r'^D.([+-])(\d{4})(\d{2})(\d{2})')
DATE_PATTERN_T_FULL = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
DATE_PATTERN_T_YEAR = re.compile(r'(\d{4})')

# A comprehensive set of states/territories to prevent them from being
# accidentally parsed as local cities.
US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
    "dakota territory", "minnesota territory", "illinois territory",
    "indiana territory", "michigan territory", "wisconsin territory",
    "iowa territory", "missouri territory", "northwest territory",
    "oregon territory", "washington territory", "utah territory",
    "new mexico territory", "nebraska territory", "kansas territory",
    "colorado territory", "nevada territory", "idaho territory",
    "arizona territory", "montana territory", "wyoming territory",
    "hawaii territory", "alaska territory", "indian territory",
    "united states", "united states of america", "usa", "u.s.a.", "us", "u.s.",
    "canada", "uk", "united kingdom", "england", "france", "germany", "ireland",
    "scotland", "mexico"
}


# ==========================================
# UTILITY FUNCTIONS
# ==========================================
def update_ui() -> None:
    """Update the fixed progress bar postfix without causing horizontal bounce."""
    if _progress_bar is not None:
        _progress_bar.set_postfix({'Fixed': _updated_count}, refresh=False)


def debug_print(message: str) -> None:
    """
    Print debug messages cleanly on a new line above the static progress bar.

    Args:
        message (str): The debug message to print.
    """
    if not DEBUG_MODE:
        return

    if _progress_bar is not None:
        _progress_bar.write(f"   -> {message}")
    else:
        print(f"   -> {message}")


def parse_rm_date(date_str: str) -> Optional[str]:
    """
    Parse RootsMagic proprietary date strings into standard YYYY-MM-DD format.

    Args:
        date_str (str): The raw date string from the database.

    Returns:
        Optional[str]: Formatted date string, or None if invalid/unparseable.
    """
    if not date_str or date_str == '.':
        return None

    year, month, day = 0, 1, 1

    # D-format: Typically D.[+/-][YYYY][MM][DD]
    if date_str.startswith('D'):
        m = DATE_PATTERN_D.match(date_str)
        if not m:
            return None
        sign, y_str, mo_str, d_str = m.groups()
        if sign == '-':  # Skip BC dates for historical county tracking
            return None
        year, month, day = int(y_str), int(mo_str), int(d_str)

    # T-format: Typically textual or ISO-like T[YYYY]-[MM]-[DD]
    elif date_str.startswith('T'):
        m_full = DATE_PATTERN_T_FULL.search(date_str)
        if m_full:
            year, month, day = (
                int(m_full.group(1)),
                int(m_full.group(2)),
                int(m_full.group(3))
            )
        else:
            m_year = DATE_PATTERN_T_YEAR.search(date_str)
            if not m_year:
                return None
            year = int(m_year.group(1))
    else:
        return None

    if year == 0:
        return None

    # Sanitize month and day to prevent calendar errors
    month = max(1, min(month, 12))
    day = max(1, day)

    # Clamp the day to the maximum allowed days for the specific month/year
    last_day = calendar.monthrange(year, month)[1]
    if day > last_day:
        day = last_day

    return f"{year:04d}-{month:02d}-{day:02d}"


def extract_local_parts(place_name: str) -> str:
    """
    Intelligently extract the local city/town from a place name string.

    Completely strips redundant state, county, or country suffixes that would
    cause compound errors during reconstruction.

    Args:
        place_name (str): The original full place string.

    Returns:
        str: The isolated local city/town name, or an empty string.
    """
    if not place_name:
        return ""

    parts = [p.strip() for p in place_name.split(',')]

    # Short places (e.g., "Minnesota, USA" or "United States")
    # If the first part is a state/country, there is no local city.
    if len(parts) <= 2:
        if parts[0].lower() in US_STATES:
            return ""
        return parts[0]

    # Standard RM Place (City, County, State, Country) or larger
    if len(parts) >= 4:
        # Strip the last 3 elements to isolate the local entity.
        return ", ".join(parts[:-3])

    # 3-part place (e.g., City, State, Country)
    if len(parts) == 3:
        return parts[0]

    return parts[0]


def clean_shapefile_name(name: str) -> str:
    """
    Clean historical names extracted from the shapefile dataset.

    Forces ALL CAPS shapefile data into clean Title Case and expands
    shorthand abbreviations into standard genealogical terms.

    Args:
        name (str): The raw string from the shapefile.

    Returns:
        str: The cleaned and expanded place name.
    """
    if not name:
        return ""

    name = name.title()

    replacements = {
        "Terr.": "Territory",
        "Unorg.": "Unorganized",
        "Fed.": "Federal",
        "Bdry.": "Boundary",
        "Nca": "NCA",
        "Dist.": "District",
        " De ": " de "
    }

    for old, new in replacements.items():
        name = name.replace(old, new)

    return name.strip()


def create_reverse_place(place_name: str) -> str:
    """
    Reverse the comma-separated parts of a place name.

    Used by RootsMagic to create the 'Reverse' column in the PlaceTable for
    sorting places geographically (e.g., "USA, North Dakota, Pembina").

    Args:
        place_name (str): The standard place name.

    Returns:
        str: The reversed place name.
    """
    if not place_name:
        return ""
    parts = [part.strip() for part in place_name.split(',')]
    parts.reverse()
    return ", ".join(parts)


# ==========================================
# DATABASE OPERATIONS
# ==========================================
def clone_historical_place(
    cursor: sqlite3.Cursor,
    original_place_id: int,
    new_place_name: str,
    columns: List[str]
) -> int:
    """
    Clone a place record, maintaining coordinates and UUIDs, with a new name.

    Checks if the new place already exists to avoid duplicates.

    Args:
        cursor (sqlite3.Cursor): The active database cursor.
        original_place_id (int): The PlaceID of the template record.
        new_place_name (str): The corrected historical name.
        columns (List[str]): List of all columns in the PlaceTable.

    Returns:
        int: The PlaceID of the new or existing historical place.
    """
    # Check if the target place name already exists
    cursor.execute(
        "SELECT PlaceID FROM PlaceTable WHERE Name = ?",
        (new_place_name,)
    )
    result = cursor.fetchone()
    if result:
        debug_print(
            f"Place '{new_place_name}' exists (ID: {result[0]}). Reusing."
        )
        return result[0]

    # Fetch the original place data to use as a template
    cursor.execute(
        "SELECT * FROM PlaceTable WHERE PlaceID = ?",
        (original_place_id,)
    )
    original_data = cursor.fetchone()

    insert_cols = []
    insert_vals = []
    placeholders = []

    new_reverse_name = create_reverse_place(new_place_name)

    # Build the insert query dynamically, replacing name and reverse name
    for col, val in zip(columns, original_data):
        if col == 'PlaceID':
            continue  # Let SQLite auto-increment the ID
        elif col == 'Name':
            insert_cols.append(col)
            insert_vals.append(new_place_name)
            placeholders.append('?')
        elif col == 'Reverse':
            insert_cols.append(col)
            insert_vals.append(new_reverse_name)
            placeholders.append('?')
        else:
            insert_cols.append(col)
            insert_vals.append(val)
            placeholders.append('?')

    insert_sql = (
        f"INSERT INTO PlaceTable ({', '.join(insert_cols)}) "
        f"VALUES ({', '.join(placeholders)})"
    )
    cursor.execute(insert_sql, insert_vals)

    return cursor.lastrowid


def get_or_create_place_detail(
    cursor: sqlite3.Cursor,
    new_place_id: int,
    detail_name: str,
    original_site_id: int,
    columns: List[str]
) -> int:
    """
    Clone a place detail (like a hospital or church) to the newly created place.

    Args:
        cursor (sqlite3.Cursor): The active database cursor.
        new_place_id (int): The MasterID linking to the parent place.
        detail_name (str): The name of the detail location.
        original_site_id (int): The PlaceID of the original detail record.
        columns (List[str]): List of all columns in the PlaceTable.

    Returns:
        int: The SiteID (PlaceID) of the new or existing detail record.
    """
    if not detail_name:
        return 0

    # PlaceType = 2 denotes a place detail in RootsMagic
    cursor.execute("""
        SELECT PlaceID FROM PlaceTable
        WHERE MasterID = ? AND Name = ? AND PlaceType = 2
    """, (new_place_id, detail_name))

    result = cursor.fetchone()
    if result:
        return result[0]

    cursor.execute(
        "SELECT * FROM PlaceTable WHERE PlaceID = ?",
        (original_site_id,)
    )
    original_data = cursor.fetchone()

    insert_cols = []
    insert_vals = []
    placeholders = []

    for col, val in zip(columns, original_data):
        if col == 'PlaceID':
            continue
        elif col == 'MasterID':
            insert_cols.append(col)
            insert_vals.append(new_place_id)
            placeholders.append('?')
        else:
            insert_cols.append(col)
            insert_vals.append(val)
            placeholders.append('?')

    insert_sql = (
        f"INSERT INTO PlaceTable ({', '.join(insert_cols)}) "
        f"VALUES ({', '.join(placeholders)})"
    )
    cursor.execute(insert_sql, insert_vals)

    return cursor.lastrowid


# ==========================================
# MAIN EXECUTION
# ==========================================
def main() -> None:
    """
    Main execution loop for the historical county fixer.

    Loads spatial data, iterates over timeline events in the RootsMagic database,
    cross-references dates and coordinates with historical polygons, and forks
    places if a historical mismatch is found.
    """
    print("Loading Newberry Historical Shapefiles...")
    try:
        # Load the shapefile and convert coordinate reference system to standard GPS
        gdf = gpd.read_file(SHAPEFILE_PATH).to_crs("EPSG:4326")
        print("Shapefiles loaded.\n")
    except Exception as e:
        print(f"Failed to load shapefiles: {e}")
        return

    if not os.path.exists(RM_DATABASE):
        print(f"Error: Database file not found at {RM_DATABASE}")
        return

    if CREATE_BACKUP:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{RM_DATABASE}.{timestamp}.bak"
        shutil.copy2(RM_DATABASE, backup_path)
        print(f"Backup created at: {backup_path}")

    print(f"Connecting to Database: {RM_DATABASE}")
    conn = sqlite3.connect(RM_DATABASE)

    # Register RootsMagic's custom collation sequence to prevent SQL errors
    def rmnocase_collation(a: str, b: str) -> int:
        a, b = a.lower(), b.lower()
        if a == b:
            return 0
        return -1 if a < b else 1

    conn.create_collation("RMNOCASE", rmnocase_collation)
    cursor = conn.cursor()

    # Retrieve table schema dynamically to ensure compatibility across RM versions
    cursor.execute("PRAGMA table_info(PlaceTable)")
    place_table_columns = [row[1] for row in cursor.fetchall()]

    # Fetch all geocoded events mapped to a place
    query = """
        SELECT e.EventID, e.Date, p.PlaceID, p.Name, p.Latitude, p.Longitude,
               e.SiteID, pd.Name AS DetailName
        FROM EventTable e
        JOIN PlaceTable p ON e.PlaceID = p.PlaceID
        LEFT JOIN PlaceTable pd ON e.SiteID = pd.PlaceID
        WHERE p.Latitude != 0 AND p.Longitude != 0
        AND p.Latitude IS NOT NULL AND p.Longitude IS NOT NULL
    """
    cursor.execute(query)
    events = cursor.fetchall()

    # Enable ANSI escape sequences on Windows terminals
    if os.name == 'nt':
        os.system('')

    print(f"Evaluating {len(events)} geocoded timeline markers...\n")
    print("-" * 50)

    global _progress_bar, _updated_count
    _updated_count = 0

    custom_format = (
        "{desc}: {percentage:3.0f}% |{bar:35}| "
        "{n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}"
    )

    # mininterval=0.2 throttles the redraws to prevent GUI flickering
    with tqdm(
        events, desc="Mapping", bar_format=custom_format, colour="green",
        dynamic_ncols=True, mininterval=0.2
    ) as bar:
        _progress_bar = bar
        for event in bar:
            event_id, raw_date, place_id, current_name, lat, lon, site_id, detail_name = event

            target_date = parse_rm_date(raw_date)
            if not target_date:
                debug_print(f"[{current_name}] No usable date")
                continue

            # Filter shapefiles down to polygons active on the event date
            try:
                active_polygons = gdf[
                    (gdf['START_DATE'] <= target_date) &
                    (gdf['END_DATE'] >= target_date)
                ]
            except Exception as e:
                debug_print(f"[{current_name}] Bad date compare ({e})")
                continue

            # RM stores coordinates internally multiplied by 10,000,000
            real_lat = lat / 1e7
            real_lon = lon / 1e7
            target_point = Point(real_lon, real_lat)

            # Perform the spatial point-in-polygon check
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                matched_mask = active_polygons.geometry.contains(target_point)
                matched = active_polygons[matched_mask]

            if matched.empty:
                debug_print(
                    f"[{current_name}] No historical polygon matched for {target_date}"
                )
                continue

            # 1. Extract local city cleanly
            local_city = extract_local_parts(current_name)

            # 2. Fix capitalization and abbreviations from Shapefile
            county_val = clean_shapefile_name(matched.iloc[0]['NAME'])
            state_val = clean_shapefile_name(matched.iloc[0]['STATE_TERR'])

            # 3. Smart County Processing (Handles unorganized territories and pseudo-counties)
            final_county = ""
            lower_county = county_val.lower()
            pseudo_keywords = [
                'territory', 'unorganized', 'nca', 'de facto', 'new pur',
                'boundary', 'ext', 'dist', 'district', 'tract', 'reserve'
            ]

            # If the county is just the state name repeated (e.g. Unorganized State Land)
            if lower_county == state_val.lower():
                final_county = ""
            # If it contains pseudo-county keywords, don't blindly append "County"
            elif any(kw in lower_county for kw in pseudo_keywords):
                final_county = county_val
            else:
                if state_val.lower() == "louisiana":
                    final_county = f"{county_val} Parish"
                else:
                    final_county = f"{county_val} County"

            # 4. Construct the pristine new place string
            components = []
            if local_city:
                components.append(local_city)
            if final_county:
                components.append(final_county)
            if state_val:
                components.append(state_val)
            components.append("USA")

            new_place_name = ", ".join(components)

            # 5. Database update sequence if the name requires a change
            if new_place_name != current_name:
                # Fork the main place record
                new_place_id = clone_historical_place(
                    cursor, place_id, new_place_name, place_table_columns
                )

                new_site_id = 0
                if site_id and site_id > 0 and detail_name:
                    # Fork the associated site/detail record (if present)
                    new_site_id = get_or_create_place_detail(
                        cursor, new_place_id, detail_name, site_id,
                        place_table_columns
                    )
                else:
                    new_site_id = site_id

                # Redirect the specific historical event to the newly forked place
                cursor.execute("""
                    UPDATE EventTable
                    SET PlaceID = ?, SiteID = ?
                    WHERE EventID = ?
                """, (new_place_id, new_site_id, event_id))

                _updated_count += 1
                update_ui()

                # Print cleanly above the bar without disrupting the UI
                bar.write(
                    f"\033[92m[✓ FORKED]\033[0m Event {event_id} "
                    f"[{target_date}] | \033[91m{current_name}\033[0m -> "
                    f"\033[92m{new_place_name}\033[0m"
                )
            else:
                debug_print(f"[{current_name}] Name already matches.")

    _progress_bar = None
    conn.commit()
    conn.close()

    print("-" * 50)
    print(
        f"\nCompleted! Adjusted {_updated_count} display values "
        f"without losing FamilySearch data fields."
    )


if __name__ == "__main__":
    main()