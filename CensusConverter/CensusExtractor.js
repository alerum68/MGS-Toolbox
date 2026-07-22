// ==UserScript==
// @name         Census Extractor
// @namespace    https://github.com/alerum68/MGS-Toolbox
// @version      0.30
// @description  Combines the Blob downloader with advanced transcription PID extraction. Includes Page Signature locking to prevent stale DOM reads.
// @author       alerum68
// @match        *://*.ancestry.com/imageviewer*
// @connect      *
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @run-at       document-start
// ==/UserScript==

(function () {
    'use strict';

    // GUARD: Check if the extractor has been explicitly triggered for this session or via URL
    if (sessionStorage.getItem('run_census_extractor') !== 'true' && !window.location.href.includes('mgs_auto=1')) {
        return; // Exit immediately, doing nothing
    }

    // Clear the flag so it only runs once per trigger (optional)
    sessionStorage.removeItem('run_census_extractor');

    const DEBUG_MODE = true;
    let isAutoExtracting = false;
    let accumulatedCsvData = [];
    let headerParsed = false;
    let batchPageCounter = 1;
    let seenPids = new Set();
    let lastPageSignature = "INITIAL_STATE_NOT_SET";

    const shouldAutoStart = window.location.href.includes('mgs_auto=1');

    const sleep = (ms) => new Promise(r => setTimeout(r, ms));

    function debugLog(msg) {
        if (DEBUG_MODE) {
            console.log(`[MGS DEBUG] ${msg}`);
        }
    }

    if (typeof unsafeWindow !== 'undefined' && !unsafeWindow.__mgs_intercepted) {
        unsafeWindow.__mgs_intercepted = true;
        unsafeWindow.__mgs_pids = [];

        function extractPidsFromText(text) {
            try {
                let parsed;
                try {
                    parsed = JSON.parse(text);
                } catch (e) {
                    parsed = null;
                }

                if (parsed && Array.isArray(parsed.RecordRectangles) && parsed.RecordRectangles.length > 0) {
                    const ids = parsed.RecordRectangles
                        .map(r => (r && r.RecordId != null) ? String(r.RecordId) : null)
                        .filter(id => id !== null);
                    if (ids.length > 0) {
                        unsafeWindow.__mgs_pids = ids;
                        return;
                    }
                }

                const matches = [...text.matchAll(/"(?:recordId|pId|clientRecordId)"\s*:\s*"?(\d{5,15})"?/gi)];
                if (matches.length > 2) {
                    unsafeWindow.__mgs_pids = matches.map(m => m[1]);
                }
            } catch (e) {
            }
        }

        const origFetch = unsafeWindow.fetch;
        unsafeWindow.fetch = async function (...args) {
            const response = await origFetch.apply(this, args);
            try {
                const clone = response.clone();
                clone.text().then(text => extractPidsFromText(text)).catch(e => {
                });
            } catch (e) {
            }
            return response;
        };

        const origOpen = unsafeWindow.XMLHttpRequest.prototype.open;
        unsafeWindow.XMLHttpRequest.prototype.open = function (method, url) {
            this.addEventListener('load', function () {
                extractPidsFromText(this.responseText);
            });
            origOpen.apply(this, arguments);
        };
    }

    function initUI() {
        if (document.getElementById('extractor-ui-container')) return;

        const style = document.createElement('style');
        style.innerHTML = `
            #extractor-ui-container { position: fixed; bottom: 20px; right: 20px; background-color: #1a1a1a; color: white; border-radius: 8px; padding: 12px 16px; font-family: sans-serif; z-index: 999999; box-shadow: 0 4px 12px rgba(0,0,0,0.5); display: flex; flex-direction: column; gap: 10px; align-items: center; border: 1px solid #333; min-width: 180px; }
            #extractor-header { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; padding-bottom: 4px; border-bottom: 1px solid #333; }
            #extractor-title { font-weight: bold; font-size: 15px; }
            #extractor-status-light { width: 12px; height: 12px; border-radius: 50%; background-color: #22c55e; box-shadow: 0 0 8px #22c55e; transition: all 0.3s ease; }
            #extractor-status-light.running { background-color: #3b82f6; box-shadow: 0 0 10px #3b82f6; animation: pulse-light 1.5s infinite; }
            @keyframes pulse-light { 0% { transform: scale(0.95); opacity: 0.8; } 50% { transform: scale(1.1); opacity: 1; } 100% { transform: scale(0.95); opacity: 0.8; } }
            .extractor-btn { color: white; border: none; border-radius: 6px; padding: 10px 14px; font-size: 14px; font-weight: bold; cursor: pointer; width: 100%; transition: background-color 0.2s; }
            #ext-start-btn { background-color: #2b7a4b; } #ext-start-btn:hover { background-color: #1e5935; }
            #ext-stop-btn { background-color: #991b1b; display: none; }
            #extractor-toast-container { position: fixed; bottom: 140px; right: 20px; z-index: 999999; display: flex; flex-direction: column; gap: 10px; pointer-events: none; }
            .extractor-toast { background-color: #333; color: #fff; padding: 12px 20px; border-radius: 6px; font-size: 14px; opacity: 0; transform: translateY(10px); transition: opacity 0.3s, transform 0.3s; }
            .extractor-toast.show { opacity: 1; transform: translateY(0); }
            .extractor-toast.error { background-color: #b91c1c; } .extractor-toast.success { background-color: #15803d; }
        `;
        document.head.appendChild(style);

        const toastContainer = document.createElement('div');
        toastContainer.id = 'extractor-toast-container';
        document.body.appendChild(toastContainer);

        window.showToast = function (message, type = 'success', duration = 2500) {
            const toast = document.createElement('div');
            toast.className = `extractor-toast ${type}`;
            toast.innerText = message;
            toastContainer.appendChild(toast);
            void toast.offsetWidth;
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
                setTimeout(() => toast.remove(), 300);
            }, duration);
        }

        const controlPanel = document.createElement('div');
        controlPanel.id = 'extractor-ui-container';

        const header = document.createElement('div');
        header.id = 'extractor-header';
        const statusLight = document.createElement('div');
        statusLight.id = 'extractor-status-light';
        const title = document.createElement('span');
        title.id = 'extractor-title';
        title.innerText = 'Extractor v0.25';

        header.appendChild(statusLight);
        header.appendChild(title);
        controlPanel.appendChild(header);

        const startBtn = document.createElement('button');
        startBtn.id = 'ext-start-btn';
        startBtn.className = 'extractor-btn';
        startBtn.innerText = '🚀 Start Auto-Batch';

        const stopBtn = document.createElement('button');
        stopBtn.id = 'ext-stop-btn';
        stopBtn.className = 'extractor-btn';
        stopBtn.innerText = '🛑 Stop & Download CSV';

        controlPanel.appendChild(startBtn);
        controlPanel.appendChild(stopBtn);
        document.body.appendChild(controlPanel);

        startBtn.addEventListener('click', startBatch);
        stopBtn.addEventListener('click', stopBatch);

        window._startBtn = startBtn;
        window._stopBtn = stopBtn;
        window._statusLight = statusLight;
    }

    function getYearAndLocation() {
        let year = "UnknownYear";
        let locationStr = "Unknown_Location";

        if (typeof unsafeWindow !== 'undefined' && unsafeWindow.__PRELOADED_STATE__) {
            try {
                const state = unsafeWindow.__PRELOADED_STATE__;
                if (state.viewer) {
                    year = state.viewer.collectionInfo?.publicationYear || year;
                    const path = state.viewer.imageInfo?.browsePath;
                    if (path && path.length > 0) locationStr = path.join(' - ');
                }
            } catch (err) {
            }
        }
        if (year === "UnknownYear") {
            const yearMatch = document.title.match(/(1[7-9]\d\d|19[0-6]\d)/);
            if (yearMatch) year = yearMatch[0];
        }
        locationStr = locationStr.replace(/[/\\?%*:|"<>]/g, '-').replace(/\s+/g, ' ').trim();
        return {year, locationStr};
    }

    function getBaseImageId() {
        const urlMatch = window.location.href.match(/images\/([^?&/]+)/);
        if (urlMatch) return urlMatch[1].replace(/[^a-zA-Z0-9_-]/g, '');
        return "Unknown_Image";
    }

    function extractCurrentPageData(rows) {
        let imageId = getBaseImageId();
        let country = "USA", state = "", county = "", city = "", placeDetails = "";

        let dbid = "0";
        const dbMatch = window.location.href.match(/collections\/(\d+)/i) || window.location.href.match(/dbid=(\d+)/i) || window.location.href.match(/view\/\d+:(\d+)/i);
        if (dbMatch) dbid = dbMatch[1];

        if (typeof unsafeWindow !== 'undefined' && unsafeWindow.__PRELOADED_STATE__) {
            const pathArr = unsafeWindow.__PRELOADED_STATE__.viewer?.imageInfo?.browsePath || [];
            state = pathArr[0] || "";
            county = pathArr[1] || "";
            city = pathArr[2] || "";
            placeDetails = pathArr.slice(3).join(" - ") || "";
        }

        let urlPidMatch = window.location.href.match(/pId=(\d+)/i);
        let baseUrlPid = urlPidMatch ? parseInt(urlPidMatch[1]) : 0;
        let rowIndex = 0;

        if (DEBUG_MODE) {
            console.log(`[MGS DEBUG] page ${batchPageCounter} | url: ${window.location.href} | baseUrlPid: ${baseUrlPid} | cached pids: ${typeof unsafeWindow !== 'undefined' ? unsafeWindow.__mgs_pids.length : 'n/a'}`);
        }

        rows.forEach(row => {
            const isHeader = row.classList.contains('indexPanelHeaderRow') || row.querySelectorAll('th, [role="columnheader"]').length > 0;

            if (isHeader) {
                if (!headerParsed) {
                    const rowData = ['"Page_Number"'];
                    row.querySelectorAll('th, td, .grid-cell, [role="columnheader"], [role="gridcell"]').forEach(col => {
                        let text = (col.innerText || col.textContent).replace(/(\r\n|\n|\r)/gm, " ").trim().replace(/"/g, '""');
                        rowData.push('"' + text + '"');
                    });
                    rowData.push('"Country"', '"State"', '"County"', '"City"', '"Place_Details"', '"Image_ID"', '"PID"', '"Extracted_URL"');
                    accumulatedCsvData.push(rowData.join(','));
                    headerParsed = true;
                }
                return;
            }

            const cols = row.querySelectorAll('td, .grid-cell, [role="gridcell"]');
            if (cols.length === 0) return;

            let rowPid = "";
            let rowUrl = "";
            let pidSource = "none";
            const link = row.querySelector('a[href*="records/"]');
            if (link) {
                const match = link.href.match(/records\/(\d+)/);
                if (match) {
                    rowPid = match[1];
                    pidSource = "anchor href";
                }
            }

            if (!rowPid) {
                const domElements = [row, ...row.querySelectorAll('*')];
                for (let el of domElements) {
                    const reactKey = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
                    if (reactKey) {
                        let fiber = el[reactKey];
                        let attempts = 0;
                        while (fiber && attempts < 15) {
                            const props = fiber.memoizedProps || {};
                            let recordsToCheck = [props.rowData, props.record, (props.data ? props.data[props.rowIndex] : null)];
                            recordsToCheck.forEach(rec => {
                                if (rec && typeof rec === 'object') {
                                    let potentialPid = rec.recordId || rec.clientRecordId || rec.id;
                                    if (!rowPid && potentialPid && String(potentialPid).match(/^\d{5,15}$/)) {
                                        rowPid = String(potentialPid);
                                        pidSource = "react fiber";
                                    }
                                }
                            });
                            fiber = fiber.return;
                            attempts++;
                        }
                    }
                    if (rowPid) break;
                }
            }

            if (!rowPid && typeof unsafeWindow !== 'undefined' && unsafeWindow.__mgs_pids[rowIndex]) {
                rowPid = unsafeWindow.__mgs_pids[rowIndex];
                pidSource = "network cache";
            }
            if (!rowPid && baseUrlPid > 0) {
                rowPid = (baseUrlPid + rowIndex).toString();
                pidSource = "math fallback";
            }
            if (rowPid && dbid && dbid !== "0") {
                rowUrl = `https://www.ancestry.com/search/collections/${dbid}/records/${rowPid}`;
            }

            if (rowPid && seenPids.has(rowPid)) {
                if (DEBUG_MODE) {
                    console.log(`[MGS DEBUG] Row ${rowIndex + 1} | PID: ${rowPid} | Source: ${pidSource} | SKIPPED as duplicate`);
                }
                return;
            }
            if (rowPid) {
                seenPids.add(rowPid);
            }

            const rowData = [`"${batchPageCounter}"`];

            cols.forEach(col => {
                let text = (col.innerText || col.textContent).replace(/(\r\n|\n|\r)/gm, " ").trim().replace(/"/g, '""');
                rowData.push('"' + text + '"');
            });

            rowData.push(`"${country}"`, `"${state}"`, `"${county}"`, `"${city}"`, `"${placeDetails}"`, `"${imageId}"`, `"${rowPid}"`, `"${rowUrl}"`);

            debugLog(`Row ${rowIndex + 1} | PID: ${rowPid} | Source: ${pidSource}`);
            accumulatedCsvData.push(rowData.join(','));
            rowIndex++;
        });
    }

    async function downloadCurrentImage() {
        return new Promise((resolve) => {
            let highResUrl = "";
            let imgFileName = getBaseImageId() + ".jpg";

            if (typeof unsafeWindow !== 'undefined' && unsafeWindow.__PRELOADED_STATE__) {
                try {
                    let path = unsafeWindow.__PRELOADED_STATE__.viewer?.imageInfo?.imageDownloadUrl;
                    if (path) {
                        if (path.startsWith('http')) {
                            highResUrl = path;
                        } else {
                            highResUrl = (unsafeWindow.__PRELOADED_STATE__.mainOrigin || "https://www.ancestry.com") + path;
                        }
                    }
                } catch (err) {
                }
            }

            if (!highResUrl) {
                if (window.showToast) window.showToast("Error: Could not find image URL.", "error");
                resolve();
                return;
            }

            GM_xmlhttpRequest({
                method: "GET",
                url: highResUrl,
                responseType: "blob",
                onload: function (response) {
                    if (response.status === 200) {
                        const blob = response.response;
                        const url = URL.createObjectURL(blob);
                        const link = document.createElement("a");
                        link.setAttribute("href", url);
                        link.setAttribute("download", imgFileName);
                        link.style.visibility = 'hidden';

                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);

                        URL.revokeObjectURL(url);
                        if (window.showToast) window.showToast(`Image captured: ${imgFileName}`, 'success', 1000);
                    } else {
                        if (window.showToast) window.showToast(`Failed to fetch image. Status: ${response.status}`, 'error');
                    }
                    resolve();
                },
                onerror: function (err) {
                    if (window.showToast) window.showToast("Network error fetching image.", 'error');
                    resolve();
                }
            });
        });
    }

    function downloadFinalCsv() {
        if (accumulatedCsvData.length === 0) {
            if (window.showToast) window.showToast("No data gathered to download.", "error");
            return;
        }
        const {year, locationStr} = getYearAndLocation();
        const csvFileName = `${year} - ${locationStr}.csv`;

        const csvString = '\uFEFF' + accumulatedCsvData.join('\r\n');
        const blob = new Blob([csvString], {type: 'text/csv;charset=utf-8;'});
        const url = URL.createObjectURL(blob);

        const link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", csvFileName);
        link.style.visibility = 'hidden';

        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        URL.revokeObjectURL(url);
        if (window.showToast) window.showToast("Success! Master CSV Downloaded.", "success", 5000);
    }

    async function runExtractionLoop() {
        while (isAutoExtracting) {

            let renderRetries = 150;
            let toggleBtn = document.getElementById('indexPanelToggle');
            while (!toggleBtn && renderRetries > 0) {
                await sleep(100);
                toggleBtn = document.getElementById('indexPanelToggle');
                renderRetries--;
            }

            let enableRetries = 150;
            while (toggleBtn && (toggleBtn.disabled || toggleBtn.classList.contains('disabled')) && enableRetries > 0) {
                await sleep(100);
                enableRetries--;
            }

            const isUnindexed = toggleBtn && (toggleBtn.disabled || toggleBtn.classList.contains('disabled'));

            if (DEBUG_MODE) {
                console.log(`[MGS DEBUG] page ${batchPageCounter} indexing check | toggleBtn found: ${!!toggleBtn} | renderRetries left: ${renderRetries} | enableRetries left: ${enableRetries} | isUnindexed: ${isUnindexed}`);
                if (isUnindexed) {
                    console.warn(`[MGS DEBUG] SKIPPING extraction on page ${batchPageCounter}: treated as unindexed. toggleBtn: ${toggleBtn ? toggleBtn.outerHTML.slice(0, 150) : 'null'}`);
                }
            }

            if (!isUnindexed) {
                const indexPanel = document.getElementById('indexPanel');
                if (indexPanel && indexPanel.classList.contains('noDisplay') && toggleBtn) {
                    toggleBtn.click();
                    await sleep(500);
                }

                let rows = null;
                let waitRetries = 150;

                while (waitRetries > 0) {
                    let currentRows = document.querySelectorAll('table tr, .grid-row, [role="row"]');
                    if (currentRows.length > 1) {

                        let dataRows = Array.from(currentRows).filter(r => !r.classList.contains('indexPanelHeaderRow') && r.querySelectorAll('th, [role="columnheader"]').length === 0);

                        if (dataRows.length > 0) {
                            let firstRowText = (dataRows[0].innerText || dataRows[0].textContent).trim();
                            let lastRowText = (dataRows[dataRows.length - 1].innerText || dataRows[dataRows.length - 1].textContent).trim();
                            let currentSignature = firstRowText + " | " + lastRowText;

                            if (currentSignature !== lastPageSignature && currentSignature.length > 5) {
                                lastPageSignature = currentSignature;
                                rows = currentRows;
                                break;
                            }
                        }
                    }
                    await sleep(200);
                    waitRetries--;
                }

                if (waitRetries === 0) {
                    debugLog("Timed out waiting for React table to update.");
                } else if (rows && rows.length > 1) {
                    if (window.showToast) window.showToast(`Transcribing page ${batchPageCounter}...`, "success", 1500);
                    extractCurrentPageData(rows);
                }
            }

            await downloadCurrentImage();

            const nextBtn = document.querySelector('button[aria-label="Next image"], .pagination.right button.page, .nextButton, button[title="Next image"]');
            const isNextDisabled = nextBtn && (nextBtn.disabled || nextBtn.classList.contains('disabled') || nextBtn.getAttribute('aria-disabled') === 'true' || nextBtn.hasAttribute('disabled'));

            if (DEBUG_MODE) {
                console.log(`[MGS DEBUG] nextBtn found: ${!!nextBtn} | disabled: ${!!isNextDisabled} | selector matched: ${nextBtn ? nextBtn.outerHTML.slice(0, 150) : 'n/a'}`);
            }

            if (nextBtn && !isNextDisabled) {
                const prevUrl = window.location.href;
                if (window.showToast) window.showToast("Advancing to next page...", "success", 1000);

                if (typeof unsafeWindow !== 'undefined') {
                    unsafeWindow.__mgs_pids = [];
                }

                nextBtn.click();

                let navRetries = 150;
                while (window.location.href === prevUrl && navRetries > 0) {
                    await sleep(100);
                    navRetries--;
                }

                if (DEBUG_MODE) {
                    console.log(`[MGS DEBUG] navigation ${navRetries === 0 ? 'TIMED OUT' : 'succeeded'} | retries left: ${navRetries} | new url: ${window.location.href}`);
                }

                if (navRetries === 0) {
                    if (window.showToast) window.showToast("Navigation timed out. Stopping.", "error");
                    stopBatch();
                    break;
                }

                batchPageCounter++;
            } else {
                if (DEBUG_MODE) {
                    console.log(`[MGS DEBUG] Stopping: nextBtn was ${!nextBtn ? 'not found' : 'found but disabled'}.`);
                }
                stopBatch();
                break;
            }
        }
    }

    function startBatch() {
        isAutoExtracting = true;
        accumulatedCsvData = [];
        seenPids.clear();
        headerParsed = false;
        batchPageCounter = 1;
        lastPageSignature = "INITIAL_STATE_NOT_SET";

        if (window._startBtn) window._startBtn.style.display = 'none';
        if (window._stopBtn) window._stopBtn.style.display = 'block';
        if (window._statusLight) window._statusLight.classList.add('running');
        if (window.showToast) window.showToast("Starting Batch Extraction...", "success");
        runExtractionLoop();
    }

    function stopBatch() {
        if (!isAutoExtracting) return;
        isAutoExtracting = false;
        if (window._startBtn) window._startBtn.style.display = 'block';
        if (window._stopBtn) window._stopBtn.style.display = 'none';
        if (window._statusLight) window._statusLight.classList.remove('running');
        debugLog("Batch stopped.");
        downloadFinalCsv();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            initUI();
            if (shouldAutoStart && !isAutoExtracting) startBatch();
        });
    } else {
        initUI();
        if (shouldAutoStart && !isAutoExtracting) startBatch();
    }

})();