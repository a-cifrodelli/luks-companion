// ===================================================================
// LUKS COMPANION DASHBOARD & IN-BROWSER STEGANOGRAPHY CLIENT
// 100% Client-Side Web Crypto API (In-Memory Processing, Zero Server Key Leakage)
// ===================================================================

const MAGIC_HEADER = new TextEncoder().encode("__LUKS_KEY_STEGO_V1__");

let currentTab = "passphrase"; // 'passphrase', 'keyfile', 'stego'
let selectedKeyfileBase64 = null;
let selectedKeyfileName = null;

let selectedStegoImageBytes = null;
let selectedStegoImageName = null;

// Stego Studio State
let studioCoverBytes = null;
let studioCoverName = "cover.jpg";
let studioGeneratedKey = null; // Uint8Array(512)

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

            // Update Studio AddKey command dynamically if VG / LV known
            const luksCmdBox = document.getElementById("luksAddKeyCmd");
            if (luksCmdBox && data.vg_name) {
                luksCmdBox.textContent = `sudo vgchange -ay ${data.vg_name}\nsudo cryptsetup luksAddKey /dev/${data.vg_name}/lv_crypto vault.key`;
            }
        }
    } catch (err) {
        const masterBadge = document.getElementById("masterStatusBadge");
        const masterText = document.getElementById("masterStatusText");
        masterBadge.className = "badge badge-red";
        masterText.textContent = "DISCONNESSO DAL DEMONE";
    }
}

// -------------------------------------------------------------------
// 2. TAB SWITCHING (PASSPHRASE / KEYFILE / STEGO)
// -------------------------------------------------------------------
function setTab(tab) {
    currentTab = tab;
    document.getElementById("tabPassphrase").className = `tab-btn ${tab === 'passphrase' ? 'active' : ''}`;
    document.getElementById("tabKeyfile").className = `tab-btn ${tab === 'keyfile' ? 'active' : ''}`;
    document.getElementById("tabStego").className = `tab-btn ${tab === 'stego' ? 'active' : ''}`;

    document.getElementById("secPassphrase").style.display = tab === 'passphrase' ? 'block' : 'none';
    document.getElementById("secKeyfile").style.display = tab === 'keyfile' ? 'block' : 'none';
    document.getElementById("secStego").style.display = tab === 'stego' ? 'block' : 'none';
}

// -------------------------------------------------------------------
// 3. CRYPTOGRAPHIC PRIMITIVES (Web Crypto API in RAM)
// -------------------------------------------------------------------
function xorUint8(a, b) {
    const res = new Uint8Array(a.length);
    for (let i = 0; i < a.length; i++) {
        res[i] = a[i] ^ b[i];
    }
    return res;
}

async function deriveKeystream(passphrase, length, salt) {
    if (!passphrase) {
        return new Uint8Array(length);
    }
    const rawPass = new TextEncoder().encode(passphrase);
    const baseKey = await crypto.subtle.importKey(
        "raw",
        rawPass,
        { name: "PBKDF2" },
        false,
        ["deriveBits"]
    );
    const derivedBuffer = await crypto.subtle.deriveBits(
        {
            name: "PBKDF2",
            salt: salt,
            iterations: 100000,
            hash: "SHA-256"
        },
        baseKey,
        length * 8
    );
    return new Uint8Array(derivedBuffer);
}

async function computeHmacTag(salt, passphrase, encryptedKey) {
    const rawPass = passphrase ? new TextEncoder().encode(passphrase) : new Uint8Array(0);
    const combined = new Uint8Array(salt.length + rawPass.length);
    combined.set(salt);
    combined.set(rawPass, salt.length);

    const macKeyHash = await crypto.subtle.digest("SHA-256", combined);
    const hmacKey = await crypto.subtle.importKey(
        "raw",
        macKeyHash,
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"]
    );
    const sig = await crypto.subtle.sign("HMAC", hmacKey, encryptedKey);
    return new Uint8Array(sig.slice(0, 16));
}

// -------------------------------------------------------------------
// 4. STEGO EXTRACTION IN CLIENT RAM
// -------------------------------------------------------------------
function findMagicHeaderIndex(data, magic) {
    for (let i = data.length - magic.length; i >= 0; i--) {
        let match = true;
        for (let j = 0; j < magic.length; j++) {
            if (data[i + j] !== magic[j]) {
                match = false;
                break;
            }
        }
        if (match) return i;
    }
    return -1;
}

async function extractKeyFromStegoBytes(imageBytes, stegoPassword) {
    const idx = findMagicHeaderIndex(imageBytes, MAGIC_HEADER);
    if (idx === -1) {
        throw new Error("Nessun payload stenografico LUKS rilevato nell'immagine!");
    }

    const payload = imageBytes.subarray(idx + MAGIC_HEADER.length);
    if (payload.length < 36) {
        throw new Error("Payload stenografico corrotto o incompleto!");
    }

    const salt = payload.subarray(0, 16);
    const authTag = payload.subarray(16, 32);
    const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
    const keyLen = view.getUint32(32, false); // big-endian
    const encryptedKey = payload.subarray(36, 36 + keyLen);

    if (encryptedKey.length !== keyLen) {
        throw new Error("Lunghezza chiave stenografica non valida!");
    }

    // Verify HMAC tag
    const expectedTag = await computeHmacTag(salt, stegoPassword, encryptedKey);
    for (let i = 0; i < 16; i++) {
        if (authTag[i] !== expectedTag[i]) {
            throw new Error("Password stenografica errata o immagine manomessa!");
        }
    }

    const keystream = await deriveKeystream(stegoPassword, keyLen, salt);
    return xorUint8(encryptedKey, keystream);
}

// -------------------------------------------------------------------
// 5. UNLOCK ACTION
// -------------------------------------------------------------------
function bytesToBase64(bytes) {
    let binary = "";
    const len = bytes.byteLength;
    for (let i = 0; i < len; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary);
}

async function performUnlock() {
    const unlockBtn = document.getElementById("btnUnlock");
    unlockBtn.disabled = true;
    unlockBtn.textContent = "⏳ Sblocco in corso...";

    let payload = {};

    try {
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

        } else if (currentTab === "keyfile") {
            if (!selectedKeyfileBase64) {
                alert("Selezionare o trascinare un file chiave (.key/.bin) valido.");
                unlockBtn.disabled = false;
                unlockBtn.textContent = "🔑 Sblocca Storage";
                return;
            }
            payload.keyfile_base64 = selectedKeyfileBase64;
            logConsole(`Invio richiesta di sblocco tramite Keyfile in-memory (${selectedKeyfileName})...`);

        } else if (currentTab === "stego") {
            if (!selectedStegoImageBytes) {
                alert("Selezionare o trascinare una foto stenografica valida.");
                unlockBtn.disabled = false;
                unlockBtn.textContent = "🔑 Sblocca Storage";
                return;
            }

            const stegoPass = document.getElementById("inputStegoPass").value || "";
            logConsole("Decifratura ed estrazione stenografica in memoria RAM browser...");

            const rawKeyBytes = await extractKeyFromStegoBytes(selectedStegoImageBytes, stegoPass);
            logConsole(`✓ Chiave (${rawKeyBytes.length} bytes) estratta ed autenticata in RAM con successo!`);
            
            payload.keyfile_base64 = bytesToBase64(rawKeyBytes);
        }

        const res = await fetch("/api/unlock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const json = await res.json();

        if (json.status === "ok") {
            logConsole(`[SUCCESSO] ${json.message}`);
            if (json.output) logConsole(json.output);
            
            // Clear sensitive input
            document.getElementById("inputPassphrase").value = "";
            document.getElementById("inputStegoPass").value = "";
            selectedKeyfileBase64 = null;
            selectedStegoImageBytes = null;
            document.getElementById("keyfileSelectedText").style.display = "none";
            document.getElementById("stegoSelectedText").style.display = "none";
        } else {
            logConsole(`[ERRORE] ${json.message || 'Sblocco fallito'}`);
            alert(`Errore sblocco: ${json.message}`);
        }
    } catch (err) {
        logConsole(`[ERRORE] ${err.message}`);
        alert(`Errore: ${err.message}`);
    } finally {
        unlockBtn.textContent = "🔑 Sblocca Storage";
        updateStatus();
    }
}

// -------------------------------------------------------------------
// 6. STEGO KEY STUDIO (In-Browser Generator & Embedder)
// -------------------------------------------------------------------
function openStegoStudioModal() {
    generateNewRandomKey();
    document.getElementById("stegoStudioModal").classList.add("active");
}

function closeStegoStudioModal() {
    document.getElementById("stegoStudioModal").classList.remove("active");
}

function generateNewRandomKey() {
    studioGeneratedKey = new Uint8Array(512);
    crypto.getRandomValues(studioGeneratedKey);
    const keyGenStatus = document.getElementById("keyGenStatus");
    if (keyGenStatus) {
        keyGenStatus.textContent = `✓ Nuova chiave CSPRNG 4096-bit generata in RAM (${new Date().toLocaleTimeString()})`;
    }
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function downloadRawKeyfile() {
    if (!studioGeneratedKey) return;
    const blob = new Blob([studioGeneratedKey], { type: "application/octet-stream" });
    downloadBlob(blob, "vault.key");
}

async function buildAndDownloadStegoImage() {
    if (!studioCoverBytes) {
        alert("Selezionare prima un'immagine di copertina (Step 1).");
        return;
    }
    if (!studioGeneratedKey) {
        generateNewRandomKey();
    }

    const stegoPass = document.getElementById("studioStegoPassword").value || "";
    const keyLen = studioGeneratedKey.length;

    const salt = new Uint8Array(16);
    crypto.getRandomValues(salt);

    const keystream = await deriveKeystream(stegoPass, keyLen, salt);
    const encryptedKey = xorUint8(studioGeneratedKey, keystream);
    const authTag = await computeHmacTag(salt, stegoPass, encryptedKey);

    // Build payload: MAGIC (21B) + Salt (16B) + AuthTag (16B) + Length (4B) + EncryptedKey (N)
    const lenBuffer = new ArrayBuffer(4);
    new DataView(lenBuffer).setUint32(0, keyLen, false);
    const lenBytes = new Uint8Array(lenBuffer);

    const payload = new Uint8Array(
        MAGIC_HEADER.length + salt.length + authTag.length + lenBytes.length + encryptedKey.length
    );

    let offset = 0;
    payload.set(MAGIC_HEADER, offset); offset += MAGIC_HEADER.length;
    payload.set(salt, offset); offset += salt.length;
    payload.set(authTag, offset); offset += authTag.length;
    payload.set(lenBytes, offset); offset += lenBytes.length;
    payload.set(encryptedKey, offset);

    // Combine cover + payload
    const finalImage = new Uint8Array(studioCoverBytes.length + payload.length);
    finalImage.set(studioCoverBytes, 0);
    finalImage.set(payload, studioCoverBytes.length);

    // Trigger download of stego image
    const blob = new Blob([finalImage], { type: "image/jpeg" });
    const outputName = `stego_${studioCoverName.replace(/\.[^/.]+$/, "")}.jpg`;
    downloadBlob(blob, outputName);

    // Also offer raw vault.key
    downloadRawKeyfile();

    document.getElementById("studioResultBox").style.display = "block";
    logConsole(`[STUDIO] Immagine steganografica creata e scaricata: ${outputName}`);
}

// -------------------------------------------------------------------
// 7. DRAG & DROP SETUP
// -------------------------------------------------------------------
function setupAllDropzones() {
    // 1. Direct Keyfile Dropzone
    const kDrop = document.getElementById("keyfileDropzone");
    const kInput = document.getElementById("keyfileInput");
    kDrop.addEventListener("click", () => kInput.click());
    kDrop.addEventListener("dragover", (e) => { e.preventDefault(); kDrop.classList.add("dragover"); });
    kDrop.addEventListener("dragleave", () => kDrop.classList.remove("dragover"));
    kDrop.addEventListener("drop", (e) => {
        e.preventDefault();
        kDrop.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) loadRawKeyfile(e.dataTransfer.files[0]);
    });
    kInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) loadRawKeyfile(e.target.files[0]);
    });

    // 2. Stego Image Dropzone
    const sDrop = document.getElementById("stegoDropzone");
    const sInput = document.getElementById("stegoInput");
    sDrop.addEventListener("click", () => sInput.click());
    sDrop.addEventListener("dragover", (e) => { e.preventDefault(); sDrop.classList.add("dragover"); });
    sDrop.addEventListener("dragleave", () => sDrop.classList.remove("dragover"));
    sDrop.addEventListener("drop", (e) => {
        e.preventDefault();
        sDrop.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) loadStegoImage(e.dataTransfer.files[0]);
    });
    sInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) loadStegoImage(e.target.files[0]);
    });

    // 3. Studio Cover Dropzone
    const scDrop = document.getElementById("studioCoverDropzone");
    const scInput = document.getElementById("studioCoverInput");
    scDrop.addEventListener("click", () => scInput.click());
    scDrop.addEventListener("dragover", (e) => { e.preventDefault(); scDrop.classList.add("dragover"); });
    scDrop.addEventListener("dragleave", () => scDrop.classList.remove("dragover"));
    scDrop.addEventListener("drop", (e) => {
        e.preventDefault();
        scDrop.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) loadStudioCover(e.dataTransfer.files[0]);
    });
    scInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) loadStudioCover(e.target.files[0]);
    });
}

function loadRawKeyfile(file) {
    selectedKeyfileName = file.name;
    const reader = new FileReader();
    reader.onload = function(e) {
        const bytes = new Uint8Array(e.target.result);
        selectedKeyfileBase64 = bytesToBase64(bytes);
        const txt = document.getElementById("keyfileSelectedText");
        txt.textContent = `✓ File caricato: ${selectedKeyfileName} (${bytes.length} bytes)`;
        txt.style.display = "block";
        logConsole(`Keyfile caricato in memoria RAM: ${selectedKeyfileName} (${bytes.length} bytes)`);
    };
    reader.readAsArrayBuffer(file);
}

function loadStegoImage(file) {
    selectedStegoImageName = file.name;
    const reader = new FileReader();
    reader.onload = function(e) {
        selectedStegoImageBytes = new Uint8Array(e.target.result);
        const txt = document.getElementById("stegoSelectedText");
        txt.textContent = `✓ Foto caricata: ${selectedStegoImageName} (${selectedStegoImageBytes.length} bytes)`;
        txt.style.display = "block";
        logConsole(`Foto stenografica caricata in RAM: ${selectedStegoImageName}`);
    };
    reader.readAsArrayBuffer(file);
}

function loadStudioCover(file) {
    studioCoverName = file.name;
    const reader = new FileReader();
    reader.onload = function(e) {
        studioCoverBytes = new Uint8Array(e.target.result);
        const txt = document.getElementById("studioCoverSelectedText");
        txt.textContent = `✓ Foto selezionata: ${studioCoverName} (${studioCoverBytes.length} bytes)`;
        txt.style.display = "block";
    };
    reader.readAsArrayBuffer(file);
}

// -------------------------------------------------------------------
// 8. STOP / TEARDOWN MODAL
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
    setupAllDropzones();
    updateStatus();
    setInterval(updateStatus, 5000);
    logConsole("LUKS Companion Dashboard inizializzata. In attesa di comandi.");
});
