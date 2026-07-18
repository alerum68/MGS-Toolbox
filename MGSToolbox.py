import os
import queue
import re
import subprocess
import sys
import threading
from typing import Union

import customtkinter as ctk
from dotenv import set_key, load_dotenv

# ==========================================
# UNIFIED ENV SCHEMA & CONTEXT OVERRIDES
# ==========================================
GLOBAL_VARS = {"API & Processing": {"GEMINI_API_KEY": "", "API_BUDGET": "20", "MODEL_NAME": "gemini-3.1-pro-preview",
                                    "COST_PER_1M_INPUT": "2.00", "COST_PER_1M_OUTPUT": "12.00",
                                    "CACHE_DISCOUNT_MULTIPLIER": "0.10"},
               "Script Locations": {"GATHER_DATA_SCRIPT": "ChurchRegisters/ChurchGatherData.py",
                                    "CHURCH_GEDCOM_SCRIPT": "ChurchRegisters/ChurchCreateGedcom.py",
                                    "CENSUS_GEDCOM_SCRIPT": "CenusConverter/CensusConverter.py",
                                    "DUPE_SCRIPT": "Dupes/Dupes.py", "COUNTY_SCRIPT": "CountyFix/CountyFix.py",
                                    "CLEANUP_CACHE_SCRIPT": "ChurchRegisters/CacheCleanup.py"},
               "Global Directories": {"PROGRAM_DIR": "C:/Path/To/Your/Genealogy/Folder", "RM_DIR": "Roots Magic 11",
                                      "FTM_DIR": "Family Tree Maker", "CSV_DIR": "CSV/Project",
                                      "JSON_DIR": "ChurchRegisters/JSON", "IMAGE_EXTENSION": "jpg",
                                      "GEDCOM_OUTPUT_PATH": "RootsMagic 11/Gedcom/Project"},
               "Metadata & Organization": {"RESEARCHER": "Your Name", "ORG_NAME": "Your Historical Society",
                                           "SOFTWARE_NAME": "RootsMagic", "SOFTWARE_VERS": "11.0",
                                           "COPYRIGHT_START": "2024",
                                           "GEDCOM_NOTE": "This file contains original historical translations and research.",
                                           "GEDCOM_CONC": "Please do not upload this raw GEDCOM to public, collaborative trees without permission and attribution.",
                                           "REVIEW_COLOR": "1", "ROOT_SOURCE_ID": "@S1@"},
               "Standard Links": {"SUBM_ADDRESS": "https://www.example.com/contact",
                                  "MGS_GROUP_URL": "https://www.example.com/groups/main",
                                  "ANCESTRY_GROUP_URL": "https://www.ancestry.com/groups/example"}}

CHURCH_VARS = {
	"Data & Directories": {"CHURCH_IMAGE_DIR": "RootsMagic 11/Media/Project/Parish", "CHURCH_GEDCOM_NAME": "Parish.ged",
	                       "CHURCH_MASTER_DB_NAME": "parish_register.json", },
	"Parish Information": {"PARISH_NAME": "St. Generic Catholic Church",
	                       "PARISH_NAME_SHORT": "St. Generic Parish, Anytown, ST", "PARISH_CITY": "Anytown",
	                       "PARISH_STATE": "State", "PARISH_FILE_NAME": "Parish_Anytown",
	                       "DEFAULT_EVENT_LOCATION": "Anytown, Any County, State, USA"},
	"Register Information": {"REGISTER_SOURCE_ID": "1", "VOLUME_TITLE": "Baptisms, marriages and burials, 1850-1900",
	                         "VOLUME_NUM": "1", "REGISTER_NAME": "Baptisms, marriages and burials, 1850-1900", },
	"Church Citation (Source)": {"CHURCH_CALL_NUMBER": "Call #1234567", "CHURCH_REPOSITORY": "FamilySearch.org",
	                             "CHURCH_REPOSITORY_LOC": "Granite Mountain, UT",
	                             "CHURCH_COLLECTION_URL": "https://www.familysearch.org/search/collection",
	                             "CHURCH_COLLECTION_NAME": "Generic Historical Collection, FamilySearch.org"},
	"Formatting & Roles": {"TRANSCRIPTION_HEADER": "Original Transcription:",
	                       "TRANSLATION_HEADER": "English Translation:", "ROLE_DEFAULT_WITNESS": "Witness",
	                       "ROLE_CLERGY": "Priest", "CLERGY_HONORIFIC": "Father",
	                       "PRIEST_NAMES": "John Smith,Michael Johnson,David Williams"}}

CENSUS_VARS = {"Data & Directories": {"CENSUS_IMAGE_DIR": "RootsMagic 11/Media/Project/1850 US Federal Census",
                                      "CENSUS_GEDCOM_NAME": "1850 US Census - State - County.ged",
                                      "CENSUS_CSV_FILE": "1850 US Census - State - County.csv", },
               "Location & Schedule": {"CENSUS_YEAR": "1850", "STATE": "State", "COUNTY": "County",
                                       "TOWNSHIP": "Township", "FILM_NUMBER": "94", "ROLL_NUMBER": "M653",
                                       "CENSUS_PUBLISHER": "Publisher, Inc.", "CENSUS_PUB_LOC": "Anytown, ST, USA"},
               "Census Citation (Source)": {"CENSUS_CALL_NUMBER": "4195937", "CENSUS_REPOSITORY": "NARA",
                                            "CENSUS_REPOSITORY_LOC": "Washington, D.C., USA",
                                            "CENSUS_COLLECTION_URL": "https://www.ancestry.com/search/collections/...",
                                            "CENSUS_COLLECTION_NAME": "1850 United States Federal Census"},
               "Ancestry Information": {"ANCESTRY_START_RECORD_ID": "1000000", "APID_DB": "7667",
                                        "ANCESTRY_IMAGE_BASE_ID": "1234567"},
               "Family Inference Tuning": {"MIN_MARRIAGE_AGE": "12", "MAX_SPOUSE_AGE_GAP": "25",
                                           "HUSBAND_CHILD_AGE_GAP_MIN": "14", "HUSBAND_CHILD_AGE_GAP_MAX": "60",
                                           "WIFE_CHILD_AGE_GAP_MIN": "12", "WIFE_CHILD_AGE_GAP_MAX": "50"}}

DUPE_VARS = {"File Paths (Relative to Program Dir)": {"DUPE_RM_DATABASE": "RootsMagic 11/Your Tree.rmtree"},
             "Matching Thresholds": {"DUPE_FUZZY_THRESHOLD": "82", "DUPE_MAX_AGE_GAP": "5",
                                     "DUPE_FUZZY_THRESHOLD_STRICT": "95", "DUPE_FAMILY_MATCH_THRESHOLD": "75"},
             "RootsMagic UI Settings": {"DUPE_FOLDER_NAME": "!Duplicate Review", "DUPE_COLOR_SET": "1",
                                        "DUPE_COLOR_VALUE": "27"}}

COUNTY_VARS = {"File Paths (Relative to Program Dir)": {"COUNTY_RM_DATABASE": "RootsMagic 11/Your Tree.rmtree",
                                                        "COUNTY_SHAPEFILE": "CountyFix/US_AtlasHCB_Counties/US_HistCounties_Shapefile/US_HistCounties.shp"},
               "Settings": {"COUNTY_DEBUG_MODE": "False", "COUNTY_CREATE_BACKUP": "True"}}


# ==========================================
# THREAD-SAFE CONSOLE & SUBPROCESS
# ==========================================
class ConsoleRedirector:
	"""Manages UI updates for streamed console text, intelligently routing progress bars."""
	
	def __init__(self, text_widget, status_widget):
		self.text_widget = text_widget
		self.status_widget = status_widget
		self.queue = queue.Queue()
		self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
		self.update_gui()
	
	def put(self, text):
		self.queue.put(text)
	
	def update_gui(self):
		"""Routes transient progress bars to the top entry, and permanent logs below."""
		if self.queue.empty():
			self.text_widget.after(50, self.update_gui)
			return
		
		self.text_widget.configure(state="normal")
		
		chars = []
		while not self.queue.empty():
			chars.append(self.queue.get_nowait())
		
		if chars:
			text_chunk = "".join(chars)
			clean_chunk = self.ansi_escape.sub('', text_chunk)
			clean_chunk = clean_chunk.replace('\r\n', '\n')
			
			# If the chunk contains \r, it's a tqdm progress bar or a line-clear
			if '\r' in clean_chunk:
				parts = clean_chunk.split('\r')
				for i, part in enumerate(parts):
					if i == 0 and part:
						self.text_widget.insert("end", part)
					elif i > 0:
						if '\n' in part:
							# Split log messages away from the active progress bar
							log_parts = part.rsplit('\n', 1)
							if log_parts[0]:
								self.text_widget.insert("end", log_parts[0] + '\n')
							if len(log_parts) > 1 and log_parts[1]:
								self.status_widget.configure(state="normal")
								self.status_widget.delete(0, "end")
								self.status_widget.insert(0, log_parts[1])
								self.status_widget.configure(state="readonly")
						else:
							# It is purely a progress bar update
							self.status_widget.configure(state="normal")
							self.status_widget.delete(0, "end")
							self.status_widget.insert(0, part)
							self.status_widget.configure(state="readonly")
			else:
				self.text_widget.insert("end", clean_chunk)
		
		# Cap the text lines to prevent layout-engine memory bloat and freezing
		try:
			current_lines = int(self.text_widget.index('end-1c').split('.')[0])
			if current_lines > 1500:
				self.text_widget.delete("1.0", f"{current_lines - 1500}.0")
		except (ValueError, TypeError, AttributeError):
			pass
		
		self.text_widget.see("end")
		self.text_widget.configure(state="disabled")
		self.text_widget.after(50, self.update_gui)


# ==========================================
# MAIN GUI APPLICATION
# ==========================================
class ModularToolbox(ctk.CTk):
	def __init__(self):
		super().__init__()
		
		self.title("MGS Modular Toolbox")
		self.geometry("1200x900")
		
		ctk.set_appearance_mode("Dark")
		ctk.set_default_color_theme("blue")
		
		self.env_file = ".env"
		self.string_vars = {}
		self.active_process = None
		
		self._build_layout()
		self._load_env_to_vars()
		self._build_tabs()
		
		self.select_tab("Church Register")
	
	# noinspection SpellCheckingInspection
	def _build_layout(self):
		self.grid_rowconfigure(0, weight=1)
		self.grid_columnconfigure(1, weight=1)
		
		self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
		self.sidebar.grid(row=0, column=0, sticky="nsew")
		self.sidebar.grid_rowconfigure(8, weight=1)
		
		ctk.CTkLabel(self.sidebar, text="MGS Toolbox", font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0,
		                                                                                              padx=20,
		                                                                                              pady=(20, 10))
		
		nav_buttons = [("Church Register", "church"), ("Census Data", "census"), ("Duplicate Finder", "dupe"),
		               ("County Fixer", "county"), ("⚙️ Global Settings", "settings")]
		
		self.nav_btns = {}
		for i, (btn_text, name) in enumerate(nav_buttons, start=1):
			btn = ctk.CTkButton(self.sidebar, text=btn_text, fg_color="transparent", text_color=("gray10", "gray90"),
			                    hover_color=("gray70", "gray30"), anchor="w",
			                    command=lambda t=btn_text: self.select_tab(t))
			btn.grid(row=i, column=0, padx=20, pady=5, sticky="ew")
			self.nav_btns[btn_text] = btn
		
		self.main_container = ctk.CTkFrame(self, corner_radius=10)
		self.main_container.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
		
		# Ensure 50/50 split between top forms and bottom console
		self.main_container.grid_rowconfigure(0, weight=1)
		self.main_container.grid_rowconfigure(1, weight=1)
		self.main_container.grid_columnconfigure(0, weight=1)
		
		self.tab_container = ctk.CTkFrame(self.main_container, fg_color="transparent")
		self.tab_container.grid(row=0, column=0, sticky="nsew")
		self.tab_container.grid_rowconfigure(0, weight=1)
		self.tab_container.grid_columnconfigure(0, weight=1)
		
		self.frames = {}
		
		# Universal Console Frame
		self.console_frame = ctk.CTkFrame(self.main_container)
		self.console_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
		self.console_frame.grid_rowconfigure(1, weight=1)  # Let the large textbox expand
		self.console_frame.grid_columnconfigure(0, weight=1)
		
		# --- LOCKED STATUS BAR ---
		self.status_bar = ctk.CTkEntry(self.console_frame, font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
		                               text_color="#00FFFF", fg_color="#1a1a1a", border_width=1)
		self.status_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
		self.status_bar.insert(0, "System Ready")
		self.status_bar.configure(state="readonly")
		
		# Scrolling Logs Textbox
		self.console_text = ctk.CTkTextbox(self.console_frame, font=ctk.CTkFont(family="Consolas", size=12),
		                                   text_color="#00FF00")
		self.console_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))
		self.console_text.configure(state="disabled")
		
		# Interactive Input & Control Frame
		self.input_frame = ctk.CTkFrame(self.console_frame, fg_color="transparent")
		self.input_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
		self.input_frame.grid_columnconfigure(0, weight=1)
		
		self.console_input = ctk.CTkEntry(self.input_frame,
		                                  placeholder_text="Type script input here and press Enter...",
		                                  state="disabled")
		self.console_input.grid(row=0, column=0, sticky="ew", padx=(0, 10))
		self.console_input.bind("<Return>", self.send_input)
		
		self.cancel_btn = ctk.CTkButton(self.input_frame, text="🛑 Cancel", fg_color="darkred", hover_color="red",
		                                width=80, state="disabled", command=self.cancel_script)
		self.cancel_btn.grid(row=0, column=1)
		
		# Pass both widgets to the redirector
		self.console = ConsoleRedirector(self.console_text, self.status_bar)
		self.active_process = None
	
	def send_input(self, _event=None):
		"""Captures Return key, sends text to the active subprocess stdin, and clears the box."""
		if self.active_process and self.active_process.poll() is None:
			user_text = self.console_input.get()
			try:
				self.console.put(f"{user_text}\n")
				self.active_process.stdin.write((user_text + "\n").encode('utf-8'))
				self.active_process.stdin.flush()
				self.console_input.delete(0, 'end')
			except (OSError, BrokenPipeError, AttributeError) as e:
				self.console.put(f"\n[System] Failed to send input: {e}\n")
	
	def cancel_script(self):
		"""Sends a termination signal to the running background process."""
		if self.active_process and self.active_process.poll() is None:
			self.console.put("\n[System] Sending termination signal to process...\n")
			self.active_process.terminate()
	
	def _load_env_to_vars(self):
		"""Loads physical .env file and matches it to our structured dictionary."""
		load_dotenv(self.env_file)
		
		for category_dict in [GLOBAL_VARS, CHURCH_VARS, CENSUS_VARS, DUPE_VARS, COUNTY_VARS]:
			for section, fields in category_dict.items():
				for key, default_val in fields.items():
					val = os.getenv(key, default_val)
					self.string_vars[key] = ctk.StringVar(value=val)
	
	def _save_env(self, extra_updates=None):
		"""Saves current states to the .env file and permanently sanitizes backslashes."""
		for key, var in self.string_vars.items():
			clean_val = var.get().replace('\\', '/')
			set_key(self.env_file, key, clean_val)
		
		if extra_updates:
			for key, val in extra_updates.items():
				clean_val = str(val).replace('\\', '/')
				set_key(self.env_file, key, clean_val)
		
		self.console.put("\n[System] Environment variables saved to .env\n")
	
	def select_tab(self, tab_name):
		"""Updates button highlights and toggles the visible frame."""
		for name, btn in self.nav_btns.items():
			btn.configure(fg_color=("gray75", "gray25") if name == tab_name else "transparent")
		
		for frame in self.frames.values():
			frame.grid_forget()
		self.frames[tab_name].grid(row=0, column=0, sticky="nsew")
	
	def _build_form_ui(self, parent, schema_dict):
		"""Dynamically generates labeled input fields based on schema dictionaries."""
		scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
		scroll.pack(fill="both", expand=True, pady=10)
		
		for section, fields in schema_dict.items():
			ctk.CTkLabel(scroll, text=section, font=ctk.CTkFont(size=16, weight="bold"), text_color="#3B8ED0").pack(
				anchor="w", pady=(15, 5))
			for key in fields.keys():
				row = ctk.CTkFrame(scroll, fg_color="transparent")
				row.pack(fill="x", pady=2)
				ctk.CTkLabel(row, text=key, width=250, anchor="w").pack(side="left", padx=5)
				ctk.CTkEntry(row, textvariable=self.string_vars[key]).pack(side="left", fill="x", expand=True, padx=5)
	
	def _build_tabs(self):
		"""Generates all standalone UI frames and workflow buttons."""
		# 1. Global Settings
		frame_global = ctk.CTkFrame(self.tab_container, fg_color="transparent")
		self.frames["⚙️ Global Settings"] = frame_global
		ctk.CTkLabel(frame_global, text="Global Environment Settings", font=ctk.CTkFont(size=24, weight="bold")).pack(
			anchor="w")
		self._build_form_ui(frame_global, GLOBAL_VARS)
		ctk.CTkButton(frame_global, text="Save Global Config", command=self._save_env).pack(pady=10)
		
		# 2. Church Register Workflow
		frame_church = ctk.CTkFrame(self.tab_container, fg_color="transparent")
		self.frames["Church Register"] = frame_church
		ctk.CTkLabel(frame_church, text="Church Register Workflow", font=ctk.CTkFont(size=24, weight="bold")).pack(
			anchor="w")
		
		self.debug_file_var = ctk.StringVar(value="")
		debug_frame = ctk.CTkFrame(frame_church, fg_color="transparent")
		debug_frame.pack(fill="x", pady=(10, 5))
		ctk.CTkLabel(debug_frame, text="Debug Image Filename (Leave blank for Batch):",
		             font=ctk.CTkFont(weight="bold")).pack(side="left")
		ctk.CTkEntry(debug_frame, textvariable=self.debug_file_var, width=300).pack(side="left", padx=10)
		
		self._build_form_ui(frame_church, CHURCH_VARS)
		btn_box = ctk.CTkFrame(frame_church, fg_color="transparent")
		btn_box.pack(fill="x", pady=10)
		ctk.CTkButton(btn_box, text="Save Config", command=self._save_env).pack(side="left", padx=5)
		ctk.CTkButton(btn_box, text="Step 1: Gather Data (API)", fg_color="darkred", hover_color="red",
		              command=lambda: self.execute_script("GATHER_DATA_SCRIPT", "church_api")).pack(side="left", padx=5)
		ctk.CTkButton(btn_box, text="Step 2: Generate GEDCOM", fg_color="darkgreen", hover_color="green",
		              command=lambda: self.execute_script("CHURCH_GEDCOM_SCRIPT", "church_gedcom")).pack(side="left",
		                                                                                                 padx=5)
		ctk.CTkButton(btn_box, text="Clear Cache", fg_color="#B85D19", hover_color="#8A4513",
		              command=lambda: self.execute_script("CLEANUP_CACHE_SCRIPT", "standalone")).pack(side="left",
		                                                                                              padx=5)
		
		# 3. Census Data
		frame_census = ctk.CTkFrame(self.tab_container, fg_color="transparent")
		self.frames["Census Data"] = frame_census
		ctk.CTkLabel(frame_census, text="Census Data Workflow", font=ctk.CTkFont(size=24, weight="bold")).pack(
			anchor="w")
		self._build_form_ui(frame_census, CENSUS_VARS)
		btn_box2 = ctk.CTkFrame(frame_census, fg_color="transparent")
		btn_box2.pack(fill="x", pady=10)
		ctk.CTkButton(btn_box2, text="Save Config", command=self._save_env).pack(side="left", padx=5)
		ctk.CTkButton(btn_box2, text="Generate Census GEDCOM", fg_color="darkgreen", hover_color="green",
		              command=lambda: self.execute_script("CENSUS_GEDCOM_SCRIPT", "census")).pack(side="left", padx=5)
		
		# 4. Duplicate Finder
		frame_dupe = ctk.CTkFrame(self.tab_container, fg_color="transparent")
		self.frames["Duplicate Finder"] = frame_dupe
		ctk.CTkLabel(frame_dupe, text="Duplicate Finder Workflow", font=ctk.CTkFont(size=24, weight="bold")).pack(
			anchor="w")
		ctk.CTkLabel(frame_dupe, text="Finds logical duplicate people in RootsMagic.", text_color="gray").pack(
			anchor="w", pady=(0, 20))
		self._build_form_ui(frame_dupe, DUPE_VARS)
		btn_box3 = ctk.CTkFrame(frame_dupe, fg_color="transparent")
		btn_box3.pack(fill="x", pady=10)
		ctk.CTkButton(btn_box3, text="Save Config", command=self._save_env).pack(side="left", padx=5)
		ctk.CTkButton(btn_box3, text="Run Script", fg_color="purple", hover_color="darkviolet",
		              command=lambda: self.execute_script("DUPE_SCRIPT", "standalone")).pack(side="left", padx=5)
		
		# 5. County Fixer
		frame_county = ctk.CTkFrame(self.tab_container, fg_color="transparent")
		self.frames["County Fixer"] = frame_county
		ctk.CTkLabel(frame_county, text="County Fixer Workflow", font=ctk.CTkFont(size=24, weight="bold")).pack(
			anchor="w")
		ctk.CTkLabel(frame_county, text="Fixes historical US county jurisdictions utilizing geopandas.",
		             text_color="gray").pack(anchor="w", pady=(0, 20))
		self._build_form_ui(frame_county, COUNTY_VARS)
		btn_box4 = ctk.CTkFrame(frame_county, fg_color="transparent")
		btn_box4.pack(fill="x", pady=10)
		ctk.CTkButton(btn_box4, text="Save Config", command=self._save_env).pack(side="left", padx=5)
		ctk.CTkButton(btn_box4, text="Run Script", fg_color="purple", hover_color="darkviolet",
		              command=lambda: self.execute_script("COUNTY_SCRIPT", "standalone")).pack(side="left", padx=5)
	
	def execute_script(self, script_key, mode):
		"""Prepares environment variables and launches a script in a background thread."""
		script_path: Union[ctk.StringVar, None] = self.string_vars.get(script_key)
		if not script_path or not script_path.get().strip():
			self.console.put(f"\n[ERROR] Script path for '{script_key}' is empty or missing in Global Settings.\n")
			return
		
		script_path_str = script_path.get().strip()
		prog_dir_var: Union[ctk.StringVar, None] = self.string_vars.get("PROGRAM_DIR")
		program_dir = prog_dir_var.get().strip() if prog_dir_var else ""
		
		# Safely resolve the script path against the PROGRAM_DIR so the Toolbox can be run from anywhere!
		if os.path.isabs(script_path_str):
			target_script_path = os.path.abspath(script_path_str)
		else:
			target_script_path = os.path.abspath(os.path.join(program_dir, script_path_str))
		
		if not os.path.exists(target_script_path):
			self.console.put(f"\n[ERROR] Script not found at: {target_script_path}\n")
			return
		
		# Reset the status bar
		self.status_bar.configure(state="normal")
		self.status_bar.delete(0, "end")
		self.status_bar.insert(0, f"Launching {os.path.basename(target_script_path)}...")
		self.status_bar.configure(state="readonly")
		
		# Copy the base Windows system environment first, then update it with our variables
		run_env = os.environ.copy()
		run_env.update({k: str(v.get()) for k, v in self.string_vars.items()})
		
		dynamic_keys = {}
		
		# Resolve Context Mappings dynamically so scripts don't have to be modified natively
		if mode.startswith("church"):
			ch_call: Union[ctk.StringVar, None] = self.string_vars.get("CHURCH_CALL_NUMBER")
			ch_repo: Union[ctk.StringVar, None] = self.string_vars.get("CHURCH_REPOSITORY")
			ch_loc: Union[ctk.StringVar, None] = self.string_vars.get("CHURCH_REPOSITORY_LOC")
			ch_url: Union[ctk.StringVar, None] = self.string_vars.get("CHURCH_COLLECTION_URL")
			ch_name: Union[ctk.StringVar, None] = self.string_vars.get("CHURCH_COLLECTION_NAME")
			
			dynamic_keys = {"IMAGE_DIR": self.string_vars["CHURCH_IMAGE_DIR"].get(),
			                "IMAGE_SOURCE_DIR": self.string_vars["CHURCH_IMAGE_DIR"].get(),
			                "GEDCOM_OUTPUT_NAME": self.string_vars["CHURCH_GEDCOM_NAME"].get(),
			                "MASTER_DB_NAME": self.string_vars["CHURCH_MASTER_DB_NAME"].get(),
			                "CALL_NUMBER": ch_call.get() if ch_call else "",
			                "REPOSITORY": ch_repo.get() if ch_repo else "",
			                "REPOSITORY_LOC": ch_loc.get() if ch_loc else "",
			                "COLLECTION_URL": ch_url.get() if ch_url else "",
			                "COLLECTION_NAME": ch_name.get() if ch_name else ""}
		elif mode == "census":
			ce_call: Union[ctk.StringVar, None] = self.string_vars.get("CENSUS_CALL_NUMBER")
			ce_repo: Union[ctk.StringVar, None] = self.string_vars.get("CENSUS_REPOSITORY")
			ce_loc: Union[ctk.StringVar, None] = self.string_vars.get("CENSUS_REPOSITORY_LOC")
			ce_url: Union[ctk.StringVar, None] = self.string_vars.get("CENSUS_COLLECTION_URL")
			ce_name: Union[ctk.StringVar, None] = self.string_vars.get("CENSUS_COLLECTION_NAME")
			
			dynamic_keys = {"IMAGE_DIR": self.string_vars["CENSUS_IMAGE_DIR"].get(),
			                "IMAGE_SOURCE_DIR": self.string_vars["CENSUS_IMAGE_DIR"].get(),
			                "GEDCOM_OUTPUT_NAME": self.string_vars["CENSUS_GEDCOM_NAME"].get(),
			                "CSV_FILE": self.string_vars["CENSUS_CSV_FILE"].get(),
			                "CALL_NUMBER": ce_call.get() if ce_call else "",
			                "REPOSITORY": ce_repo.get() if ce_repo else "",
			                "REPOSITORY_LOC": ce_loc.get() if ce_loc else "",
			                "COLLECTION_URL": ce_url.get() if ce_url else "",
			                "COLLECTION_NAME": ce_name.get() if ce_name else ""}
		
		# Ensure active dynamic variables are loaded safely to .env
		self._save_env(extra_updates=dynamic_keys)
		run_env.update(dynamic_keys)
		
		self._set_ui_state("disabled")
		
		def on_complete():
			self._set_ui_state("normal")
			self.status_bar.configure(state="normal")
			self.status_bar.delete(0, "end")
			self.status_bar.insert(0, "System Ready")
			self.status_bar.configure(state="readonly")
		
		args = [target_script_path]
		if mode == "church_api" and self.debug_file_var.get().strip():
			args.append(self.debug_file_var.get().strip())
		
		target_cwd = os.path.dirname(target_script_path) if os.path.exists(target_script_path) else None
		
		threading.Thread(target=self._run_subprocess, args=(args, run_env, target_cwd, on_complete),
		                 daemon=True).start()
	
	# noinspection SpellCheckingInspection
	def _run_subprocess(self, safe_cmd, run_env, target_cwd, on_complete):
		"""Runs the background process, handles Unicode streams, and captures inputs natively."""
		import io
		run_env['PYTHONUNBUFFERED'] = '1'
		run_env['PYTHONIOENCODING'] = 'utf-8'
		
		cmd_string = " ".join(safe_cmd)
		self.console.put(f"\n[{cmd_string}] -> Process Started...\n")
		self.console.put("-" * 50 + "\n")
		
		try:
			# Enable STDIN (keyboard input) alongside raw binary stdout
			self.active_process = subprocess.Popen([sys.executable] + safe_cmd, stdin=subprocess.PIPE,
			                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
			                                       env=run_env, cwd=target_cwd)
			
			# Wrap the raw binary stream perfectly for Windows UTF-8 text emulation
			stdout_stream = io.TextIOWrapper(self.active_process.stdout, encoding='utf-8', newline='', errors='replace')
			
			# Stream character-by-character natively mapping to the queue perfectly
			while True:
				char = stdout_stream.read(1)
				if not char:
					break
				self.console.put(char)
			
			self.active_process.wait()
			
			if self.active_process.returncode not in (0, 1, 15):
				self.console.put(
					f"\n[{cmd_string}] -> Process Finished (Exit Code: {self.active_process.returncode})\n")
			elif self.active_process.returncode in (1, 15):
				self.console.put(f"\n[{cmd_string}] -> Process Terminated by User.\n")
			else:
				self.console.put(f"\n[{cmd_string}] -> Process Finished Successfully.\n")
		
		except (OSError, subprocess.SubprocessError, ValueError) as e:
			self.console.put(f"\n[ERROR] Failed to execute {cmd_string}:\n{str(e)}\n")
		
		self.active_process = None
		self.after(100, on_complete)
	
	def _set_ui_state(self, state):
		"""Helper to lock the UI whilst a script runs, and flip the control buttons."""
		for frame in self.frames.values():
			self._recursive_state(frame, state)
		
		if hasattr(self, 'console_input'):
			if state == "disabled":
				self.console_input.configure(state="normal")
				self.cancel_btn.configure(state="normal")
			else:
				self.console_input.configure(state="disabled")
				self.cancel_btn.configure(state="disabled")
	
	# noinspection SpellCheckingInspection
	def _recursive_state(self, widget, state):
		"""Recursively steps through CTk widgets to toggle interactability."""
		if isinstance(widget, ctk.CTkButton):
			widget.configure(state=state)
		for child in widget.winfo_children():
			self._recursive_state(child, state)


if __name__ == "__main__":
	app = ModularToolbox()
	app.mainloop()
