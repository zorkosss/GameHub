// static/script.js
let allGames = [];
let currentGame = null;
let currentView = 'grid'; 
let addGameModal, settingsModal, socket;

// --- INITIALIZATION ---
window.addEventListener('DOMContentLoaded', async () => {
    // 1. Connect to WebSocket
    socket = io.connect(`http://${document.domain}:${location.port}`);
    
    socket.on('connect', () => {
        console.log('Websocket connected!');
    });
    
    // 2. WATCHER EVENT: File changed on disk -> Trigger a Scan
    socket.on('library_updated', (msg) => {
        console.log('File change detected. Requesting scan...');
        refreshLibrary(false, true); 
    });

    // 3. APP EVENT: Scan Finished -> Just Update UI (Stops the Loop)
    socket.on('scan_complete', (msg) => {
        console.log('Scan complete. Updating UI only...');
        fetchGamesAndRender(); 
    });

    // 4. Single Game Update (Playtime, Favorites)
    socket.on('game_updated', (updatedGame) => {
        const idx = allGames.findIndex(g => g.name === updatedGame.name && g.source === updatedGame.source);
        if (idx !== -1) allGames[idx] = updatedGame;
        
        // Update details if currently selected
        if (currentGame && currentGame.name === updatedGame.name && currentGame.source === updatedGame.source) {
            showGameDetails(updatedGame);
        }
    });

    // 5. Update Progress Indicators
    socket.on('update_progress', (data) => {
        console.log(`Update status: ${data.status} - ${data.percent}%`);
    });

    socket.on('update_ready', (data) => {
        const btn = document.getElementById('update-btn');
        if (btn) {
            btn.className = "btn btn-warning w-100 fw-bold"; 
            btn.innerHTML = `<i class="fas fa-check-circle"></i> Restarting to Install...`;
        }
        showToast("Download finished! The app will close and install now.", "success");
    });

    socket.on('update_error', (data) => {
        const btn = document.getElementById('update-btn');
        if (btn) {
            btn.disabled = false;
            btn.className = "btn btn-update w-100";
            btn.innerHTML = "Update Failed. Retry?";
        }
        alert("Update Error: " + data.message);
    });

    // 6. Initialize UI Components
    injectModals();
    addGameModal = new bootstrap.Modal(document.getElementById('addGameModal'));
    settingsModal = new bootstrap.Modal(document.getElementById('settingsModal'));
    
    setupEventListeners();
    
    // 7. Initial Fetch (Just get data, don't trigger scan)
    await fetchGamesAndRender(); 
    
    // 8. Check GitHub for updates
    checkForUpdates();
});

function setupEventListeners() {
    document.getElementById('refresh-btn').addEventListener('click', () => refreshLibrary(false));
    document.getElementById('view-toggle-btn').addEventListener('click', toggleView);
    document.getElementById('settings-btn').addEventListener('click', openSettings);
    document.getElementById('add-game-btn').addEventListener('click', () => addGameModal.show());
    document.getElementById('play-button').addEventListener('click', launchCurrentGame);
    document.getElementById('favorite-btn').addEventListener('click', toggleFavorite);
    document.getElementById('hide-btn').addEventListener('click', toggleHidden);
    document.getElementById('browse-btn').addEventListener('click', browseForGame);
    document.getElementById('save-game-btn').addEventListener('click', saveManualGame);
    document.getElementById('save-settings-btn').addEventListener('click', saveSettings);
    document.getElementById('add-path-btn').addEventListener('click', addScanPath);
    document.getElementById('remove-path-btn').addEventListener('click', removeScanPath);
    document.getElementById('clear-cache-btn').addEventListener('click', clearCache);
    document.getElementById('search-input').addEventListener('input', () => displayCurrentView());
}

// --- CORE FUNCTIONS ---

function toggleView() {
    currentView = (currentView === 'list') ? 'grid' : 'list';
    document.getElementById('list-view').classList.toggle('active-view', currentView === 'list');
    document.getElementById('grid-view').classList.toggle('active-view', currentView === 'grid');
    
    const icon = document.querySelector('#view-toggle-btn i');
    if (icon) icon.className = (currentView === 'list') ? 'fas fa-th-large' : 'fas fa-th-list';
    
    displayCurrentView();
}

// Triggers a backend scan (POST)
async function refreshLibrary(isInitialLoad = false, isAutoRefresh = false) {
    if (!isAutoRefresh) {
        const btn = document.getElementById('refresh-btn');
        const icon = btn.querySelector('i');
        icon.classList.add('fa-spin'); 
        btn.disabled = true;
    }
    
    // Tell backend to start scanning threads
    await fetch('/api/refresh', { method: 'POST' });
    // Note: We do NOT wait for the scan to finish here. 
    // We wait for the 'scan_complete' socket event.
}

// ONLY fetches data and updates screen (GET)
async function fetchGamesAndRender() {
    try {
        const response = await fetch('/api/games');
        allGames = await response.json();
        populateLibraries();
        
        // Restore active library tab
        const activeLib = document.querySelector('#library-list .active')?.dataset.source || 'All Games';
        const navLink = document.querySelector(`#library-list .nav-link[data-source="${activeLib}"]`);
        if (navLink) {
            document.querySelectorAll('#library-list .active').forEach(el => el.classList.remove('active'));
            navLink.classList.add('active');
        }
        
        displayCurrentView();
        
        // Stop spinner if it was running
        const btn = document.getElementById('refresh-btn');
        const icon = btn.querySelector('i');
        icon.classList.remove('fa-spin'); 
        btn.disabled = false;
        
        // Onboarding Check
        if (allGames.length > 0 && allGames.some(g => !g.grid_image_url || g.grid_image_url === "MISSING")) {
            checkSettingsAndNotify();
        }

    } catch (e) {
        console.error("UI Update failed:", e);
    }
}

function populateLibraries() {
    const libraryList = document.getElementById('library-list');
    const activeSource = document.querySelector('#library-list .active')?.dataset.source;
    
    libraryList.innerHTML = ''; 
    
    // Get unique sources
    let sources = ['All Games', 'Favorites', ...new Set(allGames.map(g => g.source)), 'Hidden'];
    
    sources.forEach(source => {
        let iconHtml;
        if (source === 'Favorites') iconHtml = '<i class="fas fa-star fa-fw"></i>';
        else if (source === 'Hidden') iconHtml = '<i class="fas fa-eye-slash fa-fw"></i>';
        else if (source === 'All Games') iconHtml = '<i class="fas fa-gamepad fa-fw"></i>';
        else iconHtml = `<img src="${getLogoPath(source)}" alt="${source}">`;

        const listItem = document.createElement('li');
        listItem.className = 'nav-item';
        
        const link = document.createElement('a');
        link.className = 'nav-link';
        link.dataset.source = source;
        link.href = '#';
        link.innerHTML = `${iconHtml} <span>${source}</span>`;
        
        if (source === activeSource) link.classList.add('active');
        
        listItem.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelectorAll('#library-list .active').forEach(el => el.classList.remove('active'));
            link.classList.add('active');
            displayCurrentView();
        });
        
        listItem.appendChild(link);
        libraryList.appendChild(listItem);
    });
}

function displayCurrentView() {
    const activeSource = document.querySelector('#library-list .active')?.dataset.source || 'All Games';
    const query = document.getElementById('search-input').value.toLowerCase();
    
    // Ensure visibility classes are set (Safety Net)
    const gridEl = document.getElementById('grid-view');
    const listEl = document.getElementById('list-view');
    if (!gridEl.classList.contains('active-view') && !listEl.classList.contains('active-view')) {
        gridEl.classList.add('active-view'); // Default to grid
    }

    let gamesToShow;
    if (activeSource === 'All Games') gamesToShow = allGames.filter(g => !g.hidden);
    else if (activeSource === 'Favorites') gamesToShow = allGames.filter(g => g.favorite && !g.hidden);
    else if (activeSource === 'Hidden') gamesToShow = allGames.filter(g => g.hidden);
    else gamesToShow = allGames.filter(g => g.source === activeSource && !g.hidden);

    if (query) gamesToShow = gamesToShow.filter(g => g.name.toLowerCase().includes(query));
    
    gamesToShow.sort((a, b) => a.name.localeCompare(b.name));

    gridEl.innerHTML = ''; 
    listEl.innerHTML = '';
    
    // Empty State
    if (gamesToShow.length === 0) {
        const noGamesHtml = `
            <div class="d-flex flex-column align-items-center justify-content-center h-100 text-muted" style="grid-column: 1 / -1; min-height: 300px;">
                <i class="fas fa-ghost fa-3x mb-3" style="opacity: 0.3;"></i>
                <h5>No games found</h5>
                <p class="small">Scan a new folder in Settings or add a game manually.</p>
            </div>
        `;
        if (currentView === 'grid') gridEl.innerHTML = noGamesHtml;
        else listEl.innerHTML = noGamesHtml;
        
        // Clear details
        document.getElementById('hero-image-container').innerHTML = '';
        document.getElementById('game-title').textContent = 'Select a Game';
        document.getElementById('play-button').disabled = true;
        document.getElementById('hero-background').style.backgroundImage = 'none';
        return;
    }

    gamesToShow.forEach(game => {
        // List Item
        const listItem = document.createElement('a'); 
        listItem.href = '#'; 
        listItem.className = 'list-group-item list-group-item-action';
        listItem.dataset.name = game.name; 
        listItem.innerHTML = `<img src="${getLogoPath(game.source)}" alt=""> ${game.name}`;
        listItem.addEventListener('click', (e) => { e.preventDefault(); showGameDetails(game); });
        listEl.appendChild(listItem);

        // Grid Item
        const gridItem = document.createElement('div'); 
        gridItem.className = 'grid-item';
        const hasImage = game.grid_image_url && game.grid_image_url !== "MISSING";
        
        gridItem.innerHTML = hasImage 
            ? `<img src="${game.grid_image_url}" alt="${game.name}" loading="lazy">`
            : `<div class="grid-item-placeholder">${game.name}</div>`;
        
        gridItem.addEventListener('click', () => showGameDetails(game));
        gridEl.appendChild(gridItem);
    });

    // Auto-select logic
    if (currentGame && gamesToShow.find(g => g.name === currentGame.name && g.source === currentGame.source)) {
        showGameDetails(currentGame);
    } else if (gamesToShow.length > 0) {
        showGameDetails(gamesToShow[0]);
    }
}

function showGameDetails(game) {
    currentGame = game;
    
    // Highlight active in list
    document.querySelectorAll('#list-view .active').forEach(el => el.classList.remove('active'));
    document.querySelector(`#list-view [data-name="${game.name}"]`)?.classList.add('active');

    // Text details
    document.getElementById('game-title').textContent = game.name;
    const dateStr = game.last_played ? new Date(game.last_played * 1000).toLocaleDateString() : 'Never';
    document.getElementById('last-played').textContent = dateStr;
    const hours = ((game.playtime_seconds || 0) / 3600).toFixed(1);
    document.getElementById('playtime').textContent = `${hours}h`;
    
    // --- IMAGE LOGIC (THE FIX) ---
    const container = document.getElementById('hero-image-container');
    const bg = document.getElementById('hero-background');
    
    // 1. Determine the "Best" URL (Steam Header) and the "Backup" URL (Grid Cover)
    let primaryUrl = null;
    let backupUrl = (game.grid_image_url && game.grid_image_url !== "MISSING") ? game.grid_image_url : null;

    if (game.source === 'Steam') {
        primaryUrl = `https://steamcdn-a.akamaihd.net/steam/apps/${game.launch_id}/header.jpg`;
    } else {
        primaryUrl = backupUrl;
    }

    // 2. Clear container
    container.innerHTML = '';

    if (primaryUrl) {
        const img = document.createElement('img');
        img.alt = game.name;
        
        // 3. The "Safety Net": If primary fails, switch to backup
        img.onerror = function() {
            console.warn(`Primary image failed for ${game.name}. Switching to backup.`);
            if (backupUrl && this.src !== backupUrl) {
                this.src = backupUrl;
                bg.style.backgroundImage = `url('${backupUrl}')`;
            } else {
                // If even backup fails (or doesn't exist), show placeholder
                container.innerHTML = `<div class="d-flex align-items-center justify-content-center h-100 text-muted" style="background: #222;"><i class="fas fa-image fa-3x"></i></div>`;
                bg.style.backgroundImage = 'none';
            }
        };

        // Set the source (triggers loading)
        img.src = primaryUrl;
        
        container.appendChild(img);
        bg.style.backgroundImage = `url('${primaryUrl}')`;
    } else {
        // No image at all
        container.innerHTML = `<div class="d-flex align-items-center justify-content-center h-100 text-muted" style="background: #222;"><i class="fas fa-image fa-3x"></i></div>`;
        bg.style.backgroundImage = 'none';
    }
    
    // Buttons
    document.getElementById('play-button').disabled = false;
    
    const favBtn = document.getElementById('favorite-btn');
    favBtn.classList.toggle('favorited', game.favorite);
    favBtn.querySelector('i').className = game.favorite ? 'fas fa-star' : 'far fa-star';
    
    const hideBtn = document.getElementById('hide-btn');
    hideBtn.querySelector('i').className = game.hidden ? 'fas fa-eye-slash' : 'far fa-eye';
}

async function updateGame(updateData) {
    if (!currentGame) return;
    const gameToUpdate = currentGame;
    
    await fetch('/api/update_game', { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ name: gameToUpdate.name, source: gameToUpdate.source, update_data: updateData }), 
    });
    
    Object.assign(gameToUpdate, updateData);
    displayCurrentView(); 
    showGameDetails(gameToUpdate);
}

async function toggleFavorite() { await updateGame({ favorite: !currentGame.favorite }); }
async function toggleHidden() { await updateGame({ hidden: !currentGame.hidden }); }

async function launchCurrentGame() {
    if (!currentGame) return;
    
    const launchData = { 
        command: getLaunchCommand(currentGame), 
        source: currentGame.source,
        name: currentGame.name,
        install_path: currentGame.install_path 
    };

    try {
        await fetch('/api/launch', {
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify(launchData),
        });
        showToast(`Launching ${currentGame.name}...`, 'success');
    } catch (error) {
        alert('Failed to launch game. Is the app running?');
    }
}

function getLaunchCommand(game) {
    switch(game.source) {
        case 'Steam': return `steam://run/${game.launch_id}`;
        case 'Epic Games': return `com.epicgames.launcher://apps/${game.launch_id}?action=launch&silent=true`;
        case 'EA': return `origin://launchgame/${game.launch_id}`;
        case 'Other Games': return game.install_path;
        default: return null;
    }
}

function getLogoPath(source) {
    if (!source || ['All Games', 'Favorites', 'Hidden'].includes(source)) return '/assets/placeholder_logo.png';
    // Convert "Epic Games" -> "epic_games_logo.png", "EA" -> "ea_logo.png"
    return `/assets/${source.toLowerCase().replace(/ /g, '_')}_logo.png`;
}

function showToast(message, type = 'info') {
    const container = document.querySelector('.toast-container');
    const toastId = `toast-${Date.now()}`;
    const bgClass = type === 'danger' ? 'bg-danger' : (type === 'success' ? 'bg-success' : 'bg-primary');
    
    const toastHtml = `
        <div id="${toastId}" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header ${bgClass} text-white">
                <i class="fas fa-gamepad me-2"></i>
                <strong class="me-auto">Game Hub</strong>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
            </div>
            <div class="toast-body text-dark">
                ${message}
            </div>
        </div>`;
        
    container.insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement);
    toastElement.addEventListener('hidden.bs.toast', () => toastElement.remove());
    toast.show();
}

async function checkSettingsAndNotify() {
    try {
        const response = await fetch('/api/settings');
        const config = await response.json();
        // Only notify if api key is missing AND we haven't notified recently (simple session check)
        if (!config.steamgriddb_api_key && !sessionStorage.getItem('notified_api')) {
            showOnboardingToast();
            sessionStorage.setItem('notified_api', 'true');
        }
    } catch (e) {}
}

function showOnboardingToast() {
    const container = document.querySelector('.toast-container');
    if (container.querySelector('.onboarding-toast')) return;

    const toastHtml = `
        <div class="toast onboarding-toast" role="alert" aria-live="assertive" aria-atomic="true" data-bs-autohide="false">
            <div class="toast-header bg-primary text-white">
                <strong class="me-auto"><i class="fas fa-image me-2"></i>Missing Cover Art?</strong>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
            </div>
            <div class="toast-body bg-light text-dark">
                <p class="mb-2">Get a free API Key to auto-download covers.</p>
                <button type="button" class="btn btn-sm btn-primary w-100" id="go-to-settings-btn">Open Settings</button>
            </div>
        </div>`;
    container.insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = container.lastElementChild;
    const toast = new bootstrap.Toast(toastElement);
    toast.show();
    document.getElementById('go-to-settings-btn').addEventListener('click', () => { toast.hide(); openSettings(); });
}

// --- MODALS (Inject & Settings) ---

function injectModals() {
    if (!document.getElementById('addGameModal')) {
        document.body.insertAdjacentHTML('beforeend', `
            <div class="modal fade" id="addGameModal" tabindex="-1">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Add Manual Game</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="mb-3">
                                <label class="form-label">Game Name</label>
                                <input type="text" class="form-control" id="gameNameInput">
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Executable Path</label>
                                <div class="input-group">
                                    <input type="text" class="form-control" id="gamePathInput" readonly>
                                    <button class="btn btn-outline-secondary" type="button" id="browse-btn">Browse...</button>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-primary" id="save-game-btn">Save Game</button>
                        </div>
                    </div>
                </div>
            </div>
        `);
    }

    if (!document.getElementById('settingsModal')) {
        document.body.insertAdjacentHTML('beforeend', `
            <div class="modal fade" id="settingsModal" tabindex="-1">
                <div class="modal-dialog modal-dialog-centered modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Settings</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="mb-4">
                                <label class="form-label fw-bold">SteamGridDB API Key</label>
                                <div class="form-text mb-2 text-muted">
                                    Get a free API key from <a href="https://www.steamgriddb.com/profile/preferences" target="_blank" class="text-info">SteamGridDB</a>.
                                </div>
                                <input type="password" class="form-control" id="apiKeyInput" placeholder="Paste your API Key here">
                            </div>
                            <div class="mb-4">
                                <label class="form-label fw-bold">Library Folders</label>
                                <div class="d-flex">
                                    <ul id="scan-path-list" class="list-group w-100" style="max-height: 150px; overflow-y: auto; background: #222;"></ul>
                                    <div class="ms-2 d-flex flex-column" style="min-width: 80px;">
                                        <button id="add-path-btn" class="btn btn-sm btn-outline-success mb-2 w-100">Add</button>
                                        <button id="remove-path-btn" class="btn btn-sm btn-outline-danger w-100">Remove</button>
                                    </div>
                                </div>
                            </div>
                            <hr class="border-secondary">
                            <button id="clear-cache-btn" class="btn btn-danger w-100">Clear Cache & Reset</button>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                            <button type="button" class="btn btn-primary" id="save-settings-btn">Save & Scan</button>
                        </div>
                    </div>
                </div>
            </div>
        `);
    }
}

async function openSettings() {
    const response = await fetch('/api/settings');
    const config = await response.json();
    document.getElementById('apiKeyInput').value = config.steamgriddb_api_key || '';
    const listbox = document.getElementById('scan-path-list');
    listbox.innerHTML = '';
    (config.scan_paths || []).forEach(path => {
        const li = document.createElement('li'); 
        li.className = 'list-group-item bg-dark text-white border-secondary'; 
        li.textContent = path;
        li.addEventListener('click', () => { 
            document.querySelector('#scan-path-list .active')?.classList.remove('active'); 
            li.classList.add('active'); 
        });
        listbox.appendChild(li);
    });
    settingsModal.show();
}

async function saveSettings() {
    const apiKey = document.getElementById('apiKeyInput').value;
    const paths = Array.from(document.querySelectorAll('#scan-path-list li')).map(li => li.textContent);
    
    await fetch('/api/settings', { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ steamgriddb_api_key: apiKey, scan_paths: paths }) 
    });
    
    settingsModal.hide();
    refreshLibrary(false); // Trigger scan
}

function addScanPath() {
    const path = prompt("Enter full folder path (e.g., D:\\Games):");
    if (path) {
        const listbox = document.getElementById('scan-path-list');
        const li = document.createElement('li');
        li.className = 'list-group-item bg-dark text-white border-secondary';
        li.textContent = path;
        li.addEventListener('click', () => { 
            document.querySelector('#scan-path-list .active')?.classList.remove('active'); 
            li.classList.add('active'); 
        });
        listbox.appendChild(li);
    }
}
function removeScanPath() { document.querySelector('#scan-path-list .active')?.remove(); }

async function browseForGame() {
    try {
        const response = await fetch('/api/browse');
        const data = await response.json();
        if (data.status === 'success') { document.getElementById('gamePathInput').value = data.path; }
    } catch (error) { alert('Browse failed on server.'); }
}

async function saveManualGame() {
    const name = document.getElementById('gameNameInput').value;
    const path = document.getElementById('gamePathInput').value;
    if (!name || !path) { alert('Name and Path required.'); return; }
    
    const response = await fetch('/api/add_game', { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ name, path }) 
    });
    
    if (response.ok) { 
        addGameModal.hide(); 
        refreshLibrary(false); 
    } else { 
        alert('Failed to save.'); 
    }
}

async function clearCache() {
    if (confirm("Reset cache? This will delete custom sorting.")) {
        alert("Please delete game_cache.json in AppData manually for now.");
        settingsModal.hide();
    }
}

async function checkForUpdates() {
    try {
        const response = await fetch('/api/check_for_updates');
        if (!response.ok) return;
        const data = await response.json();
        
        if (data.update_available) {
            const container = document.getElementById('update-container');
            const btn = document.getElementById('update-btn');
            if (container && btn) {
                container.style.display = 'block';
                btn.innerHTML = `<i class="fas fa-cloud-download-alt"></i> Update v${data.version}`;
                btn.onclick = () => {
                    if (confirm(`Install v${data.version}?\n\n${data.notes}`)) {
                        performUpdate(data.url);
                    }
                };
            }
        }
    } catch (e) { console.error(e); }
}

async function performUpdate(url) {
    const btn = document.getElementById('update-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Downloading...';
    }
    try {
        await fetch('/api/perform_update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url })
        });
    } catch (e) {
        alert("Failed to start update.");
        if (btn) btn.disabled = false;
    }
}