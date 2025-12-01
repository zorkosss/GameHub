# watcher.py
import time
import logging
import os
import winreg
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class GameLibraryEventHandler(FileSystemEventHandler):
    def __init__(self, socketio):
        self.socketio = socketio
        self.last_event_time = 0
        self.debounce_period = 2 # seconds
        logging.info("File watcher started.")

    def on_any_event(self, event):
        # 1. Ignore directory changes immediately
        if event.is_directory:
            return

        filename = os.path.basename(event.src_path)
        
        # 2. STRICT WHITELIST
        # We ONLY care about these specific files. 
        # If it's not one of these, we stop immediately.
        is_game_file = False
        
        # Steam Manifests
        if filename.startswith("appmanifest_") and filename.endswith(".acf"):
            is_game_file = True
        # Epic Games Manifests
        elif filename.endswith(".item"):
            is_game_file = True
        # EA Games Data
        elif filename == "installerdata.xml":
            is_game_file = True
        # Our Manual Games File
        elif filename == "manual_games.json":
            is_game_file = True
            
        if not is_game_file:
            # This ignores app_error.log, game_cache.json, config.json, etc.
            return

        # 3. Debounce (Prevent double-firing)
        current_time = time.time()
        if current_time - self.last_event_time < self.debounce_period:
            return

        logging.info(f"Library change detected: {filename}. Triggering refresh.")
        self.last_event_time = current_time
        self.socketio.emit('library_updated', {'data': 'Library file changed'})

def _get_steam_paths():
    paths = set()
    try:
        hkey = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")
        steam_path = winreg.QueryValueEx(hkey, "InstallPath")[0]
        winreg.CloseKey(hkey)
        paths.add(os.path.join(steam_path, "steamapps"))
    except: pass
    return paths

def _get_epic_path():
    try:
        return os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'Epic', 'EpicGamesLauncher', 'Data', 'Manifests')
    except: return None

def start_watcher(socketio, config):
    paths_to_watch = set()
    
    # Add Steam
    steam_paths = _get_steam_paths()
    paths_to_watch.update(steam_paths)
        
    # Add Epic
    epic_path = _get_epic_path()
    if epic_path and os.path.isdir(epic_path):
        paths_to_watch.add(epic_path)

    # Add Custom Paths
    custom_paths = config.get('scan_paths', [])
    for path in custom_paths:
        if os.path.isdir(path):
            paths_to_watch.add(path)
    
    # Watch the AppData folder (Only for manual_games.json)
    if os.name == 'nt':
        data_dir = os.path.join(os.getenv('LOCALAPPDATA'), 'Game Hub')
    else:
        data_dir = os.path.expanduser('~/Game Hub')
    
    if os.path.exists(data_dir):
        paths_to_watch.add(data_dir)

    if not paths_to_watch:
        return None

    event_handler = GameLibraryEventHandler(socketio)
    observer = Observer()
    
    for path in paths_to_watch:
        if os.path.isdir(path):
            try:
                observer.schedule(event_handler, path, recursive=True)
            except: pass

    observer.start()
    return observer