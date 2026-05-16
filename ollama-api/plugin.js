/**
 * Ollama Proxy Plugin for Unsloth Studio
 * Injected via <script> tag into the Studio WebUI.
 * Manager API: http://localhost:11435
 */
(function () {
  'use strict';

  const MANAGER = 'http://localhost:11435';
  const PLUGIN_ATTR = 'data-ollama-plugin';
  let pollTimer = null;
  let formPopulated = false;

  /* ------------------------------------------------------------------ */
  /* Utilities                                                            */
  /* ------------------------------------------------------------------ */

  function isDark() {
    return document.documentElement.classList.contains('dark');
  }

  async function apiFetch(path, opts) {
    const res = await fetch(MANAGER + path, opts);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /* ------------------------------------------------------------------ */
  /* Sidebar button injection                                             */
  /* ------------------------------------------------------------------ */

  function injectButton() {
    if (document.querySelector(`[${PLUGIN_ATTR}]`)) return;

    const menu = document.querySelector('[data-tour="navbar"] [data-sidebar="menu"]');
    if (!menu) return;

    const li = document.createElement('li');
    li.setAttribute('data-sidebar', 'menu-item');
    li.setAttribute(PLUGIN_ATTR, '1');
    li.innerHTML = `
      <button data-sidebar="menu-button"
              class="flex items-center gap-2 overflow-hidden rounded-md p-2 text-left text-sm outline-none ring-sidebar-ring transition-[width,height,padding] hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 active:bg-sidebar-accent active:text-sidebar-accent-foreground disabled:pointer-events-none disabled:opacity-50 group-has-[[data-sidebar=menu-action]]/menu-item:pr-8 aria-disabled:pointer-events-none aria-disabled:opacity-50 data-[active=true]:bg-sidebar-accent data-[active=true]:font-medium data-[active=true]:text-sidebar-accent-foreground data-[state=open]:hover:bg-sidebar-accent data-[state=open]:hover:text-sidebar-accent-foreground group-data-[collapsible=icon]:!size-8 group-data-[collapsible=icon]:!p-2 [&>span:last-child]:truncate [&>svg]:size-4 [&>svg]:shrink-0 w-full"
              type="button">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/>
          <line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>
        </svg>
        <span>Ollama Proxy</span>
      </button>`;

    li.querySelector('button').addEventListener('click', openModal);
    menu.appendChild(li);
  }

  /* ------------------------------------------------------------------ */
  /* Modal                                                                */
  /* ------------------------------------------------------------------ */

  function buildModal() {
    const dark = isDark();
    const overlay = document.createElement('div');
    overlay.id = 'ollama-proxy-modal';
    overlay.style.cssText =
      'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5)';

    overlay.innerHTML = `
      <div style="background:${dark ? '#1e1e2e' : '#ffffff'};color:${dark ? '#cdd6f4' : '#1e1e2e'};
                  border-radius:12px;padding:24px;width:420px;max-width:95vw;box-shadow:0 8px 32px rgba(0,0,0,0.4);
                  font-family:system-ui,sans-serif;font-size:14px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <strong style="font-size:16px">Ollama Proxy</strong>
          <button id="op-close" style="background:none;border:none;cursor:pointer;font-size:18px;color:inherit">✕</button>
        </div>

        <!-- Status row -->
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px">
          <span id="op-dot" style="width:10px;height:10px;border-radius:50%;background:#6c757d;flex-shrink:0"></span>
          <span id="op-status-text" style="flex:1">Checking…</span>
          <button id="op-toggle" style="padding:4px 12px;border-radius:6px;border:1px solid currentColor;background:none;cursor:pointer;color:inherit">…</button>
        </div>

        <hr style="border:none;border-top:1px solid ${dark ? '#313244' : '#e0e0e0'};margin-bottom:16px"/>

        <!-- Config form -->
        <div style="display:grid;gap:10px">
          <label style="display:grid;gap:4px">Base URL
            <input id="op-base-url" type="text" style="${inputStyle(dark)}" placeholder="http://localhost:8888"/>
          </label>
          <label style="display:grid;gap:4px">API Key
            <input id="op-api-key" type="password" style="${inputStyle(dark)}" placeholder="(leave blank if none)"/>
          </label>
          <label style="display:grid;gap:4px">Context Length
            <input id="op-ctx-len" type="number" style="${inputStyle(dark)}" placeholder="32768"/>
          </label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
            <label style="display:grid;gap:4px">Proxy Host
              <input id="op-host" type="text" style="${inputStyle(dark)}" placeholder="0.0.0.0"/>
            </label>
            <label style="display:grid;gap:4px">Proxy Port
              <input id="op-port" type="number" style="${inputStyle(dark)}" placeholder="11434"/>
            </label>
          </div>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input id="op-open-browser" type="checkbox" style="width:15px;height:15px;cursor:pointer"/>
            Open browser on startup
          </label>
        </div>

        <!-- Footer -->
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:16px;gap:8px">
          <span style="font-size:12px;opacity:0.6">Proxy restarts automatically on save.</span>
          <button id="op-save" style="padding:6px 18px;border-radius:6px;background:#7c3aed;color:#fff;border:none;cursor:pointer;font-weight:600">Save</button>
        </div>
      </div>`;

    return overlay;
  }

  function inputStyle(dark) {
    return `padding:6px 10px;border-radius:6px;border:1px solid ${dark ? '#45475a' : '#d0d0d0'};
            background:${dark ? '#313244' : '#f8f8f8'};color:inherit;font-size:13px;width:100%;box-sizing:border-box`;
  }

  function openModal() {
    if (document.getElementById('ollama-proxy-modal')) return;
    const modal = buildModal();
    document.body.appendChild(modal);

    modal.querySelector('#op-close').addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
    modal.querySelector('#op-toggle').addEventListener('click', onToggle);
    modal.querySelector('#op-save').addEventListener('click', onSave);

    formPopulated = false;
    refreshStatus();
    pollTimer = setInterval(refreshStatus, 2000);
  }

  function closeModal() {
    clearInterval(pollTimer);
    pollTimer = null;
    const m = document.getElementById('ollama-proxy-modal');
    if (m) m.remove();
  }

  async function refreshStatus() {
    const modal = document.getElementById('ollama-proxy-modal');
    if (!modal) return;
    try {
      const data = await apiFetch('/status');
      updateStatusUI(data.running);
      if (!formPopulated) {
        populateForm(data.settings || {});
        formPopulated = true;
      }
    } catch {
      updateStatusUI(null);
    }
  }

  function updateStatusUI(running) {
    const modal = document.getElementById('ollama-proxy-modal');
    if (!modal) return;
    const dot = modal.querySelector('#op-dot');
    const txt = modal.querySelector('#op-status-text');
    const btn = modal.querySelector('#op-toggle');
    if (running === null) {
      dot.style.background = '#6c757d';
      txt.textContent = 'Manager unreachable';
      btn.textContent = '—';
    } else if (running) {
      dot.style.background = '#22c55e';
      txt.textContent = 'Running';
      btn.textContent = 'Stop';
    } else {
      dot.style.background = '#ef4444';
      txt.textContent = 'Stopped';
      btn.textContent = 'Start';
    }
  }

  function populateForm(settings) {
    const modal = document.getElementById('ollama-proxy-modal');
    if (!modal) return;
    const set = (id, val) => { const el = modal.querySelector(id); if (el && val !== undefined) el.value = val; };
    set('#op-base-url', settings.unsloth_base_url);
    set('#op-api-key', settings.unsloth_api_key);
    set('#op-ctx-len', settings.model_context_length);
    set('#op-host', settings.proxy_host);
    set('#op-port', settings.proxy_port);
    const cb = modal.querySelector('#op-open-browser');
    if (cb && settings.open_browser_on_startup !== undefined) cb.checked = !!settings.open_browser_on_startup;
  }

  async function onToggle() {
    const btn = document.getElementById('ollama-proxy-modal')?.querySelector('#op-toggle');
    if (!btn) return;
    const action = btn.textContent.trim() === 'Stop' ? '/stop' : '/start';
    try {
      await apiFetch(action, { method: 'POST' });
      await refreshStatus();
    } catch (e) {
      console.error('[OllamaPlugin] toggle error', e);
    }
  }

  async function onSave() {
    const modal = document.getElementById('ollama-proxy-modal');
    if (!modal) return;
    const get = (id) => modal.querySelector(id)?.value;
    const payload = {
      unsloth_base_url: get('#op-base-url'),
      unsloth_api_key: get('#op-api-key'),
      model_context_length: parseInt(get('#op-ctx-len'), 10) || 32768,
      proxy_host: get('#op-host'),
      proxy_port: parseInt(get('#op-port'), 10) || 11434,
      open_browser_on_startup: !!(modal.querySelector('#op-open-browser')?.checked),
    };
    try {
      await apiFetch('/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const saveBtn = modal.querySelector('#op-save');
      saveBtn.textContent = 'Restarting…';
      setTimeout(() => { saveBtn.textContent = 'Save'; refreshStatus(); }, 2000);
    } catch (e) {
      console.error('[OllamaPlugin] save error', e);
    }
  }

  /* ------------------------------------------------------------------ */
  /* Bootstrap                                                            */
  /* ------------------------------------------------------------------ */

  function tryInject() {
    injectButton();
  }

  const observer = new MutationObserver(tryInject);
  observer.observe(document.body, { childList: true, subtree: true });
  tryInject();
})();
