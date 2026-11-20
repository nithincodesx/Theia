(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);

  const statusText = $("#status-text");
  const dot = $("#dot");
  const voiceList = $("#voice-list");
  const btnMic = $("#btn-mic");
  const btnAgent = $("#btn-agent");
  const btnTest = $("#btn-test");
  const micTxt = $("#mic-txt");
  const agentTxt = $("#agent-txt");
  const activity = $("#activity");
  const activityEmpty = $("#activity-empty");
  const dossier = $("#dossier");
  const consolePanel = $("#console-panel");
  const header = $("#site-header");

  const STATE_TEXT = {
    idle: "idle",
    listening: "listening…",
    thinking: "thinking…",
    speaking: "speaking…",
  };

  const VOICE_DESC = {
    "en-US-JennyNeural": "Warm sweetheart - clear, friendly",
    "en-US-AriaNeural": "Confident and direct",
    "en-US-EmmaNeural": "Soft romantic - intimate",
    "en-GB-LibbyNeural": "Young tease - playful",
    "en-US-AvaNeural": "Breathy temptress - close",
  };

  let ws = null;
  let retry = 800;
  let micOn = true;
  let agentOn = true;
  let voiceId = null;

  function setState(s) {
    const state = Object.prototype.hasOwnProperty.call(STATE_TEXT, s) ? s : "idle";
    document.body.className = "state-" + state;
    statusText.textContent = STATE_TEXT[state];
    dot.classList.toggle("active", state !== "idle");
  }

  function setMic(on) {
    micOn = on;
    btnMic.classList.toggle("on", on);
    btnMic.setAttribute("aria-pressed", String(on));
    micTxt.textContent = on ? "Mic on" : "Mic off";
  }

  const meter = document.createElement("div");
  meter.className = "mic-meter";
  meter.style.cssText = "margin-top:12px;display:flex;align-items:center;gap:8px;font-size:11px;opacity:.85;";
  meter.innerHTML = `<span class="mic-meter-label">Mic level</span>
    <span class="mic-meter-track" style="flex:1;height:6px;border-radius:3px;background:rgba(255,255,255,.15);overflow:hidden;display:block;">
      <span class="mic-meter-fill" style="display:block;height:100%;width:0%;background:rgba(255,255,255,.35);transition:width 80ms linear;"></span>
    </span>`;
  document.querySelector(".controls-card").appendChild(meter);
  const meterFill = meter.querySelector(".mic-meter-fill");

  function setLevel(rms) {
    const pct = Math.min(100, Math.max(0, Math.round((rms / 0.15) * 100)));
    meterFill.style.width = pct + "%";
    meterFill.style.background = rms > 0.02 ? "#22c55e" : "rgba(255,255,255,.35)";
  }

  function setAgent(on) {
    agentOn = on;
    btnAgent.classList.toggle("on", on);
    btnAgent.setAttribute("aria-pressed", String(on));
    agentTxt.textContent = on ? "Agent on" : "Agent off";
  }

  function route() {
    const onConsolePath = location.pathname.endsWith("/console");
    const isConsole = (onConsolePath && !location.hash) || location.hash === "#console";
    if (!isConsole && onConsolePath) {
      history.replaceState(null, "", "/" + location.hash);
    }
    dossier.classList.toggle("view-hidden", isConsole);
    consolePanel.classList.toggle("view-hidden", !isConsole);
    document.body.classList.toggle("console-view", isConsole);
    if (!isConsole) {
      const id = location.hash.slice(1);
      if (id) {
        requestAnimationFrame(() => {
          const el = document.getElementById(id);
          if (el) el.scrollIntoView({ block: "start" });
        });
      }
    }
  }

  function selectVoice(id) {
    if (id === voiceId) return;
    voiceId = id;
    document.querySelectorAll(".voice-row").forEach((row) => {
      const on = row.dataset.voice === id;
      row.classList.toggle("selected", on);
      row.querySelector(".voice-row-main").setAttribute("aria-pressed", String(on));
    });
    send({ type: "voice", voice: id });
  }

  function populateVoices(voices, current) {
    voiceList.innerHTML = "";
    voiceId = current || null;
    for (const [id, label] of Object.entries(voices)) {
      const row = document.createElement("div");
      row.className = "voice-row";
      row.dataset.voice = id;

      const main = document.createElement("button");
      main.type = "button";
      main.className = "voice-row-main";
      main.setAttribute("aria-pressed", String(id === voiceId));
      const name = document.createElement("span");
      name.className = "voice-name";
      name.textContent = label;
      const desc = document.createElement("span");
      desc.className = "voice-desc";
      desc.textContent = VOICE_DESC[id] || "Edge neural voice";
      main.append(name, desc);

      const test = document.createElement("button");
      test.type = "button";
      test.className = "voice-test";
      test.textContent = "Test";
      test.setAttribute("aria-label", `Test ${label}`);

      main.addEventListener("click", () => selectVoice(id));
      test.addEventListener("click", () => {
        selectVoice(id);
        setTimeout(() => send({ type: "test-voice" }), 350);
      });
      row.append(main, test);
      voiceList.appendChild(row);
    }
  }

  const RAIL_FALLBACK = {
    stt: "faster-whisper",
    llm: "mercury-3",
    voice: "—",
    wake: "lana",
    spice: "1.0",
  };

  function applyConfig(cfg) {
    if (!cfg) return;
    document.querySelectorAll("[data-key]").forEach((el) => {
      const key = el.dataset.key;
      if (el.classList.contains("spec-mini-value")) {
        el.textContent = String(cfg[key] ?? RAIL_FALLBACK[key]);
      } else if (el.classList.contains("row-value") && key !== "latency") {
        el.textContent = String(cfg[key] ?? RAIL_FALLBACK[key]);
      }
    });
    if (cfg.voice_id && cfg.voice_id !== voiceId) {
      selectVoice(cfg.voice_id);
    }
    const wakeLabel = document.getElementById("wake-word-label");
    if (wakeLabel && cfg.wake) wakeLabel.textContent = cfg.wake;
  }

  const ACTIVITY_MAX = 40;
  let lastUserAt = null;

  function trimActivity() {
    while (activity.children.length > ACTIVITY_MAX) {
      activity.lastElementChild.remove();
    }
    if (activity.scrollTop !== 0) {
      activity.scrollTop = 0;
    }
  }

  function findUtt(id) {
    if (!id) return null;
    return activity.querySelector(`li[data-utt-id="${id}"]`);
  }

  function upsertPartial(d) {
    activityEmpty.hidden = true;
    let li = findUtt(d.utterance_id);
    if (!li) {
      li = document.createElement("li");
      li.className = "activity-item user";
      if (d.utterance_id) li.dataset.uttId = d.utterance_id;
      const now = new Date();
      const p = (n) => String(n).padStart(2, "0");
      const time = document.createElement("span");
      time.className = "activity-time";
      time.textContent = `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
      const body = document.createElement("span");
      body.className = "activity-text";
      li.append(time, body);
      activity.prepend(li);
    }
    const body = li.querySelector(".activity-text");
    body.textContent = d.text;
    li.classList.toggle("live", d.status === "partial");
    trimActivity();
  }

  function addActivity(kind, text, intent, utteranceId) {
    activityEmpty.hidden = true;
    let li = findUtt(utteranceId);
    const now = new Date();
    const p = (n) => String(n).padStart(2, "0");
    if (!li) {
      li = document.createElement("li");
      li.className = "activity-item " + kind;
      if (utteranceId) li.dataset.uttId = utteranceId;
      li.append(
        (() => {
          const time = document.createElement("span");
          time.className = "activity-time";
          time.textContent = `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
          return time;
        })()
      );
      activity.prepend(li);
    }
    const body = li.querySelector(".activity-text") || document.createElement("span");
    if (!li.querySelector(".activity-text")) {
      body.className = "activity-text";
      li.append(body);
    }
    body.textContent = text;
    if (intent && kind === "user") {
      const chip = document.createElement("span");
      chip.className = "activity-intent";
      chip.textContent = intent;
      body.append(chip);
    }
    if (kind === "assistant" && lastUserAt) {
      const ms = now - lastUserAt;
      lastUserAt = null;
      const sec = (ms / 1000).toFixed(1);
      const extra = document.createElement("span");
      extra.className = "activity-latency";
      extra.textContent = `replied in ${sec}s`;
      body.append(extra);
    }
    trimActivity();
  }

  async function requestMicPermission() {
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return true;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
      return true;
    } catch {
      return false;
    }
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => {
      retry = 800;
      dot.classList.add("active");
      statusText.textContent = "online";
      send({ type: "agent", connected: agentOn });
    };
    ws.onmessage = (e) => {
      let d;
      try { d = JSON.parse(e.data); } catch { return; }
      switch (d.type) {
        case "state": setState(d.state); break;
        case "voices": populateVoices(d.voices, d.current); break;
        case "voice":
          if (d.voice && d.voice !== voiceId) selectVoice(d.voice);
          break;
        case "mic": setMic(Boolean(d.enabled)); break;
        case "agent": setAgent(Boolean(d.connected)); break;
        case "config": applyConfig(d.config); break;
        case "level": setLevel(d.rms); break;
        case "mic_error": addActivity("ignored", "mic error — " + d.message); break;
        case "transcript_partial": upsertPartial(d); break;
        case "user": lastUserAt = new Date(); addActivity("user", d.text, d.intent, d.utterance_id); break;
        case "assistant": addActivity("assistant", d.text); break;
        case "ignored":
          addActivity("ignored", d.reason === "not-awake"
            ? `ignored — “${d.text}”`
            : `ignored while speaking — “${d.text}”`);
          break;
        case "awake": addActivity("ignored", "listening"); break;
      }
    };
    ws.onclose = () => {
      dot.classList.remove("active");
      statusText.textContent = "reconnecting…";
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 8000);
    };
    ws.onerror = () => ws.close();
  }

  btnMic.addEventListener("click", async () => {
    if (micOn) {
      setMic(false);
      send({ type: "mic", enabled: false });
      return;
    }
    const granted = await requestMicPermission();
    if (!granted) {
      addActivity("ignored", "microphone permission denied — allow mic access in the browser");
      return;
    }
    setMic(true);
    send({ type: "mic", enabled: true });
  });

  btnAgent.addEventListener("click", () => {
    setAgent(!agentOn);
    send({ type: "agent", connected: agentOn });
  });

  btnTest.addEventListener("click", () => {
    send({ type: "test-voice" });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") send({ type: "stop" });
  });

  window.addEventListener("hashchange", route);

  function onScroll() {
    header.classList.toggle("scrolled", window.scrollY > 8);
  }

  window.addEventListener("scroll", onScroll, { passive: true });

  route();
  connect();

  window.setState = setState;
  window.setMic = setMic;
  window.setAgent = setAgent;
  window.applyConfig = applyConfig;
  window.populateVoices = populateVoices;
  window.selectVoice = selectVoice;
  window.addActivity = addActivity;
  window.upsertPartial = upsertPartial;
  window.requestMicPermission = requestMicPermission;
})();
