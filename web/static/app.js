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
// 0. MODERN TOAST NOTIFICATIONS (Replaces browser alert())
// -------------------------------------------------------------------
function showToast(title, message, type = "info", duration = 5000) {
    let container = document.getElementById("toastContainer");
    if (!container) {
        container = document.createElement("div");
        container.id = "toastContainer";
        document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;

    const icons = {
        success: "✓",
        error: "✕",
        warning: "⚠",
        info: "ℹ"
    };

    toast.innerHTML = `
        <div class="toast-icon">${icons[type] || "ℹ"}</div>
        <div class="toast-content">
            ${title ? `<div class="toast-title">${title}</div>` : ""}
            <div>${message}</div>
        </div>
        <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
    `;

    container.appendChild(toast);

    if (duration > 0) {
        setTimeout(() => {
            if (toast.parentElement) {
                toast.classList.add("toast-out");
                setTimeout(() => toast.remove(), 200);
            }
        }, duration);
    }
}

// // -------------------------------------------------------------------
// 1. STATUS POLLING & UI UPDATE
// -------------------------------------------------------------------
function applyStatusData(data) {
    if (!data) return;

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
    document.getElementById("valLvm").textContent = data.vg_active ? `Attivo (${data.vg_name})` : "Disattivato";
    document.getElementById("valLuks").textContent = data.unlocked ? `Sbloccato (/dev/mapper/${data.mapper_name || '...' })` : "Sigillato (0 byte in RAM)";
    document.getElementById("valMount").textContent = data.mounted ? (data.mount_crypto || "Montato") : "Non montato";
    
    const webdavPortStr = data.webdav_port ? ` (Porta ${data.webdav_port})` : "";
    document.getElementById("valWebdav").textContent = data.webdav_active ? `Attivo${webdavPortStr}` : "Inattivo";

    // Telemetry: S.M.A.R.T. & Hardware Health
    const badgeSmartHealth = document.getElementById("badgeSmartHealth");
    const badgeSmartTemp = document.getElementById("badgeSmartTemp");
    const badgeSmartDevice = document.getElementById("badgeSmartDevice");

    if (data.status === "stopped") {
        if (badgeSmartHealth) {
            badgeSmartHealth.className = "badge badge-gray";
            badgeSmartHealth.textContent = "S.M.A.R.T.: Standby";
        }
        if (badgeSmartTemp) {
            badgeSmartTemp.className = "badge badge-gray";
            badgeSmartTemp.textContent = "🌡️ -- °C";
        }
        if (badgeSmartDevice) {
            badgeSmartDevice.textContent = "Disco: 0W Standby";
        }
    } else {
        const smart = data.smart || {};
        if (badgeSmartHealth) {
            if (smart.installed === false) {
                badgeSmartHealth.className = "badge badge-amber";
                badgeSmartHealth.textContent = "smartctl: Non installato";
                badgeSmartHealth.title = "Installa smartmontools sul server";
            } else if (smart.supported && smart.health === "PASSED") {
                badgeSmartHealth.className = "badge badge-green";
                badgeSmartHealth.textContent = "S.M.A.R.T.: Integro (PASSED)";
            } else if (smart.supported && smart.health === "FAILED") {
                badgeSmartHealth.className = "badge badge-red";
                badgeSmartHealth.textContent = "S.M.A.R.T.: ALLARME GUASTO (FAILED)";
            } else if (smart.reason) {
                badgeSmartHealth.className = "badge badge-gray";
                badgeSmartHealth.textContent = `S.M.A.R.T.: ${smart.reason}`;
            } else {
                badgeSmartHealth.className = "badge badge-gray";
                badgeSmartHealth.textContent = "S.M.A.R.T.: N/D";
            }
        }

        if (badgeSmartTemp) {
            if (smart.temperature_c !== null && smart.temperature_c !== undefined) {
                const temp = smart.temperature_c;
                if (temp >= 55) {
                    badgeSmartTemp.className = "badge badge-red";
                    badgeSmartTemp.textContent = `🌡️ ${temp} °C (Caldo)`;
                } else if (temp >= 45) {
                    badgeSmartTemp.className = "badge badge-amber";
                    badgeSmartTemp.textContent = `🌡️ ${temp} °C`;
                } else {
                    badgeSmartTemp.className = "badge badge-green";
                    badgeSmartTemp.textContent = `🌡️ ${temp} °C`;
                }
            } else {
                badgeSmartTemp.className = "badge badge-gray";
                badgeSmartTemp.textContent = "🌡️ -- °C";
            }
        }

        if (badgeSmartDevice) {
            if (smart.model) {
                const devName = smart.device ? ` (${smart.device})` : "";
                badgeSmartDevice.textContent = `Disco: ${smart.model}${devName}`;
            } else {
                badgeSmartDevice.textContent = "Disco: Attivo";
            }
        }
    }

    // Telemetry: Storage Capacity Bars
    const volContainer = document.getElementById("volumeBarsContainer");
    if (volContainer) {
        if (data.volumes && data.volumes.length > 0) {
            let html = "";
            data.volumes.forEach(vol => {
                let fillClass = "";
                if (vol.used_percent >= 90) fillClass = "danger";
                else if (vol.used_percent >= 75) fillClass = "warn";

                html += `
                    <div class="volume-bar-card">
                        <div class="volume-info">
                            <span class="volume-name">📁 ${vol.name} <small style="color:var(--text-muted);font-weight:normal;">(${vol.mountpoint})</small></span>
                            <span class="volume-usage">${vol.used_human} / ${vol.total_human} (${vol.used_percent}%)</span>
                        </div>
                        <div class="progress-track">
                            <div class="progress-fill ${fillClass}" style="width: ${vol.used_percent}%"></div>
                        </div>
                        <div class="volume-footer">
                            <span>Spazio disponibile: ${vol.free_human}</span>
                            <span>Capacità totale: ${vol.total_human}</span>
                        </div>
                    </div>
                `;
            });
            volContainer.innerHTML = html;
        } else if (data.status === "stopped") {
            volContainer.innerHTML = `<div class="volume-placeholder"><span>Storage in standby (0W). I dettagli dello spazio disco e telemetria S.M.A.R.T. saranno disponibili allo sblocco.</span></div>`;
        } else {
            volContainer.innerHTML = `<div class="volume-placeholder"><span>Nessun volume attualmente montato.</span></div>`;
        }
    }
}

async function updateStatus() {
    try {
        const res = await fetch("/api/status");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (json.status === "ok" && json.data) {
            const data = json.data;
            applyStatusData(data);

            const unlockBtn = document.getElementById("btnUnlock");
            const stopBtn = document.getElementById("btnStop");

            if (data.busy) {
                if (data.status === "mounted" || data.status === "unlocked" || data.mounted || data.unlocked) {
                    if (stopBtn) {
                        stopBtn.disabled = true;
                        stopBtn.textContent = "⏳ Operazione in corso...";
                    }
                    if (unlockBtn) unlockBtn.disabled = true;
                } else {
                    if (unlockBtn) {
                        unlockBtn.disabled = true;
                        unlockBtn.textContent = "⏳ Operazione in corso...";
                    }
                    if (stopBtn) stopBtn.disabled = true;
                }
            } else {
                if (data.status === "mounted" || data.status === "unlocked" || data.mounted || data.unlocked) {
                    if (unlockBtn) {
                        unlockBtn.disabled = true;
                        unlockBtn.textContent = "🔑 Sblocca Storage";
                    }
                    if (stopBtn) {
                        stopBtn.disabled = false;
                        stopBtn.textContent = "🛑 Espelli & Spegni 220V";
                    }
                } else {
                    if (unlockBtn) {
                        unlockBtn.disabled = false;
                        unlockBtn.textContent = "🔑 Sblocca Storage";
                    }
                    if (stopBtn) {
                        stopBtn.disabled = true;
                        stopBtn.textContent = "🛑 Espelli & Spegni 220V";
                    }
                }
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
        if (masterBadge) masterBadge.className = "badge badge-red";
        if (masterText) masterText.textContent = "DISCONNESSO DAL DEMONE";
    }
}

async function processNdjsonStream(res, onLine, onData) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let finalResult = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const raw of lines) {
            const line = raw.trim();
            if (!line) continue;
            try {
                const eventObj = JSON.parse(line);
                if (eventObj.event === "log" && eventObj.line) {
                    if (onLine) onLine(eventObj.line);
                    if (eventObj.data && onData) onData(eventObj.data);
                } else if (eventObj.event === "done") {
                    finalResult = eventObj;
                    if (eventObj.data && onData) onData(eventObj.data);
                }
            } catch (e) {
                if (onLine) onLine(line);
            }
        }
    }
    if (buffer.trim()) {
        try {
            const eventObj = JSON.parse(buffer.trim());
            if (eventObj.event === "done") finalResult = eventObj;
        } catch (e) {}
    }
    return finalResult;
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

    const masterBadge = document.getElementById("masterStatusBadge");
    const masterText = document.getElementById("masterStatusText");
    if (masterBadge) masterBadge.className = "badge badge-amber";
    if (masterText) masterText.textContent = "SBLOCCO IN CORSO...";

    let payload = {};

    try {
        if (currentTab === "passphrase") {
            const passphrase = document.getElementById("inputPassphrase").value;
            if (!passphrase) {
                showToast("Attenzione", "Inserire la passphrase prima di continuare.", "warning");
                unlockBtn.disabled = false;
                unlockBtn.textContent = "🔑 Sblocca Storage";
                return;
            }
            payload.passphrase = passphrase;
            logConsole("Invio richiesta di sblocco tramite Passphrase...");

        } else if (currentTab === "keyfile") {
            if (!selectedKeyfileBase64) {
                showToast("Attenzione", "Selezionare o trascinare un file chiave (.key/.bin) valido.", "warning");
                unlockBtn.disabled = false;
                unlockBtn.textContent = "🔑 Sblocca Storage";
                return;
            }
            payload.keyfile_base64 = selectedKeyfileBase64;
            logConsole(`Invio richiesta di sblocco tramite Keyfile in-memory (${selectedKeyfileName})...`);

        } else if (currentTab === "stego") {
            if (!selectedStegoImageBytes) {
                showToast("Attenzione", "Selezionare o trascinare una foto stenografica valida.", "warning");
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

        const json = await processNdjsonStream(
            res,
            (line) => {
                logConsole(line);
                // Real-time progressive step updates
                if (line.includes("[1/7]")) {
                    if (masterText) masterText.textContent = "⚡ ACCENSIONE PRESA...";
                } else if (line.includes("[2/7]")) {
                    if (masterText) masterText.textContent = "🔌 ATTESA DISCO USB...";
                } else if (line.includes("[3/7]")) {
                    if (masterText) masterText.textContent = "📦 ATTIVAZIONE LVM...";
                    const lvmEl = document.getElementById("valLvm");
                    if (lvmEl) lvmEl.textContent = "Attivazione...";
                } else if (line.includes("[4/7]")) {
                    if (masterText) masterText.textContent = "🔑 SBLOCCO LUKS IN RAM...";
                } else if (line.includes("[5/7]")) {
                    if (masterText) masterText.textContent = "📁 MONTAGGIO FILESYSTEM...";
                    const luksEl = document.getElementById("valLuks");
                    if (luksEl) luksEl.textContent = "Sbloccato (RAM)";
                } else if (line.includes("[6/7]") || line.includes("WebDAV")) {
                    if (masterText) masterText.textContent = "🌐 AVVIO SERVIZI...";
                    const mountEl = document.getElementById("valMount");
                    if (mountEl) mountEl.textContent = "Montato";
                }
            },
            (data) => {
                applyStatusData(data);
            }
        );

        if (json && json.status === "ok") {
            logConsole(`[SUCCESSO] ${json.message}`);
            showToast("Volume Sbloccato", json.message || "Storage montato e pronto all'uso!", "success");
            
            // Clear sensitive input
            document.getElementById("inputPassphrase").value = "";
            document.getElementById("inputStegoPass").value = "";
            selectedKeyfileBase64 = null;
            selectedStegoImageBytes = null;
            const kTxt = document.getElementById("keyfileSelectedText");
            if (kTxt) kTxt.style.display = "none";
            const sTxt = document.getElementById("stegoSelectedText");
            if (sTxt) sTxt.style.display = "none";
        } else {
            const errMsg = (json && json.message) || "Impossibile sbloccare il container LUKS";
            logConsole(`[ERRORE] ${errMsg}`);
            showToast("Errore Sblocco", errMsg, "error");
        }
    } catch (err) {
        logConsole(`[ERRORE] ${err.message}`);
        showToast("Errore", err.message, "error");
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
    showToast("Download Chiave", "File vault.key scaricato con successo!", "info");
}

async function buildAndDownloadStegoImage() {
    if (!studioCoverBytes) {
        showToast("Attenzione", "Selezionare prima un'immagine di copertina (Step 1).", "warning");
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
    showToast("Creazione Completata", `Immagine ${outputName} e vault.key generati e scaricati!`, "success");
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
    // 4. Header Restore Dropzone
    const hrDrop = document.getElementById("headerRestoreDropzone");
    const hrInput = document.getElementById("headerRestoreInput");
    if (hrDrop && hrInput) {
        hrDrop.addEventListener("click", () => hrInput.click());
        hrDrop.addEventListener("dragover", (e) => { e.preventDefault(); hrDrop.classList.add("dragover"); });
        hrDrop.addEventListener("dragleave", () => hrDrop.classList.remove("dragover"));
        hrDrop.addEventListener("drop", (e) => {
            e.preventDefault();
            hrDrop.classList.remove("dragover");
            if (e.dataTransfer.files.length > 0) loadHeaderRestoreFile(e.dataTransfer.files[0]);
        });
        hrInput.addEventListener("change", (e) => {
            if (e.target.files.length > 0) loadHeaderRestoreFile(e.target.files[0]);
        });
    }

    const chkRestore = document.getElementById("chkConfirmHeaderRestore");
    if (chkRestore) {
        chkRestore.addEventListener("change", updateRestoreButtonState);
    }
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
        showToast("Keyfile Caricato", `${selectedKeyfileName} (${bytes.length} bytes)`, "info");
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
        showToast("Foto Caricata", `${selectedStegoImageName}`, "info");
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

    const masterBadge = document.getElementById("masterStatusBadge");
    const masterText = document.getElementById("masterStatusText");
    if (masterBadge) masterBadge.className = "badge badge-amber";
    if (masterText) masterText.textContent = "ARRESTO IN CORSO...";

    logConsole("Avvio procedura di arresto e spegnimento 220V...");

    try {
        const res = await fetch("/api/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
        });

        const json = await processNdjsonStream(
            res,
            (line) => {
                logConsole(line);
                if (line.includes("WebDAV")) {
                    const wEl = document.getElementById("valWebdav");
                    if (wEl) wEl.textContent = "Inattivo";
                } else if (line.includes("Smontaggio") || line.includes("umount")) {
                    const mEl = document.getElementById("valMount");
                    if (mEl) mEl.textContent = "Smontato";
                } else if (line.includes("Chiusura container LUKS")) {
                    const luksEl = document.getElementById("valLuks");
                    if (luksEl) luksEl.textContent = "Sigillato (0 byte)";
                } else if (line.includes("Disattivazione Volume Group")) {
                    const lvmEl = document.getElementById("valLvm");
                    if (lvmEl) lvmEl.textContent = "Disattivato";
                } else if (line.includes("Spegnimento alimentazione")) {
                    if (masterText) masterText.textContent = "⚡ SPEGNIMENTO 220V...";
                }
            },
            (data) => {
                applyStatusData(data);
            }
        );

        if (json && json.status === "ok") {
            logConsole(`[SUCCESSO] ${json.message}`);
            showToast("Arresto Completato", json.message || "Filesystem smontati e alimentazione 220V disattivata.", "success");
        } else {
            const errMsg = (json && json.message) || "Errore durante l'arresto";
            logConsole(`[ERRORE] ${errMsg}`);
            showToast("Errore Arresto", errMsg, "error");
        }
    } catch (err) {
        logConsole(`[ERRORE RETE] ${err.message}`);
        showToast("Errore di Rete", err.message, "error");
    } finally {
        stopBtn.textContent = "🛑 Espelli & Spegni 220V";
        updateStatus();
    }
}

// -------------------------------------------------------------------
// 9. LUKS HEADER BACKUP & DISASTER RECOVERY
// -------------------------------------------------------------------
let selectedHeaderRestoreBase64 = null;
let selectedHeaderRestoreName = null;

function openHeaderBackupModal() {
    setHeaderTab('backup');
    document.getElementById("headerBackupModal").classList.add("active");
}

function closeHeaderBackupModal() {
    document.getElementById("headerBackupModal").classList.remove("active");
}

function setHeaderTab(tab) {
    const tabB = document.getElementById("tabHeaderBackup");
    const tabR = document.getElementById("tabHeaderRestore");
    const secB = document.getElementById("secHeaderBackup");
    const secR = document.getElementById("secHeaderRestore");

    if (tabB) tabB.classList.toggle("active", tab === "backup");
    if (tabR) tabR.classList.toggle("active", tab === "restore");
    if (secB) secB.style.display = tab === "backup" ? "block" : "none";
    if (secR) secR.style.display = tab === "restore" ? "block" : "none";
}

async function downloadHeaderBackup() {
    const btn = document.getElementById("btnDownloadHeader");
    btn.disabled = true;
    btn.textContent = "⏳ Generazione Backup Header...";

    logConsole("Richiesta backup header LUKS in memoria sicura (/dev/shm)...");

    try {
        const res = await fetch("/api/header/backup");
        if (!res.ok) {
            const errJson = await res.json().catch(() => ({}));
            throw new Error(errJson.message || `Errore HTTP ${res.status}`);
        }

        const disposition = res.headers.get("Content-Disposition") || "";
        let filename = "luks_header_backup.header";
        const match = disposition.match(/filename="?([^";]+)"?/);
        if (match && match[1]) filename = match[1];

        const blob = await res.blob();
        downloadBlob(blob, filename);

        logConsole(`✓ Backup Header LUKS (${blob.size} bytes) scaricato con successo: ${filename}`);
        showToast("Backup Header Completato", `File ${filename} scaricato (${(blob.size / 1024 / 1024).toFixed(1)} MB)`, "success");
    } catch (err) {
        logConsole(`[ERRORE BACKUP] ${err.message}`);
        showToast("Errore Backup Header", err.message, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "⬇️ Genera & Scarica Backup Header (.header)";
    }
}

function loadHeaderRestoreFile(file) {
    selectedHeaderRestoreName = file.name;
    const reader = new FileReader();
    reader.onload = function(e) {
        const bytes = new Uint8Array(e.target.result);
        selectedHeaderRestoreBase64 = bytesToBase64(bytes);
        const txt = document.getElementById("headerRestoreSelectedText");
        if (txt) {
            txt.textContent = `✓ File header caricato: ${selectedHeaderRestoreName} (${bytes.length} bytes)`;
            txt.style.display = "block";
        }
        updateRestoreButtonState();
        logConsole(`File Header caricato in memoria browser: ${selectedHeaderRestoreName} (${bytes.length} bytes)`);
        showToast("Header Caricato", `${selectedHeaderRestoreName}`, "info");
    };
    reader.readAsArrayBuffer(file);
}

function updateRestoreButtonState() {
    const chk = document.getElementById("chkConfirmHeaderRestore");
    const btn = document.getElementById("btnRestoreHeader");
    if (btn) {
        btn.disabled = !(selectedHeaderRestoreBase64 && chk && chk.checked);
    }
}

async function performHeaderRestore() {
    if (!selectedHeaderRestoreBase64) {
        showToast("Attenzione", "Selezionare prima un file .header valido.", "warning");
        return;
    }

    const btn = document.getElementById("btnRestoreHeader");
    btn.disabled = true;
    btn.textContent = "⏳ Ripristino in corso...";

    logConsole(`Avvio Disaster Recovery: ripristino header LUKS da ${selectedHeaderRestoreName}...`);

    try {
        const res = await fetch("/api/header/restore", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ header_base64: selectedHeaderRestoreBase64 })
        });
        const json = await res.json();

        if (json.status === "ok") {
            logConsole(`[SUCCESSO DISASTER RECOVERY] ${json.message}`);
            showToast("Header Ripristinato", json.message, "success");
            closeHeaderBackupModal();
            selectedHeaderRestoreBase64 = null;
        } else {
            logConsole(`[ERRORE DISASTER RECOVERY] ${json.message}`);
            showToast("Errore Ripristino", json.message, "error");
        }
    } catch (err) {
        logConsole(`[ERRORE] ${err.message}`);
        showToast("Errore di Rete", err.message, "error");
    } finally {
        btn.textContent = "⚠️ Avvia Ripristino Header";
        updateRestoreButtonState();
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
