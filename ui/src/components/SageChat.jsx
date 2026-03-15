import { useState, useEffect, useRef, useCallback } from "react";
import { sageChat, sageSpeak } from "../api.js";

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

// ── Typing dots ───────────────────────────────────────────────────────────────

function TypingDots() {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center", padding: "10px 14px" }}>
      {[0, 1, 2].map(i => (
        <div key={i} style={{
          width: 7, height: 7, borderRadius: "50%",
          background: "var(--accent)",
          animation: `sage-bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
        }} />
      ))}
    </div>
  );
}

// ── Message bubble ────────────────────────────────────────────────────────────

function Message({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div style={{
      display: "flex", justifyContent: isUser ? "flex-end" : "flex-start",
      marginBottom: 10, padding: "0 12px",
    }}>
      {!isUser && (
        <div style={{
          width: 28, height: 28, borderRadius: "50%",
          background: "linear-gradient(135deg, #4f8ef7, #a78bfa)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 13, fontWeight: 700, color: "#fff",
          flexShrink: 0, marginRight: 8, marginTop: 2,
        }}>S</div>
      )}
      <div style={{
        maxWidth: "78%",
        background: isUser ? "var(--accent)" : "var(--bg3)",
        color: "var(--text)",
        padding: "9px 13px",
        borderRadius: isUser ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
        fontSize: 14, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word",
      }}>
        {msg.text}
      </div>
    </div>
  );
}

// ── Main SageChat ─────────────────────────────────────────────────────────────

const SESSION_KEY = "sage_session";

// sessionStorage persistence: survives panel close/reopen within the same browser
// tab, but is intentionally cleared when the tab closes (unlike localStorage).
// This is the right tradeoff for a financial chat — no sensitive conversation
// history left behind in persistent storage after the user closes the tab,
// but the session isn't lost just because they navigated away or toggled the panel.
function loadSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return null;
}

function saveSession(messages, history) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ messages, history }));
  } catch {}
}

export default function SageChat() {
  const saved = loadSession();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState(saved?.messages ?? []);
  const [history, setHistory] = useState(saved?.history ?? []);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [unread, setUnread] = useState(0);
  const [speaking, setSpeaking] = useState(false);

  // Voice modes
  const [voiceMode, setVoiceMode] = useState("off"); // off | browser | openai
  const voiceModeRef = useRef("off"); // ref avoids stale closure in speak()
  const [listening, setListening] = useState(false);  // manual mic active

  // Always-on / Hey Sage
  const [alwaysOn, setAlwaysOn] = useState(false);
  const [awake, setAwake] = useState(false); // wake word detected, capturing command

  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const audioRef = useRef(null);

  // sendRef: a stable ref that always points to the latest `send` callback.
  // The always-on silence timer (setTimeout in resetSilenceTimer) closes over
  // sendRef, not `send` directly. If it closed over `send`, it would capture a
  // stale version from the render in which the timer was created — meaning it
  // might use outdated `history`, `loading`, or `input` state. By calling
  // sendRef.current() instead, the timer always invokes the freshest send().
  const sendRef = useRef(null);

  // speakGenRef: a generation counter used to invalidate in-flight TTS queues.
  // When speak() is called, it captures the current generation number (myGen).
  // If stopSpeaking() or a new speak() call increments speakGenRef.current before
  // a sentence's audio promise resolves, the loop detects myGen !== speakGenRef.current
  // and aborts playback — preventing orphaned audio from a previous response
  // playing over the top of the current one.
  const speakGenRef = useRef(0);

  // Refs for always-on logic (stable across renders)
  const alwaysOnRef = useRef(false);
  const awakeRef = useRef(false);
  const commandBuf = useRef("");       // accumulated command after wake word
  const silenceTimer = useRef(null);
  const continuousRec = useRef(null);
  const manualRec = useRef(null);      // for the manual mic button

  // ── Persist session ──────────────────────────────────────────────────────────

  useEffect(() => {
    saveSession(messages, history);
  }, [messages, history]);

  // ── Scroll ──────────────────────────────────────────────────────────────────

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (open) {
      setUnread(0);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  // ── TTS ─────────────────────────────────────────────────────────────────────

  // Keep ref in sync with state
  useEffect(() => { voiceModeRef.current = voiceMode; }, [voiceMode]);

  const hasTTS = !!window.speechSynthesis;
  const VOICE_MODES = [
    { value: "off",     label: "🔇", title: "Voice off" },
    ...(hasTTS ? [{ value: "browser", label: "🗣", title: "Browser voice (free)" }] : []),
    { value: "openai",  label: "✦",  title: "AI voice (OpenAI)" },
  ];

  function stopSpeaking() {
    speakGenRef.current++;  // invalidate any in-progress sentence queue
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  }

  // Split a Sage response into individual sentences so we can request TTS for each
  // sentence in parallel. This is the key latency optimization: the user hears the
  // first sentence while OpenAI is still generating audio for sentences 2, 3, etc.
  // Without splitting, the entire response would be one TTS call, and playback
  // wouldn't start until the full audio was ready (~2-4 seconds for long responses).
  // The regex matches sentence-ending punctuation; the trailing `[^.!?]+$` catches
  // the last fragment if it doesn't end in punctuation.
  function splitSentences(text) {
    const parts = text.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [text];
    return parts.map(s => s.trim()).filter(s => s.length > 3);
  }

  const speak = useCallback(async (text) => {
    const mode = voiceModeRef.current;
    if (mode === "off") return;

    // Inline stop: increment generation counter to cancel any prior in-flight queue,
    // then pause any active audio element. We do this inline (not calling stopSpeaking)
    // to avoid capturing a stale closure on the stopSpeaking function itself.
    speakGenRef.current++;
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    window.speechSynthesis?.cancel();
    setSpeaking(false);

    if (mode === "openai") {
      const myGen = speakGenRef.current;
      const sentences = splitSentences(text);

      // Fire ALL sentence TTS requests to the backend immediately and in parallel.
      // Each sageSpeak() call returns a Promise<objectURL>. By launching them all
      // before we start the playback loop, sentences 2..N are already downloading
      // while sentence 1 is playing — sequential playback with parallel fetching.
      const urlPromises = sentences.map(s => sageSpeak(s));

      setSpeaking(true);
      // Play sentences in order. `await urlPromises[i]` resolves as soon as that
      // sentence's audio is ready — but since we launched all requests upfront,
      // it's often already resolved by the time we reach it.
      for (let i = 0; i < urlPromises.length; i++) {
        if (speakGenRef.current !== myGen) break; // user stopped or new speak() fired
        let url;
        try { url = await urlPromises[i]; } catch (err) {
          if (i === 0) {
            // First sentence failed — surface the error and bail entirely
            setSpeaking(false);
            setMessages(prev => [...prev, { role: "sage", text: `⚠️ OpenAI voice unavailable: ${err.message}` }]);
            return;
          }
          continue; // mid-response failure — skip this sentence and keep going
        }
        if (speakGenRef.current !== myGen) { URL.revokeObjectURL(url); break; }
        await new Promise(resolve => {
          const audio = new Audio(url);
          audioRef.current = audio;
          audio.play().catch(resolve);
          audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
          audio.onerror  = () => { URL.revokeObjectURL(url); resolve(); };
        });
      }
      if (speakGenRef.current === myGen) { setSpeaking(false); audioRef.current = null; }
      return;
    }

    // Browser TTS
    if (!window.speechSynthesis) return;
    const utt = new SpeechSynthesisUtterance(text);
    utt.rate = 1.0; utt.pitch = 0.85;
    const voices = window.speechSynthesis.getVoices();
    const male = voices.find(v => /male|man|david|mark|daniel|alex/i.test(v.name));
    if (male) utt.voice = male;
    setSpeaking(true);
    utt.onend = () => setSpeaking(false);
    utt.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utt);
  }, []); // reads refs only — no stale closures, no deps needed

  // ── Send ─────────────────────────────────────────────────────────────────────

  const send = useCallback(async (text) => {
    const msg = (text || input).trim();
    if (!msg || loading) return;
    setInput("");
    commandBuf.current = "";

    const userMsg = { role: "user", text: msg };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);

    try {
      const { response, history: newHistory } = await sageChat(msg, history);
      setHistory(newHistory);
      setMessages(prev => [...prev, { role: "sage", text: response }]);
      if (!open) setUnread(n => n + 1);
      speak(response);
    } catch (err) {
      setMessages(prev => [...prev, { role: "sage", text: `Error: ${err.message}` }]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, history, open, speak]);

  // Keep sendRef pointing to the latest send() on every render.
  // This is the other half of the stale-closure fix: the silence timer in
  // resetSilenceTimer() calls sendRef.current() rather than send() directly,
  // so it always uses the send() that has the current history/loading state.
  useEffect(() => { sendRef.current = send; }, [send]);

  // ── Always-on / Hey Sage ──────────────────────────────────────────────────

  const WAKE_REGEX = /\b(?:hey|ok|okay|hi),?\s*sage\b/i;

  function playActivationTone() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.08);
      gain.gain.setValueAtTime(0.15, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.25);
    } catch {}
  }

  function startContinuousRecognition() {
    if (!SpeechRecognition) return;
    const rec = new SpeechRecognition();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";
    let processed = 0; // how many results we've fully processed as final

    rec.onresult = (e) => {
      // Build transcript from results we haven't finalized yet
      let interim = "";
      for (let i = processed; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) {
          const finalText = r[0].transcript.trim();
          processed = i + 1;

          if (!awakeRef.current) {
            // Look for wake word
            if (WAKE_REGEX.test(finalText)) {
              awakeRef.current = true;
              setAwake(true);
              setOpen(true);
              playActivationTone();
              // Strip wake word, keep anything said after it
              const after = finalText.replace(WAKE_REGEX, "").trim();
              if (after) {
                commandBuf.current = after;
                setInput(after);
                resetSilenceTimer();
              }
            }
          } else {
            // Awake — accumulate command
            commandBuf.current = (commandBuf.current + " " + finalText).trim();
            setInput(commandBuf.current);
            resetSilenceTimer();
          }
        } else {
          interim += r[0].transcript;
        }
      }

      // Show live interim text while awake
      if (awakeRef.current && interim) {
        setInput((commandBuf.current + " " + interim).trim());
      }
    };

    rec.onend = () => {
      // Auto-restart unless explicitly stopped
      if (alwaysOnRef.current) {
        setTimeout(() => {
          try { rec.start(); processed = 0; } catch {}
        }, 200);
      }
    };

    rec.onerror = (e) => {
      if (e.error === "not-allowed") {
        alwaysOnRef.current = false;
        setAlwaysOn(false);
        setAwake(false);
      } else if (alwaysOnRef.current) {
        setTimeout(() => {
          try { rec.start(); processed = 0; } catch {}
        }, 500);
      }
    };

    continuousRec.current = rec;
    rec.start();
  }

  function resetSilenceTimer() {
    if (silenceTimer.current) clearTimeout(silenceTimer.current);
    silenceTimer.current = setTimeout(() => {
      const cmd = commandBuf.current.trim();
      if (cmd) {
        sendRef.current?.(cmd); // use ref — avoids stale closure from when timer was created
      }
      awakeRef.current = false;
      setAwake(false);
      commandBuf.current = "";
    }, 1800); // 1.8s of silence → auto-send
  }

  function toggleAlwaysOn() {
    if (alwaysOn) {
      alwaysOnRef.current = false;
      setAlwaysOn(false);
      setAwake(false);
      awakeRef.current = false;
      continuousRec.current?.stop();
      continuousRec.current = null;
      if (silenceTimer.current) clearTimeout(silenceTimer.current);
      commandBuf.current = "";
      setInput("");
    } else {
      alwaysOnRef.current = true;
      setAlwaysOn(true);
      startContinuousRecognition();
    }
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      alwaysOnRef.current = false;
      continuousRec.current?.stop();
      manualRec.current?.stop();
      if (silenceTimer.current) clearTimeout(silenceTimer.current);
    };
  }, []);

  // ── Manual mic (push-to-talk) ────────────────────────────────────────────

  function startListening() {
    if (!SpeechRecognition) return;
    const rec = new SpeechRecognition();
    rec.continuous = false;
    rec.interimResults = false;
    rec.lang = "en-US";
    rec.onresult = (e) => { setInput(e.results[0][0].transcript); setListening(false); };
    rec.onerror = () => setListening(false);
    rec.onend = () => setListening(false);
    manualRec.current = rec;
    rec.start();
    setListening(true);
  }

  function stopListening() {
    manualRec.current?.stop();
    setListening(false);
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  const hasVoice = !!SpeechRecognition;

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <>
      <style>{`
        @keyframes sage-bounce {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
          40% { transform: scale(1); opacity: 1; }
        }
        @keyframes sage-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(79,142,247,0.4); }
          50% { box-shadow: 0 0 0 8px rgba(79,142,247,0); }
        }
        @keyframes sage-awake-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(52,211,153,0.6); }
          50% { box-shadow: 0 0 0 12px rgba(52,211,153,0); }
        }
      `}</style>

      {/* ── Floating button ── */}
      {!open && (
        <button
          onClick={() => awake ? null : setOpen(true)}
          className="sage-float-btn"
          style={{
            position: "fixed", bottom: 24, right: 24,
            width: 56, height: 56, borderRadius: "50%",
            background: awake
              ? "linear-gradient(135deg, #34d399, #4f8ef7)"
              : "linear-gradient(135deg, #4f8ef7, #a78bfa)",
            border: "none", cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 22, color: "#fff",
            boxShadow: "0 4px 20px rgba(79,142,247,0.5)",
            animation: awake
              ? "sage-awake-pulse 1s ease-in-out infinite"
              : "sage-pulse 2.5s ease-in-out infinite",
            zIndex: 1000,
          }}
          title={awake ? "Sage is listening…" : "Chat with Sage"}
        >
          {awake ? "👂" : "✦"}
          {/* Always-on indicator dot */}
          {alwaysOn && !awake && (
            <div style={{
              position: "absolute", bottom: 2, right: 2,
              width: 12, height: 12, borderRadius: "50%",
              background: "#34d399", border: "2px solid var(--bg)",
            }} />
          )}
          {unread > 0 && !awake && (
            <div style={{
              position: "absolute", top: 0, right: 0,
              background: "#f87171", borderRadius: "50%",
              width: 18, height: 18, fontSize: 10,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontWeight: 700, color: "#fff",
            }}>{unread}</div>
          )}
        </button>
      )}

      {/* ── Chat panel ── */}
      {open && (
        <div style={{
          position: "fixed", bottom: 24, right: 24,
          width: "min(420px, calc(100vw - 32px))",
          height: "min(580px, calc(100vh - 100px))",
          background: "var(--bg2)", border: "1px solid var(--border)",
          borderRadius: 16, display: "flex", flexDirection: "column",
          boxShadow: "0 8px 40px rgba(0,0,0,0.5)", zIndex: 1000, overflow: "hidden",
        }}>

          {/* Header */}
          <div style={{
            display: "flex", alignItems: "center", padding: "14px 16px",
            borderBottom: "1px solid var(--border)", background: "var(--bg3)",
          }}>
            <div style={{
              width: 34, height: 34, borderRadius: "50%",
              background: "linear-gradient(135deg, #4f8ef7, #a78bfa)",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 16, fontWeight: 700, color: "#fff", marginRight: 10, flexShrink: 0,
            }}>S</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: 15, color: "var(--text)" }}>Sage</div>
              <div style={{ fontSize: 11, color: awake ? "#34d399" : "var(--text2)" }}>
                {awake ? "Listening… speak your question" : alwaysOn ? "👂 Always listening for \"Hey Sage\"" : "Your AI financial advisor"}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {/* Hey Sage toggle */}
              {hasVoice && (
                <button
                  onClick={toggleAlwaysOn}
                  title={alwaysOn ? "Disable Hey Sage" : "Enable Hey Sage (always listening)"}
                  style={{
                    background: alwaysOn ? "rgba(52,211,153,0.15)" : "none",
                    border: `1px solid ${alwaysOn ? "#34d399" : "var(--border)"}`,
                    color: alwaysOn ? "#34d399" : "var(--text2)",
                    borderRadius: 6, padding: "4px 8px", cursor: "pointer", fontSize: 12,
                    fontWeight: alwaysOn ? 600 : 400,
                  }}
                >
                  {alwaysOn ? "👂 On" : "👂 Off"}
                </button>
              )}
              {/* Voice mode selector */}
              <div style={{ display: "flex", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
                {VOICE_MODES.map((m, idx) => (
                  <button key={m.value} title={m.title}
                    onClick={() => setVoiceMode(m.value)}
                    style={{
                      background: voiceMode === m.value ? "rgba(79,142,247,0.2)" : "none",
                      border: "none",
                      borderRight: idx < VOICE_MODES.length - 1 ? "1px solid var(--border)" : "none",
                      color: voiceMode === m.value ? "var(--accent)" : "var(--text2)",
                      padding: "4px 8px", cursor: "pointer", fontSize: 14,
                    }}
                  >{m.label}</button>
                ))}
              </div>
              {/* Stop speaking */}
              {speaking && (
                <button onClick={stopSpeaking} title="Stop speaking"
                  style={{
                    background: "rgba(248,113,113,0.15)", border: "1px solid #f87171",
                    color: "#f87171", borderRadius: 6, padding: "4px 8px",
                    cursor: "pointer", fontSize: 13, fontWeight: 600,
                  }}>⏹</button>
              )}
              <button onClick={() => { setMessages([]); setHistory([]); sessionStorage.removeItem(SESSION_KEY); }} title="Clear conversation"
                style={{ background: "none", border: "1px solid var(--border)", color: "var(--text2)",
                  borderRadius: 6, padding: "4px 8px", cursor: "pointer", fontSize: 13 }}>↺</button>
              <button onClick={() => setOpen(false)}
                style={{ background: "none", border: "1px solid var(--border)", color: "var(--text2)",
                  borderRadius: 6, padding: "4px 8px", cursor: "pointer", fontSize: 14 }}>✕</button>
            </div>
          </div>

          {/* Messages */}
          <div style={{ flex: 1, overflowY: "auto", padding: "12px 0", display: "flex", flexDirection: "column" }}>
            {messages.length === 0 && (
              <div style={{ textAlign: "center", padding: "32px 24px", color: "var(--text2)" }}>
                <div style={{ fontSize: 32, marginBottom: 10 }}>✦</div>
                <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 6, color: "var(--text)" }}>Hi, I'm Sage</div>
                <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                  Ask me anything about your finances. Enable{" "}
                  <strong style={{ color: "#34d399" }}>👂 Hey Sage</strong> to talk hands-free.
                </div>
                <div style={{ marginTop: 20, display: "flex", flexDirection: "column", gap: 8, alignItems: "center" }}>
                  {["What's my net worth?", "How am I spending?", "Am I on track to retire?"].map(q => (
                    <button key={q} onClick={() => send(q)} style={{
                      background: "var(--bg3)", border: "1px solid var(--border)",
                      color: "var(--text2)", borderRadius: 20, padding: "6px 14px",
                      cursor: "pointer", fontSize: 12, transition: "all 0.15s",
                    }}
                      onMouseEnter={e => e.target.style.color = "var(--accent)"}
                      onMouseLeave={e => e.target.style.color = "var(--text2)"}
                    >{q}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((msg, i) => <Message key={i} msg={msg} />)}
            {loading && (
              <div style={{ display: "flex", padding: "0 12px", marginBottom: 10 }}>
                <div style={{
                  width: 28, height: 28, borderRadius: "50%",
                  background: "linear-gradient(135deg, #4f8ef7, #a78bfa)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 13, fontWeight: 700, color: "#fff", flexShrink: 0, marginRight: 8,
                }}>S</div>
                <div style={{ background: "var(--bg3)", borderRadius: "14px 14px 14px 4px" }}>
                  <TypingDots />
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <div style={{
            padding: "12px", borderTop: "1px solid var(--border)",
            display: "flex", gap: 8, alignItems: "flex-end",
          }}>
            {/* Awake indicator */}
            {awake && (
              <div style={{
                position: "absolute", bottom: 70, left: "50%", transform: "translateX(-50%)",
                background: "rgba(52,211,153,0.15)", border: "1px solid #34d399",
                borderRadius: 20, padding: "4px 14px", fontSize: 12, color: "#34d399",
                display: "flex", alignItems: "center", gap: 6,
              }}>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#34d399",
                  animation: "sage-bounce 1s ease-in-out infinite" }} />
                Listening — speak now
              </div>
            )}
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={awake ? "Listening…" : listening ? "Listening…" : "Ask Sage anything… or say \"Hey Sage\""}
              rows={1}
              style={{
                flex: 1, background: "var(--bg3)",
                border: `1px solid ${awake ? "#34d399" : "var(--border)"}`,
                color: "var(--text)", borderRadius: 10, padding: "9px 12px",
                fontSize: 14, resize: "none", outline: "none", lineHeight: 1.5,
                maxHeight: 100, overflow: "auto", fontFamily: "inherit",
                transition: "border-color 0.2s",
              }}
              onInput={e => {
                e.target.style.height = "auto";
                e.target.style.height = Math.min(e.target.scrollHeight, 100) + "px";
              }}
            />
            {hasVoice && !alwaysOn && (
              <button
                onClick={listening ? stopListening : startListening}
                title={listening ? "Stop" : "Push to talk"}
                style={{
                  width: 38, height: 38, borderRadius: "50%", flexShrink: 0,
                  background: listening ? "#f87171" : "var(--bg3)",
                  border: `1px solid ${listening ? "#f87171" : "var(--border)"}`,
                  color: listening ? "#fff" : "var(--text2)",
                  cursor: "pointer", fontSize: 16,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  animation: listening ? "sage-awake-pulse 1s ease-in-out infinite" : "none",
                }}>🎤</button>
            )}
            <button
              onClick={() => send()}
              disabled={!input.trim() || loading}
              style={{
                width: 38, height: 38, borderRadius: "50%", flexShrink: 0,
                background: input.trim() && !loading ? "var(--accent)" : "var(--bg3)",
                border: "1px solid var(--border)",
                color: input.trim() && !loading ? "#fff" : "var(--text2)",
                cursor: input.trim() && !loading ? "pointer" : "default",
                fontSize: 16, display: "flex", alignItems: "center", justifyContent: "center",
                transition: "all 0.15s",
              }}>➤</button>
          </div>
        </div>
      )}
    </>
  );
}
