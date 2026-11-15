// Clicking the toolbar button saves the current page to the local
// `satchel serve` listener. The page's own HTML is sent, so what gets saved
// is what you are reading, including pages behind your own login.
const api = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);

function show(message, ok) {
  const el = $("status");
  el.textContent = message;
  el.className = ok === undefined ? "" : ok ? "ok" : "bad";
}

function showSetup(message, token = "", port = 8765) {
  show(message);
  $("token").value = token;
  $("port").value = port;
  $("setup").hidden = false;
  $("actions").hidden = true;
  $("change").hidden = true;
  $("token").focus();
}

function showActions() {
  $("setup").hidden = true;
  $("actions").hidden = false;
  $("change").hidden = false;
}

async function settings() {
  return api.storage.local.get({ token: "", port: 8765 });
}

async function pageHtml(tabId) {
  try {
    const [frame] = await api.scripting.executeScript({
      target: { tabId },
      func: () => document.documentElement.outerHTML,
    });
    return frame?.result || "";
  } catch {
    return ""; // a browser or store page scripts can't read: satchel fetches the URL instead
  }
}

async function save() {
  const { token, port } = await settings();
  if (!token) {
    showSetup("Run `satchel serve --keep-token` and paste the token it prints.");
    return;
  }
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url || !/^https?:/.test(tab.url)) {
    show("Only web pages (http or https) can be saved.", false);
    showActions();
    return;
  }
  show("Saving…");
  showActions();
  $("save").disabled = true;
  try {
    const html = await pageHtml(tab.id);
    const resp = await fetch(`http://127.0.0.1:${port}/add`, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ token, url: tab.url, html }),
    });
    const body = await resp.json();
    if (resp.status === 403) {
      showSetup("That token was refused. Paste the one `satchel serve` printed.", "", port);
      return;
    }
    show(body.message, body.ok);
  } catch {
    show(`Could not reach satchel on port ${port}. Is \`satchel serve\` running?`, false);
  } finally {
    $("save").disabled = false;
  }
}

$("setup").addEventListener("submit", async (event) => {
  event.preventDefault();
  const port = Number($("port").value);
  await api.storage.local.set({ token: $("token").value.trim(), port });
  save();
});

$("save").addEventListener("click", save);

$("open").addEventListener("click", async () => {
  const { token, port } = await settings();
  api.tabs.create({ url: `http://127.0.0.1:${port}/?token=${encodeURIComponent(token)}` });
  window.close();
});

$("settings").addEventListener("click", async () => {
  const { token, port } = await settings();
  showSetup("Paste the token `satchel serve` printed.", token, port);
});

save();
