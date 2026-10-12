import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

/* Higgsfield API key panel (ComfyUI Settings → Higgsfield).
 * The key is sent once to the local ComfyUI server (PUT /higgsfield/key) and is never
 * returned to the browser or stored in browser storage / workflow JSON.
 */

const ROUTE = "/higgsfield/key";

async function call(path, options = {}) {
    let response;
    try {
        response = await api.fetchApi(path, {
            ...options,
            headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        });
    } catch (error) {
        throw new Error("Can't reach the ComfyUI server. Is ComfyUI still running?");
    }
    let body = null;
    try {
        body = await response.json();
    } catch {
        /* empty or non-JSON body */
    }
    if (!response.ok) {
        if (body?.error) throw new Error(body.error);
        if (response.status === 404) throw new Error("This feature isn't available — restart ComfyUI to load the latest Higgsfield nodes.");
        throw new Error(`The ComfyUI server returned an error (HTTP ${response.status}).`);
    }
    if (body === null) throw new Error("The ComfyUI server sent an unreadable reply.");
    return body;
}

function button(label, onClick, primary = false) {
    const el = document.createElement("button");
    el.type = "button";
    el.textContent = label;
    el.style.cssText =
        "padding:6px 12px;border-radius:6px;cursor:pointer;font:inherit;" +
        (primary
            ? "border:0;background:var(--p-primary-color,#4f46e5);color:var(--p-primary-contrast-color,#fff);"
            : "border:1px solid var(--border-color,#555);background:transparent;color:inherit;");
    el.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        onClick(el);
    });
    return el;
}

/* ---------------------------------------------------------------- API key panel */

const KEY_PANEL_CSS = `
/* Use the full settings width: stack this one row (no left label column, no right alignment). */
.setting-item:has(.hf-card) .form-label{display:none}
.setting-item:has(.hf-card) .form-input{flex:1;justify-content:stretch;min-width:0}
.setting-item:has(.hf-card) .form-input>div{width:100%}
.hf-card{box-sizing:border-box;width:100%;max-width:none;display:flex;flex-direction:column;gap:12px;
  padding:14px 16px;border:1px solid var(--border-color,#3a3a3a);border-radius:10px;
  background:var(--comfy-menu-secondary-bg,rgba(255,255,255,.02));color:var(--fg-color,#ddd);font-size:13px;line-height:1.45}
.hf-head{display:flex;align-items:center;gap:12px;min-height:30px}
.hf-status{display:flex;align-items:center;gap:10px;min-width:0;flex:1}
.hf-pill{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 10px;border-radius:999px;
  font-size:11.5px;font-weight:600;letter-spacing:.2px;white-space:nowrap}
.hf-pill::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.hf-pill.ok{color:#4ade80;background:rgba(74,222,128,.12)}
.hf-pill.off{color:#fbbf24;background:rgba(251,191,36,.12)}
.hf-pill.wait{color:#9ca3af;background:rgba(156,163,175,.14)}
.hf-meta{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;opacity:.72}
.hf-meta code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
.hf-actions{display:flex;gap:6px;flex-shrink:0}
.hf-btn{height:30px;padding:0 12px;border-radius:6px;border:1px solid var(--border-color,#4a4a4a);background:transparent;
  color:inherit;font:inherit;font-size:12.5px;font-weight:500;cursor:pointer;white-space:nowrap;transition:background .12s,border-color .12s,color .12s}
.hf-btn:hover:not(:disabled){background:rgba(255,255,255,.06)}
.hf-btn:disabled{opacity:.45;cursor:not-allowed}
.hf-btn.primary{border-color:transparent;background:var(--p-primary-color,#3b82f6);color:var(--p-primary-contrast-color,#fff)}
.hf-btn.primary:hover:not(:disabled){filter:brightness(1.1);background:var(--p-primary-color,#3b82f6)}
.hf-btn.danger:hover:not(:disabled){color:#f87171;border-color:rgba(248,113,113,.55);background:rgba(248,113,113,.08)}
.hf-field{display:flex;align-items:center;gap:8px}
.hf-input-wrap{position:relative;flex:1;min-width:0}
.hf-input{box-sizing:border-box;width:100%;height:32px;padding:0 60px 0 10px;border-radius:6px;border:1px solid var(--border-color,#4a4a4a);
  background:var(--comfy-input-bg,#1e1e1e);color:inherit;font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace}
.hf-input:focus{outline:none;border-color:var(--p-primary-color,#3b82f6);box-shadow:0 0 0 2px rgba(59,130,246,.25)}
.hf-reveal{position:absolute;top:50%;right:4px;transform:translateY(-50%);height:24px;padding:0 8px;border:0;border-radius:4px;
  background:transparent;color:inherit;opacity:.65;font:inherit;font-size:11.5px;cursor:pointer}
.hf-reveal:hover{opacity:1;background:rgba(255,255,255,.06)}
.hf-msg{font-size:12px}
.hf-msg:empty{display:none}
.hf-msg.ok{color:#4ade80}.hf-msg.err{color:#f87171}.hf-msg.info{opacity:.75}
.hf-rule{height:1px;background:var(--border-color,#3a3a3a);opacity:.7}
.hf-help{display:flex;align-items:baseline;justify-content:space-between;gap:16px;font-size:12px}
.hf-help span{opacity:.6}
.hf-help a{flex-shrink:0;white-space:nowrap}
.hf-toggle{display:inline-flex;align-items:center;gap:8px;cursor:pointer;user-select:none;font-size:12.5px}
.hf-toggle input{appearance:none;position:relative;width:30px;height:17px;margin:0;border-radius:999px;cursor:pointer;
  background:var(--border-color,#4a4a4a);transition:background .15s}
.hf-toggle input::after{content:"";position:absolute;top:2px;left:2px;width:13px;height:13px;border-radius:50%;
  background:#fff;transition:transform .15s}
.hf-toggle input:checked{background:var(--p-primary-color,#3b82f6)}
.hf-toggle input:checked::after{transform:translateX(13px)}
.hf-pill.warn{color:#60a5fa;background:rgba(96,165,250,.13)}
.hf-list{margin:4px 0 0;padding-left:18px;font-size:12px;opacity:.85}
.hf-help a{color:var(--p-primary-color,#60a5fa);text-decoration:none}
.hf-help a:hover{text-decoration:underline}
`;

function injectCss() {
    if (document.getElementById("hf-key-panel-css")) return;
    const style = document.createElement("style");
    style.id = "hf-key-panel-css";
    style.textContent = KEY_PANEL_CSS;
    document.head.append(style);
}

function mk(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined) e.textContent = text;
    return e;
}

function action(label, onClick, variant = "") {
    const b = mk("button", `hf-btn ${variant}`.trim(), label);
    b.type = "button";
    b.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        onClick(b);
    });
    return b;
}

function shortSource(source = "") {
    if (/HF_API_KEY_ID/.test(source)) return "env: HF_API_KEY_ID";
    if (/HF_KEY/.test(source)) return "env: HF_KEY";
    return "saved locally";
}

function keySetting() {
    injectCss();
    const card = mk("div", "hf-card");

    // Header: status badge + key details on the left, actions on the right.
    const head = mk("div", "hf-head");
    const statusBox = mk("div", "hf-status");
    const pill = mk("span", "hf-pill wait", "Checking");
    const meta = mk("span", "hf-meta");
    statusBox.append(pill, meta);
    const actions = mk("div", "hf-actions");
    head.append(statusBox, actions);

    // Key entry row.
    const field = mk("div", "hf-field");
    const wrap = mk("div", "hf-input-wrap");
    const input = mk("input", "hf-input");
    input.type = "password";
    input.autocomplete = "new-password";
    input.spellcheck = false;
    input.placeholder = "KEY_ID:KEY_SECRET";
    input.setAttribute("aria-label", "Higgsfield API key");
    const reveal = mk("button", "hf-reveal", "Show");
    reveal.type = "button";
    reveal.addEventListener("click", (e) => {
        e.preventDefault();
        input.type = input.type === "password" ? "text" : "password";
        reveal.textContent = input.type === "password" ? "Show" : "Hide";
    });
    wrap.append(input, reveal);
    const saveBtn = action("Save key", () => save(), "primary");
    const cancelBtn = action("Cancel", () => setEditing(false));
    field.append(wrap, saveBtn, cancelBtn);

    const msg = mk("div", "hf-msg");
    msg.setAttribute("aria-live", "polite");

    const help = mk("div", "hf-help");
    const link = mk("a", "", "Get an API key ↗");
    link.href = "https://console.higgsfield.ai";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const storageNote = mk("span", "", "Stored only on this computer. Never saved in workflows.");
    help.append(storageNote, link);

    card.append(head, field, msg, mk("div", "hf-rule"), help);

    let state = { configured: false };
    let editing = false;

    const say = (text, kind = "info") => {
        msg.className = `hf-msg ${kind}`;
        msg.textContent = text || "";
    };

    function setEditing(on) {
        editing = on;
        input.value = "";
        input.type = "password";
        reveal.textContent = "Show";
        render();
        if (on) setTimeout(() => input.focus(), 0);
    }

    function render() {
        const fromEnv = !!state.managed_by_env || (state.configured && /environment/i.test(state.source || ""));
        if (state.storage) storageNote.textContent = `${state.storage} Never saved in workflows.`;
        pill.className = `hf-pill ${state.configured ? "ok" : "off"}`;
        pill.textContent = state.configured ? "Connected" : "Not connected";
        meta.replaceChildren();
        if (state.configured) {
            const code = mk("code", "", state.key_id);
            meta.append("Key ", code, ` · ${shortSource(state.source)}`);
            meta.title = `Key ${state.key_id} · ${state.source}`;
        } else {
            meta.textContent = state.error || "Add your key to use the Higgsfield nodes.";
            meta.title = meta.textContent;
        }

        actions.replaceChildren();
        if (fromEnv) {
            // Cloud/shared servers: the key is managed by the server environment and can't be changed here.
            if (state.configured) actions.append(action("Test", (b) => test(b)));
            storageNote.textContent = "Managed by the server's HF_KEY environment variable (recommended for cloud and shared ComfyUI).";
        } else if (state.configured && !editing) {
            actions.append(action("Test", (b) => test(b)), action("Replace", () => setEditing(true)),
                action("Remove", () => removeKey(), "danger"));
        }
        const showField = !fromEnv && (!state.configured || editing);
        field.style.display = showField ? "flex" : "none";
        cancelBtn.style.display = state.configured && editing ? "" : "none";
    }

    async function refresh() {
        try {
            state = await call(ROUTE);
        } catch (error) {
            state = { configured: false, error: `Could not read key status: ${error.message}` };
        }
        render();
    }

    async function save() {
        if (!input.value.trim()) {
            say("Paste the key in KEY_ID:KEY_SECRET format.", "err");
            input.focus();
            return;
        }
        saveBtn.disabled = true;
        say("Checking the key with Higgsfield…");
        try {
            const result = await call(ROUTE, { method: "PUT", body: JSON.stringify({ key: input.value }) });
            state = result;
            editing = false;
            input.value = "";
            render();
            say(result.warning || "Key saved and verified.", result.warning ? "info" : "ok");
        } catch (error) {
            say(error.message, "err");
        } finally {
            saveBtn.disabled = false;
        }
    }

    async function test(btn) {
        btn.disabled = true;
        say("Testing the key…");
        try {
            const result = await call(`${ROUTE}/test`, { method: "POST", body: "{}" });
            say(`Key works. Sample estimate (Soul 2): ${result.sample_estimate}.`, "ok");
        } catch (error) {
            say(error.message, "err");
        } finally {
            btn.disabled = false;
        }
    }

    async function removeKey() {
        if (!confirm("Remove the saved Higgsfield API key from this computer?")) return;
        try {
            state = await call(ROUTE, { method: "DELETE" });
            render();
            say("Key removed.", "info");
        } catch (error) {
            say(error.message, "err");
        }
    }

    input.addEventListener("keydown", (event) => {
        event.stopPropagation(); // keep ComfyUI shortcuts from firing while typing
        if (event.key === "Enter") save();
        if (event.key === "Escape" && state.configured) setEditing(false);
    });

    render();
    refresh();
    return card;
}

/* ---------------------------------------------------------------- Model catalog panel */

function ago(iso) {
    if (!iso) return "never";
    const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
    if (!isFinite(seconds)) return "unknown";
    if (seconds < 90) return "just now";
    const minutes = seconds / 60;
    if (minutes < 90) return `${Math.round(minutes)} min ago`;
    const hours = minutes / 60;
    if (hours < 36) return `${Math.round(hours)} h ago`;
    return `${Math.round(hours / 24)} days ago`;
}

function catalogSetting() {
    injectCss();
    const card = mk("div", "hf-card");
    const head = mk("div", "hf-head");
    const statusBox = mk("div", "hf-status");
    const pill = mk("span", "hf-pill wait", "Checking");
    const meta = mk("span", "hf-meta");
    statusBox.append(pill, meta);
    const actions = mk("div", "hf-actions");
    const checkBtn = action("Check for updates", () => update(), "primary");
    actions.append(checkBtn);
    head.append(statusBox, actions);

    const optionRow = mk("div", "hf-field");
    const toggle = mk("label", "hf-toggle");
    const box = document.createElement("input");
    box.type = "checkbox";
    toggle.append(box, mk("span", "", "Check for new models automatically (once a day, when ComfyUI starts)"));
    optionRow.append(toggle);

    const msg = mk("div", "hf-msg");
    msg.setAttribute("aria-live", "polite");
    const details = mk("div");
    const restartRow = mk("div", "hf-field");
    const restartBtn = action("Restart ComfyUI", () => window.electronAPI.restartApp(), "primary");
    restartRow.append(restartBtn);

    const help = mk("div", "hf-help");
    const link = mk("a", "", "Higgsfield models ↗");
    link.href = "https://open.higgsfield.ai/explore";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    help.append(mk("span", "", "Reads Higgsfield's public model pages and docs. New models appear after a restart; prices update instantly."), link);
    // Pack version row: is a newer release on the Comfy Registry? (installed via ComfyUI Manager / comfy-cli)
    const versionHead = mk("div", "hf-head");
    const versionStatus = mk("div", "hf-status");
    const versionPill = mk("span", "hf-pill wait", "Checking");
    const versionMeta = mk("span", "hf-meta", "Higgsfield nodes");
    versionStatus.append(versionPill, versionMeta);
    const versionActions = mk("div", "hf-actions");
    versionHead.append(versionStatus, versionActions);
    const versionRule = mk("div", "hf-rule");

    async function loadVersion() {
        let v;
        try {
            v = await call("/higgsfield/version");
        } catch (error) {
            v = { installed: "?", error: error.message };
        }
        versionActions.replaceChildren();
        versionMeta.textContent = `Higgsfield nodes ${v.installed}`;
        if (v.update_available) {
            versionPill.className = "hf-pill warn";
            versionPill.textContent = "Update available";
            versionMeta.textContent = `Higgsfield nodes ${v.installed} → ${v.latest} · update in ComfyUI Manager (or: comfy node update ${v.node_id})`;
            const open = action("What's new", () => window.open(v.page, "_blank", "noopener"));
            versionActions.append(open);
        } else if (v.error || v.published === null) {
            versionPill.className = "hf-pill wait";
            versionPill.textContent = "Version";
            versionMeta.title = v.error || "";
        } else if (v.published === false) {
            versionPill.className = "hf-pill wait";
            versionPill.textContent = "Development";
            versionMeta.textContent = `Higgsfield nodes ${v.installed} (not published on the Comfy Registry yet)`;
        } else {
            versionPill.className = "hf-pill ok";
            versionPill.textContent = "Latest version";
        }
    }

    card.append(versionHead, versionRule, head, optionRow, msg, details, restartRow, mk("div", "hf-rule"), help);
    loadVersion();

    let state = {};
    const say = (text, kind = "info") => {
        msg.className = `hf-msg ${kind}`;
        msg.textContent = text || "";
    };

    function render() {
        if (state.updating) {
            pill.className = "hf-pill wait";
            pill.textContent = "Updating";
        } else if (state.restart_needed) {
            pill.className = "hf-pill warn";
            pill.textContent = "Restart to apply";
        } else {
            pill.className = "hf-pill ok";
            pill.textContent = "Up to date";
        }
        meta.textContent = `${state.families ?? "–"} models · ${state.endpoints ?? "–"} endpoints · checked ${ago(state.last_check || state.synced_at)}`;
        meta.title = `Catalog synced ${state.synced_at || "unknown"}`;
        box.checked = state.auto_update !== false;
        checkBtn.disabled = !!state.updating;
        const canRestart = !!window.electronAPI?.restartApp;
        restartRow.style.display = state.restart_needed && canRestart ? "flex" : "none";
        if (state.restart_needed && !canRestart && !msg.textContent) {
            say("Restart ComfyUI to load the updated models.", "info");
        }
    }

    function showResult(result) {
        details.replaceChildren();
        if (!result) return;
        const lines = [];
        if (result.added_models?.length) lines.push(`New models: ${result.added_models.join(", ")}`);
        if (result.removed_models?.length) lines.push(`Removed: ${result.removed_models.join(", ")}`);
        const modes = result.added_endpoints?.length || 0;
        if (modes) lines.push(`${modes} new mode(s)`);
        if (result.changed_endpoints?.length) lines.push(`${result.changed_endpoints.length} mode(s) with new or changed settings`);
        if (lines.length) {
            const list = mk("ul", "hf-list");
            lines.forEach((line) => list.append(mk("li", "", line)));
            details.append(list);
        }
    }

    async function refresh() {
        try {
            state = await call("/higgsfield/catalog");
        } catch (error) {
            say(`Could not read the model catalog: ${error.message}`, "err");
        }
        render();
        // A background (daily) check may be running: follow it until it finishes, while the panel is open.
        if (state.updating && card.isConnected !== false) {
            clearTimeout(card.__hfPoll);
            card.__hfPoll = setTimeout(async () => {
                if (!document.body.contains(card)) return;
                await refresh();
                if (!state.updating) {
                    showResult(state.last_result);
                    say(state.restart_needed ? "New models were downloaded. Restart ComfyUI to load them."
                        : "Automatic check finished — you have the latest models.", "ok");
                }
            }, 3000);
        }
    }

    async function update() {
        checkBtn.disabled = true;
        state.updating = true;
        render();
        say("Checking Higgsfield for new models and settings… (about a minute)");
        try {
            const response = await call("/higgsfield/catalog/update", { method: "POST", body: "{}" });
            state = response;
            const result = response.result || {};
            showResult(result);
            if (result.changed) {
                say(state.restart_needed ? "Model list updated. Restart ComfyUI to load the changes." : "Model list updated.", "ok");
            } else {
                say("You already have the latest models. Prices were refreshed.", "ok");
            }
        } catch (error) {
            say(error.message, "err");
        } finally {
            state.updating = false;
            render();
        }
    }

    box.addEventListener("change", async () => {
        try {
            state = await call("/higgsfield/catalog/settings", {
                method: "PUT",
                body: JSON.stringify({ auto_update: box.checked }),
            });
            say(box.checked ? "Daily check turned on." : "Daily check turned off.", "info");
        } catch (error) {
            say(error.message, "err");
        }
        render();
    });

    render();
    refresh();
    return card;
}

/* ---------------------------------------------------------------- ID pickers
 * Right-click a Higgsfield node → "Pick …" lists styles / presets / Soul IDs from Higgsfield
 * (fetched by the local server with the saved key) and writes the chosen id into the widget.
 */

let pickerMap = null;
async function loadPickerMap() {
    if (!pickerMap) {
        try {
            pickerMap = await call("/higgsfield/pickers");
        } catch {
            pickerMap = {};
        }
    }
    return pickerMap;
}

function pickerFieldsFor(node) {
    const spec = pickerMap?.[node.comfyClass || node.type];
    if (!spec) return {};
    if (!spec.multi) return Object.values(spec.modes)[0] || {};
    const mode = node.widgets?.find((w) => w.name === "mode")?.value;
    return spec.modes[mode] || {};
}

function el(tag, css, text) {
    const e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text !== undefined) e.textContent = text;
    return e;
}

function openPicker(node, widgetName, field) {
    const overlay = el("div", "position:fixed;inset:0;z-index:10000;display:grid;place-items:center;background:rgba(0,0,0,.6);");
    const panel = el("div",
        "width:min(720px,92vw);max-height:80vh;display:flex;flex-direction:column;gap:10px;padding:16px;border-radius:12px;" +
        "background:var(--comfy-menu-bg,#202020);color:var(--fg-color,#eee);border:1px solid var(--border-color,#444);");
    const header = el("div", "display:flex;gap:8px;align-items:center;");
    const title = el("strong", "flex:1;", `Pick ${field.label}`);
    const close = button("Close", () => overlay.remove());
    header.append(title, close);
    const search = el("input",
        "padding:6px 8px;border-radius:6px;border:1px solid var(--border-color,#555);background:var(--comfy-input-bg,#1e1e1e);color:inherit;font:inherit;");
    search.placeholder = "Search…";
    search.addEventListener("keydown", (e) => e.stopPropagation());
    const status = el("div", "opacity:.7;font-size:.9em;", "Loading…");
    const grid = el("div", "overflow:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;");
    panel.append(header, search, status, grid);
    overlay.append(panel);
    overlay.addEventListener("click", (e) => e.target === overlay && overlay.remove());
    document.body.append(overlay);
    search.focus();

    let items = [];
    const render = () => {
        const needle = search.value.trim().toLowerCase();
        const shown = items.filter((i) => !needle || `${i.name} ${i.group}`.toLowerCase().includes(needle));
        grid.replaceChildren(...shown.map((item) => {
            const card = el("button",
                "display:flex;flex-direction:column;gap:4px;padding:6px;border-radius:8px;cursor:pointer;text-align:left;" +
                "border:1px solid var(--border-color,#444);background:transparent;color:inherit;font:inherit;");
            if (item.preview) {
                const img = el("img", "width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;background:#111;");
                img.loading = "lazy";
                img.src = item.preview;
                card.append(img);
            }
            card.append(el("span", "font-size:.9em;", item.name));
            if (item.group) card.append(el("span", "font-size:.75em;opacity:.6;", item.group));
            card.title = `${item.description || ""}\n${item.id}`.trim();
            card.addEventListener("click", () => {
                const widget = node.widgets?.find((w) => w.name === widgetName);
                if (widget) {
                    widget.value = item.id;
                    widget.callback?.(item.id, app.canvas, node);
                    node.setDirtyCanvas?.(true, true);
                }
                overlay.remove();
            });
            return card;
        }));
        status.textContent = `${shown.length} of ${items.length}`;
    };
    search.addEventListener("input", render);
    call(`/higgsfield/options/${field.kind}`)
        .then((result) => {
            items = result.items || [];
            render();
        })
        .catch((error) => {
            status.textContent = error.message;
        });
}

/* ---------------------------------------------------------------- live price badge
 * Shows the price of the node's current settings in its title bar:
 *   live (with API key): Higgsfield's /estimate for exactly these settings (your account's price)
 *   published:           range from the public pricing page
 * Updates ~0.7 s after a widget or connection changes. Errors never interrupt editing.
 */

const LIVE_PRICE_SETTING = "Higgsfield.LivePrice";

function livePriceEnabled() {
    try {
        return app.extensionManager?.setting?.get(LIVE_PRICE_SETTING) !== false;
    } catch {
        return true;
    }
}

function priceRequestFor(node) {
    const values = {};
    for (const widget of node.widgets || []) {
        if (!widget?.name || widget.name === "control_after_generate") continue;
        const name = widget.name.startsWith("mode.") ? widget.name.slice(5) : widget.name;
        if (typeof widget.value !== "object") values[name] = widget.value;
    }
    const linked = (node.inputs || [])
        .filter((input) => input.link != null)
        .map((input) => (input.name.startsWith("mode.") ? input.name.slice(5) : input.name));
    return { node: node.comfyClass || node.type, values, linked };
}

function schedulePrice(node, delay = 700) {
    if (!livePriceEnabled()) {
        node.__hfPrice = null;
        return;
    }
    clearTimeout(node.__hfPriceTimer);
    node.__hfPriceTimer = setTimeout(async () => {
        const request = priceRequestFor(node);
        const signature = JSON.stringify(request);
        if (signature === node.__hfPriceSignature) return;
        node.__hfPriceSignature = signature;
        const ticket = (node.__hfPriceTicket || 0) + 1;
        node.__hfPriceTicket = ticket;
        try {
            const price = await call("/higgsfield/price", { method: "POST", body: signature });
            if (ticket !== node.__hfPriceTicket) return; // a newer request superseded this one
            node.__hfPrice = price?.text ? price : null;
        } catch {
            node.__hfPrice = null; // the badge is informational: never surface errors while editing
            node.__hfPriceSignature = null;
        }
        node.setDirtyCanvas?.(true, true);
    }, delay);
}

function drawPriceBadge(node, ctx) {
    const price = node.__hfPrice;
    if (!price?.text || node.flags?.collapsed) return;
    const titleHeight = globalThis.LiteGraph?.NODE_TITLE_HEIGHT ?? 30;
    ctx.save();
    ctx.font = "600 11px Inter, Arial, sans-serif";
    const label = price.text;
    const width = ctx.measureText(label).width + 14;
    const height = 18;
    const x = node.size[0] - width - 8;
    const y = -titleHeight + (titleHeight - height) / 2;
    const live = price.kind === "live";
    ctx.fillStyle = live ? "rgba(250, 204, 21, 0.16)" : "rgba(148, 163, 184, 0.16)";
    ctx.strokeStyle = live ? "rgba(250, 204, 21, 0.55)" : "rgba(148, 163, 184, 0.45)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(x, y, width, height, 9) : ctx.rect(x, y, width, height);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = live ? "#fde68a" : "#cbd5e1";
    ctx.textBaseline = "middle";
    ctx.fillText(label, x + 7, y + height / 2 + 0.5);
    ctx.restore();
}

/* ---------------------------------------------------------------- restore growing reference slots
 * ComfyUI restores links before a growing input list (e.g. "mode.image_urls.image_2") inside a mode
 * dropdown has created its later slots, so links to slot 2+ were dropped when a workflow loaded.
 * We remember those links from the saved workflow and reconnect them in order once the graph is ready
 * (each connection makes the next slot appear).
 */

const AUTOGROW_INPUT = /^(?:mode\.)?\w+\.(?:image|video|audio)_(\d+)$/;
let pendingAutogrowLinks = [];

function rememberAutogrowLinks(graphData) {
    pendingAutogrowLinks = [];
    try {
        const links = new Map();
        for (const link of graphData?.links || []) {
            if (Array.isArray(link)) links.set(link[0], { origin: link[1], slot: link[2] });
            else if (link) links.set(link.id, { origin: link.origin_id, slot: link.origin_slot });
        }
        for (const node of graphData?.nodes || []) {
            if (!String(node.type || "").startsWith("Higgsfield_")) continue;
            for (const input of node.inputs || []) {
                const match = AUTOGROW_INPUT.exec(input?.name || "");
                if (!match || input.link == null || !links.has(input.link)) continue;
                pendingAutogrowLinks.push({ target: node.id, input: input.name, index: Number(match[1]), ...links.get(input.link) });
            }
        }
        pendingAutogrowLinks.sort((a, b) => a.target - b.target || a.input.localeCompare(b.input, undefined, { numeric: true }));
    } catch (error) {
        console.warn("[Higgsfield] could not read reference links from the workflow", error);
        pendingAutogrowLinks = [];
    }
}

async function restoreAutogrowLinks() {
    const pending = pendingAutogrowLinks;
    pendingAutogrowLinks = [];
    const failed = [];
    for (const item of pending) {
        try {
            const target = app.graph.getNodeById(item.target);
            const origin = app.graph.getNodeById(item.origin);
            if (!target || !origin) continue;
            let slot = -1;
            for (let attempt = 0; attempt < 20; attempt++) { // the slot appears once the previous one is linked
                slot = target.inputs.findIndex((input) => input.name === item.input);
                if (slot >= 0) break;
                await new Promise((resolve) => setTimeout(resolve, 50));
            }
            if (slot < 0) {
                failed.push(item.input);
                continue;
            }
            if (target.inputs[slot].link == null) origin.connect(item.slot, target, slot);
        } catch (error) {
            failed.push(item.input);
            console.warn("[Higgsfield] could not restore link", item, error);
        }
    }
    if (failed.length) {
        console.warn(`[Higgsfield] ${failed.length} reference connection(s) could not be restored: ${failed.join(", ")}`);
    }
    app.graph.setDirtyCanvas?.(true, true);
}

app.registerExtension({
    name: "Higgsfield.WorkflowRestore",
    async beforeConfigureGraph(graphData) {
        rememberAutogrowLinks(graphData);
    },
    async afterConfigureGraph() {
        if (pendingAutogrowLinks.length) await restoreAutogrowLinks();
    },
});

app.registerExtension({
    name: "Higgsfield.Pickers",
    async setup() {
        await loadPickerMap();
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!nodeData?.name?.startsWith("Higgsfield_")) return;
        const original = nodeType.prototype.getExtraMenuOptions;
        nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
            const result = original?.apply(this, arguments);
            const fields = pickerFieldsFor(this);
            const entries = Object.entries(fields).map(([widgetName, field]) => ({
                content: `Pick ${field.label}…`,
                callback: () => openPicker(this, widgetName, field),
            }));
            if (entries.length) options.unshift(...entries, null);
            if (this.__hfPrice?.detail) {
                options.unshift({ content: `Price: ${this.__hfPrice.text}`, disabled: true, title: this.__hfPrice.detail });
            }
            return result;
        };

        // Live price badge (Soul ID nodes have no price).
        if (nodeData.name.startsWith("Higgsfield_soul_id")) return;
        const drawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function (ctx) {
            const result = drawForeground?.apply(this, arguments);
            try {
                drawPriceBadge(this, ctx);
            } catch {
                /* drawing must never break the canvas */
            }
            return result;
        };
        const widgetChanged = nodeType.prototype.onWidgetChanged;
        nodeType.prototype.onWidgetChanged = function () {
            const result = widgetChanged?.apply(this, arguments);
            schedulePrice(this);
            return result;
        };
        const connectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = connectionsChange?.apply(this, arguments);
            schedulePrice(this);
            return result;
        };
    },
    nodeCreated(node) {
        if ((node.comfyClass || "").startsWith("Higgsfield_") && !node.comfyClass.startsWith("Higgsfield_soul_id")) {
            schedulePrice(node, 300);
        }
    },
    loadedGraphNode(node) {
        if ((node.comfyClass || "").startsWith("Higgsfield_") && !node.comfyClass.startsWith("Higgsfield_soul_id")) {
            schedulePrice(node, 500);
        }
    },
});

app.registerExtension({
    name: "Higgsfield.Nodes",
    settings: [
        {
            id: "Higgsfield.ApiKey",
            name: "API key",
            category: ["Higgsfield", "API key", "Key"],
            type: keySetting,
            defaultValue: "",
        },
        {
            id: LIVE_PRICE_SETTING,
            name: "Show live price on Higgsfield nodes",
            tooltip: "With an API key, each node shows the exact price of its current settings (from Higgsfield's "
                + "free estimate endpoint, ~1 request per change). Without a key, the published price range.",
            category: ["Higgsfield", "Price", "Live price"],
            type: "boolean",
            defaultValue: true,
            onChange: () => {
                for (const node of app.graph?._nodes || []) {
                    if ((node.comfyClass || "").startsWith("Higgsfield_")) {
                        node.__hfPriceSignature = null;
                        schedulePrice(node, 100);
                    }
                }
            },
        },
        {
            id: "Higgsfield.Catalog",
            name: "Updates",
            category: ["Higgsfield", "Updates", "Catalog"],
            type: catalogSetting,
            defaultValue: "",
        },
    ],
});
