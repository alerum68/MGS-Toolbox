import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

from dotenv import load_dotenv


def parse_ancestry_url(url: str):
	"""Extract APID (dbid) and Start Record ID from Ancestry URLs."""
	view_match = re.search(r'view/(\d+):(\d+)', url)
	if view_match:
		return view_match.group(2), view_match.group(1)
	
	col_match = re.search(r'collections/(\d+)', url)
	pid_match = re.search(r'[?&]pId=(\d+)', url, re.IGNORECASE)
	if col_match and pid_match:
		return col_match.group(1), pid_match.group(1)
	
	parsed = urllib.parse.urlparse(url)
	qs = urllib.parse.parse_qs(parsed.query)
	if 'dbid' in qs and 'h' in qs:
		return qs['dbid'][0], qs['h'][0]
	
	return None, None


def main():
	print("========================================")
	print(" Ancestry Census Automation Pipeline")
	print("========================================")
	
	program_dir = os.getenv("PROGRAM_DIR", "")
	env_path = Path(program_dir) / ".env" if program_dir else Path(".env")
	
	if env_path.exists():
		load_dotenv(dotenv_path=env_path)
	
	url = os.getenv("CENSUS_URL", "").strip()
	csv_dir = os.getenv("CSV_DIR", "CSV/Project")
	base_img_setting = os.getenv("CENSUS_IMAGE_DIR", "RootsMagic 11/Media/Project/Census")
	gedcom_script = os.getenv("CENSUS_GEDCOM_SCRIPT", "CensusConverter/CensusConverter.py").strip()
	
	if not url:
		print("[ERROR] Please enter an Ancestry URL in the Toolbox settings first.")
		sys.exit(1)
	
	dbid, start_id = parse_ancestry_url(url)
	if not dbid or not start_id:
		print("[ERROR] Could not parse database ID (dbid) or record ID (h) from the URL.")
		sys.exit(1)
	
	print(f"[System] Extracted -> DBID: {dbid} | Start ID: {start_id}")
	
	start_time = time.time()
	auto_url = url + ("&mgs_auto=1" if "?" in url else "?mgs_auto=1")
	print("[System] Launching browser...")
	webbrowser.open(auto_url)
	
	print("\n[System] Waiting for Tampermonkey downloads (Auto-Batch will start automatically)...")
	
	downloads_dir = Path.home() / "Downloads"
	csv_file = None
	
	try:
		while True:
			try:
				for file_path in downloads_dir.iterdir():
					if not file_path.is_file() or file_path.stat().st_mtime < start_time:
						continue
					if file_path.suffix.lower() == '.csv' and not csv_file:
						csv_file = file_path
						print(f"[System] Detected Final CSV: {file_path.name}")
						break
			except Exception:
				pass
			
			if csv_file:
				break
			time.sleep(1)
	except KeyboardInterrupt:
		print("\n[System] Operation cancelled by user.")
		sys.exit(0)
	
	print("\n[System] Processing extracted files...")
	
	csv_target_dir = Path(program_dir) / csv_dir if program_dir else Path(csv_dir)
	csv_target_dir.mkdir(parents=True, exist_ok=True)
	
	final_csv = csv_target_dir / csv_file.name
	shutil.move(str(csv_file), str(final_csv))
	
	stem_parts = final_csv.stem.split(' - ', 1)
	census_year = stem_parts[0].strip() if len(stem_parts) > 0 else "Unknown_Year"
	raw_location = stem_parts[1].strip() if len(stem_parts) > 1 else "Unknown_Location"
	
	location_folder = re.sub(r'^USA\s*-\s*', '', raw_location)
	census_folder = f"{census_year} US Federal Census"
	
	base_img_dir = Path(program_dir) / base_img_setting if program_dir else Path(base_img_setting)
	img_target_dir = base_img_dir / census_folder / location_folder
	img_target_dir.mkdir(parents=True, exist_ok=True)
	
	img_count = 0
	base_id = ""
	for file_path in downloads_dir.iterdir():
		try:
			if file_path.is_file() and file_path.suffix.lower() == '.jpg' and file_path.stat().st_mtime >= start_time:
				final_img = img_target_dir / file_path.name
				shutil.move(str(file_path), str(final_img))
				img_count += 1
				if not base_id:
					base_id = final_img.stem.split('_')[0] if '_' in final_img.stem else final_img.stem
		except Exception:
			pass
	
	print(f"[System] Moved CSV and {img_count} images to Project folders.")
	deep_img_dir = (Path(base_img_setting) / census_folder / location_folder).as_posix()
	
	print("\n[System] Triggering GEDCOM conversion...")
	
	run_env = os.environ.copy()
	run_env["APID_DB"] = str(dbid)
	run_env["ANCESTRY_START_RECORD_ID"] = str(start_id)
	run_env["CENSUS_CSV_FILE"] = str(final_csv.name)
	run_env["CSV_FILE"] = str(final_csv.name)
	run_env["CENSUS_IMAGE_DIR"] = str(deep_img_dir)
	run_env["IMAGE_DIR"] = str(deep_img_dir)
	run_env["IMAGE_SOURCE_DIR"] = str(deep_img_dir)
	run_env["CENSUS_YEAR"] = str(census_year)
	
	run_env["COLLECTION_NAME"] = f"{census_year} United States Federal Census"
	run_env["GEDCOM_OUTPUT_NAME"] = str(final_csv.with_suffix('.ged').name)
	
	if base_id:
		run_env["ANCESTRY_IMAGE_BASE_ID"] = str(base_id)
	
	run_env["CALL_NUMBER"] = run_env.get("CENSUS_CALL_NUMBER", "")
	run_env["REPOSITORY"] = run_env.get("CENSUS_REPOSITORY", "")
	run_env["REPOSITORY_LOC"] = run_env.get("CENSUS_REPOSITORY_LOC", "")
	run_env["COLLECTION_URL"] = run_env.get("CENSUS_COLLECTION_URL", "")
	
	gedcom_path = Path(program_dir) / gedcom_script if program_dir else Path(gedcom_script)
	
	if gedcom_path.exists():
		result = subprocess.run([sys.executable, str(gedcom_path.resolve())], env=run_env)
		if result.returncode == 0:
			print("\n✅ Automation Pipeline Complete! GEDCOM generated successfully.")
		else:
			print(f"\n❌ Error generating GEDCOM. Exit code: {result.returncode}")
	else:
		print(f"\n[ERROR] Could not find GEDCOM script at {gedcom_path}")


if __name__ == "__main__":
	main()
