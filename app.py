# app.py
import sys
import os
import time
import logging
import json
import threading
import requests
import subprocess
import psutil
import sqlite3
import tkinter as tk
import webbrowser
from tkinter import filedialog
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO
from concurrent.futures import ThreadPoolExecutor

# --- EXTERNAL LIBRARIES ---
import pystray
from PIL import Image, ImageDraw
import engineio.async_drivers.threading

# --- SECURITY LIBRARIES ---
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.exceptions import InvalidSignature

# --- IMPORT MODULES ---
from game_scanner import GameScanner
from game import Game

try:
    from watcher import start_watcher
except ImportError:
    start_watcher = None

# --- CONFIGURATION & CONSTANTS ---
if os.name == 'nt':
    DATA_DIR = os.path.join(os.getenv('LOCALAPPDATA'), 'Game Hub')
else:
    DATA_DIR = os.path.expanduser('~/Game Hub')

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
MANUAL_GAMES_FILE = os.path.join(DATA_DIR, 'manual_games.json')
DATABASE_FILE = os.path.join(DATA_DIR, 'library.db')
OLD_CACHE_FILE = os.path.join(DATA_DIR, 'game_cache.json')
LOG_FILE = os.path.join(DATA_DIR, 'app_error.log')

STEAMGRIDDB_API_URL = "https://www.steamgriddb.com/api/v2"
GITHUB_REPO = "zorkosss/GameHub"
CURRENT_VERSION = "2.1" # Incremented for the new update
PORT = 5000
HOST_URL = f"http://127.0.0.1:{PORT}"

# --- SECURITY: PUBLIC KEY ---
# PASTE YOUR GENERATED PUBLIC KEY BELOW BETWEEN THE TRIPLE QUOTES
# It should look like: b"""-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"""
APP_PUBLIC_KEY = b"""
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA3V9lbP/ydhAfuqVWElNA
wqrTpBxjCi0oWaue4K/Hen5X5HMMSNPOf4Iy8vb/S13CebtaZQRsPgEAmD01uez/
G6uWMi+v5A8cCIIa4tc5LVDDvcMCWwrA8mW0n8H6rrZUEoU9TMIfC1NxbVzwJleJ
5uVjlWOcgfDRuVFhwZChUXz+/quFvXlUk8yRlda/ZsS2DZMi2r7kHjT6J8Wkcni/
8c26tx0Eb8ECrOc6t25miQ+gEZXxB9N8C8hjwoVPm1D2MyVZ75dPVgJcN2ayzm3P
FzJ72l14KUTxpAn2i4apYjEN8QFvi05YwlmF+r1qYF5x58csOal+C8w3IIzPzWGe
/wIDAQAB
-----END PUBLIC KEY-----
"""

def setup_logging():
    logging.basicConfig(level=logging.INFO, 
                        format='%(asctime)s - %(levelname)s - %(message)s', 
                        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])

app = Flask(__name__)
app.config['SECRET_KEY'] = 'gamehub_secret_key_change_this_to_random_string'

# --- SOCKET.IO ---
# Async mode threading is used for compatibility with Windows and PyInstaller
socketio = SocketIO(app, cors_allowed_origins=[], async_mode='threading')

scanner = GameScanner()
all_games = []

# --- SECURITY: ORIGIN CHECK ---
@app.before_request
def check_origin():
    """
    Prevents external access to the API (CSRF Protection).
    Only allows requests from the local interface on the specific port.
    """
    if request.method == "OPTIONS":
        return # Allow preflight checks
        
    allowed_host = f"127.0.0.1:{PORT}"
    
    # Check if the Host header matches localhost
    if request.host != allowed_host and request.host != f"localhost:{PORT}":
        logging.warning(f"Blocked unauthorized access from {request.remote_addr} to {request.host}")
        return jsonify({"error": "Unauthorized Access"}), 403

# --- DATABASE FUNCTIONS ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY,
            name TEXT,
            source TEXT,
            launch_id TEXT,
            install_path TEXT,
            favorite BOOLEAN,
            hidden BOOLEAN,
            last_played REAL,
            playtime_seconds INTEGER,
            grid_image_url TEXT
        )
    ''')
    conn.commit()
    conn.close()

def check_and_update_db_schema():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get list of columns
    cursor.execute("PRAGMA table_info(games)")
    columns = [row['name'] for row in cursor.fetchall()]
    
    # Add 'avg_fps' if missing
    if 'avg_fps' not in columns:
        print("Migrating DB: Adding avg_fps...")
        cursor.execute("ALTER TABLE games ADD COLUMN avg_fps TEXT")
        
    # Add 'best_ping' if missing
    if 'best_ping' not in columns:
        print("Migrating DB: Adding best_ping...")
        cursor.execute("ALTER TABLE games ADD COLUMN best_ping TEXT")
        
    conn.commit()
    conn.close()

def migrate_json_to_db():
    if os.path.exists(OLD_CACHE_FILE):
        logging.info("Found old JSON cache. Migrating to Database...")
        try:
            with open(OLD_CACHE_FILE, 'r') as f:
                data = json.load(f)
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            for g_data in data:
                uid = f"{g_data.get('source')}|{g_data.get('name')}"
                cursor.execute('''
                    INSERT OR IGNORE INTO games 
                    (id, name, source, launch_id, install_path, favorite, hidden, last_played, playtime_seconds, grid_image_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    uid,
                    g_data.get('name'),
                    g_data.get('source'),
                    str(g_data.get('launch_id')),
                    g_data.get('install_path'),
                    g_data.get('favorite', False),
                    g_data.get('hidden', False),
                    g_data.get('last_played', 0),
                    g_data.get('playtime_seconds', 0),
                    g_data.get('grid_image_url', '')
                ))
            conn.commit()
            conn.close()
            os.rename(OLD_CACHE_FILE, OLD_CACHE_FILE + ".bak")
            logging.info("Migration complete.")
        except Exception as e:
            logging.error(f"Migration failed: {e}")

def load_games_from_db():
    global all_games
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM games")
        rows = cursor.fetchall()
        
        loaded_games = []
        for row in rows:
            # Create the Game Object with basic data
            g = Game(
                name=row['name'],
                source=row['source'],
                launch_id=row['launch_id'],
                install_path=row['install_path']
            )
            
            # Load User Data
            g.favorite = bool(row['favorite'])
            g.hidden = bool(row['hidden'])
            g.last_played = row['last_played']
            g.playtime_seconds = row['playtime_seconds']
            g.grid_image_url = row['grid_image_url']
            
            # --- NEW PERFORMANCE DATA ---
            # We check if the column exists in the row keys first (safety check)
            # Then we check if the value is not None. If None, use ""
            keys = row.keys()
            
            if 'avg_fps' in keys and row['avg_fps']:
                g.avg_fps = row['avg_fps']
            else:
                g.avg_fps = ""

            if 'best_ping' in keys and row['best_ping']:
                g.best_ping = row['best_ping']
            else:
                g.best_ping = ""

            loaded_games.append(g)
            
        all_games = loaded_games
        conn.close()
        logging.info(f"Loaded {len(all_games)} games from Database.")
    except Exception as e:
        logging.error(f"DB Load Error: {e}")
        all_games = []

def save_games_to_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for g in all_games:
            uid = f"{g.source}|{g.name}"
            cursor.execute('''
                INSERT OR REPLACE INTO games 
                (id, name, source, launch_id, install_path, favorite, hidden, last_played, playtime_seconds, grid_image_url, avg_fps, best_ping)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                uid, g.name, g.source, str(g.launch_id), g.install_path, 
                g.favorite, g.hidden, g.last_played, g.playtime_seconds, g.grid_image_url,
                getattr(g, 'avg_fps', ""), getattr(g, 'best_ping', "")
            ))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"DB Save Error: {e}")

# --- HELPER FUNCTIONS ---
def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logging.error(f"Failed to save config: {e}")

def _fetch_grid_image(game: Game, api_key: str):
    if game.grid_image_url and game.grid_image_url != "":
        return

    headers = {'Authorization': f'Bearer {api_key}'}
    found = False
    try:
        if game.source == "Steam":
            res = requests.get(f"{STEAMGRIDDB_API_URL}/grids/steam/{game.launch_id}", headers=headers, params={'dimensions': '600x900'}, timeout=3)
            if res.ok:
                data = res.json().get('data')
                if data:
                    game.grid_image_url = data[0]['url']
                    found = True
        
        if not found:
            res = requests.get(f"{STEAMGRIDDB_API_URL}/search/autocomplete/{game.name}", headers=headers, timeout=3)
            if res.ok:
                data = res.json().get('data')
                if data:
                    game_id = data[0]['id']
                    grid_res = requests.get(f"{STEAMGRIDDB_API_URL}/grids/game/{game_id}", headers=headers, params={'dimensions': '600x900'}, timeout=3)
                    if grid_res.ok:
                        grid_data = grid_res.json().get('data')
                        if grid_data:
                            game.grid_image_url = grid_data[0]['url']
                            found = True
    except Exception as e:
        logging.warning(f"Error fetching cover: {e}")

    if not found:
        game.grid_image_url = "MISSING"

def fetch_missing_covers(api_key):
    while True:
        queue = [g for g in all_games if not g.grid_image_url or g.grid_image_url == ""]
        if not queue:
            break
        
        batch = queue[:5] 
        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(lambda g: _fetch_grid_image(g, api_key), batch))
        
        save_games_to_db()
        socketio.emit('scan_complete', {'message': 'Covers updated'})
        time.sleep(1.5)

# --- BACKGROUND SCANNER ---
def scan_library_background():
    with app.app_context():
        global all_games
        logging.info("--- Starting Background Library Scan ---")
        
        config = load_config()
        scanned_games = scanner.find_all_games(config)
        
        existing_data = { (g.name, g.source): g for g in all_games }
        updated_list = []
        
        for s_game in scanned_games:
            key = (s_game.name, s_game.source)
            if key in existing_data:
                e_game = existing_data[key]
                s_game.favorite = e_game.favorite
                s_game.hidden = e_game.hidden
                s_game.playtime_seconds = e_game.playtime_seconds
                s_game.last_played = e_game.last_played
                if e_game.grid_image_url:
                    s_game.grid_image_url = e_game.grid_image_url
            updated_list.append(s_game)
        
        all_games = updated_list
        save_games_to_db()
        
        socketio.emit('scan_complete', {'message': 'Scan complete'})
        
        api_key = config.get('steamgriddb_api_key')
        if api_key:
            missing_count = len([g for g in all_games if not g.grid_image_url or g.grid_image_url == ""])
            if missing_count > 0:
                threading.Thread(target=fetch_missing_covers, args=(api_key,)).start()

# --- CLASSES ---
class AppTray:
    def __init__(self):
        self.icon = None
    def create_image(self):
        if os.path.exists('assets/app_icon.ico'):
            try: return Image.open('assets/app_icon.ico')
            except: pass
        width = 64; height = 64
        image = Image.new('RGB', (width, height), (49, 51, 56))
        dc = ImageDraw.Draw(image)
        dc.ellipse((8, 8, 56, 56), fill=(88, 101, 242))
        return image
    def on_open(self, icon, item): webbrowser.open(HOST_URL)
    def on_quit(self, icon, item): icon.stop(); os._exit(0)
    def run(self):
        image = self.create_image()
        menu = pystray.Menu(
            pystray.MenuItem('Open Game Hub', self.on_open, default=True),
            pystray.MenuItem('Quit', self.on_quit)
        )
        self.icon = pystray.Icon("Game Hub", image, "Game Hub", menu)
        self.icon.run()

class PlaytimeTracker(threading.Thread):
    def __init__(self, game_name, source, install_path):
        super().__init__(daemon=True)
        self.game_name = game_name
        self.source = source
        self.install_path = os.path.normpath(install_path) if install_path else None
        self.game_processes = set()
        self.start_time = 0

    def run(self):
        self.start_time = time.time()
        if not self.detect_game_process(): return
        while True:
            time.sleep(5)
            try:
                self.game_processes = {p for p in self.game_processes if p.is_running()}
                if not self.game_processes: break
            except: break 
        self.update_local_playtime(int(time.time() - self.start_time))

    def detect_game_process(self):
        for _ in range(12): 
            time.sleep(5)
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    p_exe = proc.info.get('exe')
                    if p_exe and self.install_path and os.path.normpath(p_exe).startswith(self.install_path):
                        process = psutil.Process(proc.info['pid'])
                        self.game_processes.update([process] + process.children(recursive=True))
                except: continue
            if self.game_processes: return True
        return False

    def update_local_playtime(self, duration):
        global all_games; found = False
        for game in all_games:
            if game.name == self.game_name and game.source == self.source:
                game.playtime_seconds = (game.playtime_seconds or 0) + duration
                game.last_played = time.time()
                found = True; break
        if found: save_games_to_db(); socketio.emit('game_updated', game.to_dict())

# --- API ROUTES ---
@app.route('/api/games')
def get_games():
    return jsonify([g.to_dict() for g in all_games])

@app.route('/api/refresh', methods=['POST'])
def refresh_games():
    threading.Thread(target=scan_library_background).start()
    return jsonify({"status": "success"})

@app.route('/api/update_game', methods=['POST'])
def update_game():
    data = request.json
    game_name = data.get('name')
    game_source = data.get('source')
    update_data = data.get('update_data')
    
    for game in all_games:
        # Find the specific game in the list
        if game.name == game_name and game.source == game_source:
            
            # --- EXISTING UPDATES ---
            if 'favorite' in update_data: game.favorite = update_data['favorite']
            if 'hidden' in update_data: game.hidden = update_data['hidden']
            
            # --- NEW PERFORMANCE UPDATES (ADD THESE LINES) ---
            if 'avg_fps' in update_data: game.avg_fps = update_data['avg_fps']
            if 'best_ping' in update_data: game.best_ping = update_data['best_ping']
            
            # Save changes to database
            save_games_to_db()
            return jsonify({"status": "success"})
            
    return jsonify({"status": "error"}), 404
@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    config = load_config()
    if request.method == 'POST':
        data = request.json
        config['steamgriddb_api_key'] = data.get('steamgriddb_api_key', '')
        config['scan_paths'] = data.get('scan_paths', [])
        save_config(config)
        return jsonify({"status": "success"})
    return jsonify(config)

# --- FIXED: INDENTATION ERROR FIX ---
@app.route('/api/add_game', methods=['POST'])
def add_game():
    data = request.json
    name, path = data.get('name'), data.get('path')
    
    if not name or not path or not os.path.exists(path):
        return jsonify({"status": "error", "message": "Invalid path or name"}), 400
        
    try:
        manual_games = {}
        if os.path.exists(MANUAL_GAMES_FILE):
            with open(MANUAL_GAMES_FILE, 'r') as f:
                manual_games = json.load(f)
        
        manual_games[name] = path
        
        with open(MANUAL_GAMES_FILE, 'w') as f:
            json.dump(manual_games, f, indent=4)
            
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/launch', methods=['POST'])
def launch_game():
    data = request.json
    command, source, name, install_path = data.get('command'), data.get('source'), data.get('name'), data.get('install_path')
    if not all([command, source, name]): return jsonify({"status": "error"}), 400
    try:
        if source == 'Epic Games': subprocess.Popen(f'cmd /c start "" "{command}"', shell=True)
        else: os.startfile(command)
        if install_path: PlaytimeTracker(name, source, install_path).start()
        return jsonify({"status": "success"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/browse', methods=['GET'])
def browse_files():
    try:
        root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
        filepath = filedialog.askopenfilename(title="Select Game Executable", filetypes=[("Executables", "*.exe"), ("All files", "*.*")])
        root.destroy()
        return jsonify({"status": "success", "path": filepath}) if filepath else jsonify({"status": "cancelled", "path": ""})
    except Exception as e: return jsonify({"status": "error", "message": str(e)})

@app.route('/assets/<path:filename>')
def serve_assets(filename): return send_from_directory('assets', filename)

@app.route('/api/check_for_updates')
def check_for_updates():
    try:
        response = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5, headers={'User-Agent': 'GameHub'})
        if response.ok:
            data = response.json()
            latest = data.get("tag_name", "").lstrip("v")
            if latest > CURRENT_VERSION:
                exe_url = next((a["browser_download_url"] for a in data.get("assets", []) if a["name"].endswith(".exe")), None)
                if exe_url: 
                    return jsonify({
                        "update_available": True, 
                        "version": latest, 
                        "url": exe_url, 
                        "notes": data.get("body", "")
                    })
    except: pass
    return jsonify({"update_available": False})

# --- SECURED: UPDATE ROUTE WITH SIGNATURE VERIFICATION ---
@app.route('/api/perform_update', methods=['POST'])
def perform_update():
    data = request.json
    download_url = data.get('url')
    # Expect signature file to be named [exe_name].sig at the same URL location
    sig_url = download_url + ".sig"

    if not download_url: 
        return jsonify({"status": "error"}), 400

    if b"REPLACE_THIS" in APP_PUBLIC_KEY:
        return jsonify({"status": "error", "message": "Developer has not configured security keys."}), 500

    def do_update():
        try:
            dl_dir = os.path.join(os.path.expanduser("~"), "Downloads")
            setup_path = os.path.join(dl_dir, "GameHub_Update.exe")
            sig_path = os.path.join(dl_dir, "GameHub_Update.exe.sig")

            # 1. Download Executable
            socketio.emit('update_progress', {'status': 'Downloading Update...', 'percent': 10})
            with requests.get(download_url, stream=True, headers={'User-Agent': 'GameHub'}) as r:
                r.raise_for_status()
                total = int(r.headers.get('content-length', 0))
                dl = 0
                with open(setup_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        dl += len(chunk)
                        f.write(chunk)
                        if total and (int(100*dl/total) % 10 == 0): 
                            socketio.emit('update_progress', {'status': 'downloading', 'percent': int(100*dl/total)})

            # 2. Download Signature
            socketio.emit('update_progress', {'status': 'Verifying Security...', 'percent': 90})
            with requests.get(sig_url, headers={'User-Agent': 'GameHub'}) as r:
                if not r.ok:
                    raise Exception("Security signature file missing. Update aborted.")
                with open(sig_path, 'wb') as f:
                    f.write(r.content)

            # 3. VERIFY SIGNATURE
            try:
                public_key = serialization.load_pem_public_key(APP_PUBLIC_KEY)
                
                with open(setup_path, 'rb') as f:
                    exe_data = f.read()
                with open(sig_path, 'rb') as f:
                    sig_data = f.read()

                public_key.verify(
                    sig_data,
                    exe_data,
                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                    hashes.SHA256()
                )
                logging.info("Update signature verified successfully.")
            except InvalidSignature:
                if os.path.exists(setup_path): os.remove(setup_path)
                if os.path.exists(sig_path): os.remove(sig_path)
                raise Exception("SECURITY ALERT: The update file is invalid or has been tampered with.")
            except Exception as e:
                raise Exception(f"Verification Error: {e}")

            # 4. Install
            socketio.emit('update_ready', {'message': 'Installing...'})
            bat_path = os.path.join(dl_dir, "update_launcher.bat")
            with open(bat_path, "w") as f: 
                f.write(f'@echo off\ntimeout /t 2 /nobreak >nul\nstart "" "{setup_path}" /SILENT /SP- /CLOSEAPPLICATIONS\ndel "%~f0"')
            subprocess.Popen([bat_path], shell=True)
            os._exit(0)
            
        except Exception as e:
            logging.error(f"Update failed: {e}")
            socketio.emit('update_error', {'message': str(e)})

    threading.Thread(target=do_update).start()
    return jsonify({"status": "success", "message": "Update started"})

# --- NEW ROUTES FOR FOLDER & DELETE ---

@app.route('/api/open_folder', methods=['POST'])
def open_folder():
    data = request.json
    path = data.get('path')
    
    if not path or not os.path.exists(path):
        return jsonify({"status": "error", "message": "Path does not exist"}), 404
        
    try:
        # Windows-specific command to highlight the file in Explorer
        path = os.path.normpath(path)
        if os.path.isfile(path):
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            os.startfile(path)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/remove_manual_game', methods=['POST'])
def remove_manual_game():
    data = request.json
    name = data.get('name')
    
    if not name: 
        return jsonify({"status": "error"}), 400

    try:
        # 1. Remove from manual_games.json
        if os.path.exists(MANUAL_GAMES_FILE):
            with open(MANUAL_GAMES_FILE, 'r') as f:
                manual_games = json.load(f)
            
            if name in manual_games:
                del manual_games[name]
                
                with open(MANUAL_GAMES_FILE, 'w') as f:
                    json.dump(manual_games, f, indent=4)
                    
        # 2. Remove from Database
        conn = get_db_connection()
        cursor = conn.cursor()
        # Source is usually 'Other Games' for manual entries
        cursor.execute("DELETE FROM games WHERE name = ? AND source = 'Other Games'", (name,))
        conn.commit()
        conn.close()

        # 3. Update local list
        global all_games
        all_games = [g for g in all_games if not (g.name == name and g.source == 'Other Games')]
        
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- NEW SYSTEM STATS ROUTE ---
@app.route('/api/system_stats')
def get_system_stats():
    try:
        # 1. Get CPU (FIX: Add interval=0.1 to allow measurement time)
        cpu = psutil.cpu_percent(interval=0.1)
        
        # 2. Get RAM
        ram = psutil.virtual_memory().percent
        
        # 3. Measure Ping (to Google DNS)
        import subprocess
        import platform
        
        host = "8.8.8.8"
        # Windows uses -n, Linux/Mac uses -c
        param = "-n" if platform.system().lower() == "windows" else "-c"
        
        # Flag creation is needed to hide the console window popup on Windows
        startupinfo = None
        if platform.system().lower() == "windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        command = ["ping", param, "1", host]
        
        result = subprocess.run(
            command, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            startupinfo=startupinfo # Hide the popup window
        )
        output = result.stdout.decode()
        
        ping_ms = "999"
        
        # Parsing Logic
        if "time<" in output: 
            ping_ms = "1" # Very fast connection (<1ms)
        elif "time=" in output:
            try:
                # Extracts "24" from "time=24ms"
                ping_ms = output.split("time=")[1].split("ms")[0].strip()
            except: 
                pass
            
        return jsonify({
            "cpu": cpu,
            "ram": ram,
            "ping": ping_ms
        })
    except Exception as e:
        print(f"Stats Error: {e}")
        return jsonify({"cpu": 0, "ram": 0, "ping": "Err"})

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    setup_logging()
    
    # Init Database and Data
    init_db()
    check_and_update_db_schema()
    migrate_json_to_db()
    load_games_from_db()
    
    # Start File Watcher
    if start_watcher:
        start_watcher(socketio, load_config())
    
    # Initial Scan if needed
    if len(all_games) == 0:
        logging.info("Library empty. Starting Auto-Scan...")
        threading.Thread(target=scan_library_background).start()
    
    print("--- STARTING GAME HUB ---")
    
    # Start Flask Server
    # SECURITY: Host set to 127.0.0.1 to prevent network access
    server_thread = threading.Thread(target=lambda: socketio.run(
        app, 
        host='127.0.0.1', 
        port=PORT, 
        allow_unsafe_werkzeug=True, 
        use_reloader=False
    ))
    server_thread.daemon = True
    server_thread.start()

    time.sleep(1)
    webbrowser.open(HOST_URL)

    # Start System Tray
    tray = AppTray()
    tray.run()
