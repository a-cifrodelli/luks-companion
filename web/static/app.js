// ===================================================================
// LUKS MANAGER DASHBOARD JAVASCRIPT CLIENT
// ===================================================================

let selectedKeyfileBase64 = null;
let selectedKeyfileName = null;
let currentTab = "passphrase"; // 'passphrase' or 'keyfile'

function logConsole(msg) {
    const consoleEl = document.getElementById("consoleOutput");
    if (!consoleEl) return;
    const time = new Date().toLocaleTimeString();
    consoleEl.textContent += `[${time}] ${msg}\n`;
    consoleEl.scrollTop = consoleEl.scrollHeight;
}

// -------------------------------------------------------------------
// 1. STATUS POLLING & UI UPDATE
// -------------------------------------------------------------------
async function updateStatus() {
    try {
        const res = await fetch("/api/status");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();

        if (json.status === "ok" && json.data) {
            const data = json.data;

            // Global Master Status Badge
            const masterBadge = document.getElementById("masterStatusBadge");
            const masterText = document.getElementById("masterStatusText");
            if (data.status === "mounted") {
                masterBadge.className = "badge badge-green";
                masterText.textContent = "MONTATO & OPERATIVO";
            } else if (data.status === "unlocked") {
                masterBadge.className = "badge badge-amber";
                masterText.textContent = "SBLOCCATO (SMONTATO)";
            } else {
                masterBadge.className = "badge badge-red";
                masterText.textContent = "SPENTO / 0W STANDBY";
            }

            // Cards
            document.getElementById("valLvm").textContent = data.vg_name ? `Attivo (${data.vg_name})` : (data.unlocked ? "Attivo" : "Disattivato");
            document.getElementById("valLuks").textContent = data.unlocked ? `Sbloccato (/dev/mapper/${data.mapper_name || '...'})` : "Sigillato (0 byte in RAM)";
            document.getElementById("valMount").textContent = data.mounted ? (data.mount_crypto || "Montato") : "Non montato";
            document.getElementById("valWebdav").textContent = data.webdav_active ? "Attivo (Porta 9443)" : "Inattivo";

            // Enable/Disable Action Buttons based on state
            const unlockBtn = document.getElementById("btnUnlock");
            const stopBtn = document.getElementById("btnStop");
            if (data.mounted || data.unlocked) {
                if (unlockBtn) unlockBtn.disabled = true;
                if (stopBtn) stopBtn.disabled = false;
            } else {
                if (unlockBtn) unlockBtn.disabled = false;
                if (stopBtn) stopBtn.disabled = true;
            }
        }
    } catch (err) {
        // Demone down or network error
        const masterBadge = document.getElementById("masterStatusBadge");
        const masterText = document.getElementById("masterStatusText");
        masterBadge.className = "badge badge-red";
        masterText.textContent = "DISCONNESSO DAL DEMONE";
    }
}

// -------------------------------------------------------------------
// 2. TAB SWITCHING (PASSPHRASE VS KEYFILE)
// -------------------------------------------------------------------
function setTab(tab) {
    currentTab = tab;
    document.getElementById("tabPassphrase").className = `tab-btn ${tab === 'passphrase' ? 'active' : ''}`;
    document.getElementById("tabKeyfile").className = `tab-btn ${tab === 'keyfile' ? 'active' : ''}`;

    document.getElementById("secPassphrase").style.display = tab === 'passphrase' ? 'block' : 'none';
    document.getElementById("secKeyfile").style.display = tab === 'keyfile' ? 'block' : 'none';
}

// -------------------------------------------------------------------
// 3. KEYFILE DRAG & DROP / FILE SELECTION
// -------------------------------------------------------------------
function setupDropzone() {
    const dropzone = document.getElementById("keyfileDropzone");
    const fileInput = document.getElementById("keyfileInput");

    dropzone.addEventListener("click", () => fileInput.click());

    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
    });

    dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
    });

    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            handleKeyfile(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) {
            handleKeyfile(e.target.files[0]);
        }
    });
}

function handleKeyfile(file) {
    selectedKeyfileName = file.name;
    const reader = new FileReader();

    reader.onload = function(e) {
        const arrayBuffer = e.target.result;
        const bytes = new Uint8Array(arrayBuffer);
        
        // Convert to base64
        let binary = '';
        const len = bytes.byteLength;
        for (let i = 0; i < len; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        selectedKeyfileBase64 = window.btoa(binary);

        document.getElementById("keyfileSelectedText").textContent = `✓ File caricato: ${selectedKeyfileName} (${len} bytes)`;
        document.getElementById("keyfileSelectedText").style.display = "block";
        logConsole(`Keyfile caricato in memoria RAM client: ${selectedKeyfileName} (${len} bytes)`);
    };

    reader.readAsArrayBuffer(file);
}

// -------------------------------------------------------------------
// 4. UNLOCK ACTION
// -------------------------------------------------------------------
async function performUnlock() {
    const unlockBtn = document.getElementById("btnUnlock");
    unlockBtn.disabled = true;
    unlockBtn.textContent = "⏳ Sblocco in corso...";

    let payload = {};

    if (currentTab === "passphrase") {
        const passphrase = document.getElementById("inputPassphrase").value;
        if (!passphrase) {
            alert("Inserire la passphrase prima di continuare.");
            unlockBtn.disabled = false;
            unlockBtn.textContent = "🔑 Sblocca Storage";
            return;
        }
        payload.passphrase = passphrase;
        logConsole("Invio richiesta di sblocco tramite Passphrase...");
    } else {
        if (!selectedKeyfileBase64) {
            alert("Selezionare o trascinare un file chiave (.key/.bin) valido.");
            unlockBtn.disabled = false;
            unlockBtn.textContent = "🔑 Sblocca Storage";
            return;
        }
        payload.keyfile_base64 = selectedKeyfileBase64;
        logConsole(`Invio richiesta di sblocco tramite Keyfile in-memory (${selectedKeyfileName})...`);
    }

    try {
        const res = await fetch("/api/unlock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const json = await res.json();

        if (json.status === "ok") {
            logConsole(`[SUCCESSO] ${json.message}`);
            if (json.output) logConsole(json.output);
            
            // Clear input
            document.getElementById("inputPassphrase").value = "";
            selectedKeyfileBase64 = null;
            document.getElementById("keyfileSelectedText").style.display = "none";
        } else {
            logConsole(`[ERRORE] ${json.message || 'Sblocco fallito'}`);
            alert(`Errore sblocco: ${json.message}`);
        }
    } catch (err) {
        logConsole(`[ERRORE RETE] ${err.message}`);
        alert(`Errore di comunicazione: ${err.message}`);
    } finally {
        unlockBtn.textContent = "🔑 Sblocca Storage";
        updateStatus();
    }
}

// -------------------------------------------------------------------
// 5. STOP / TEARDOWN ACTION & MODAL
// -------------------------------------------------------------------
function openStopModal() {
    document.getElementById("stopModal").classList.add("active");
}

function closeStopModal() {
    document.getElementById("stopModal").classList.remove("active");
}

async function confirmStop() {
    closeStopModal();
    const stopBtn = document.getElementById("btnStop");
    stopBtn.disabled = true;
    stopBtn.textContent = "⏳ Arresto in corso...";

    logConsole("Avvio procedura di arresto e spegnimento 220V...");

    try {
        const res = await fetch("/api/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
        });
        const json = await res.json();

        if (json.status === "ok") {
            logConsole(`[SUCCESSO] ${json.message}`);
            if (json.output) logConsole(json.output);
        } else {
            logConsole(`[ERRORE] ${json.message}`);
            alert(`Errore durante l'arresto: ${json.message}`);
        }
    } catch (err) {
        logConsole(`[ERRORE RETE] ${err.message}`);
    } finally {
        stopBtn.textContent = "🛑 Espelli & Spegni 220V";
        updateStatus();
    }
}

// -------------------------------------------------------------------
// INITIALIZATION
// -------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    setupDropzone();
    updateStatus();
    setInterval(updateStatus, 5000);
    logConsole("Console client inizializzata. In attesa di comandi.");
});
