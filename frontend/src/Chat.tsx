import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useSpeech, MicIcon } from "./speech";

export type ChatRole = "user" | "assistant" | "note" | "error";
export interface ChatMsg { role: ChatRole; text: string; }

interface Props {
  messages: ChatMsg[];
  status: string | null;   // línea efímera mientras trabaja: "" = pensando, texto = herramienta
  busy: boolean;
  onSend: (text: string) => void;
  onClose: () => void;
  onNew: () => void;
}

// markdown mínimo y seguro: escapa HTML, luego enlaces, **negrita** y `código`.
function mdLite(raw: string) {
  const esc = raw
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc
    // enlaces Markdown [texto](http(s)://…) -> <a> clicable (solo http/https, pestaña nueva)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

export default function Chat({ messages, status, busy, onSend, onClose, onNew }: Props) {
  const [text, setText] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, status, busy]);

  // auto-alto del cajetín (también cuando el texto llega por dictado, no solo al teclear)
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
  }, [text]);

  // dictado por voz: transcripción en vivo en el cajetín; al callar se envía solo
  // (si el asistente aún está respondiendo, el dictado se queda como borrador)
  const voice = useSpeech({
    onInterim: setText,
    onFinal: (t) => {
      if (busy) { setText(t); return; }
      onSend(t);
      setText("");
    },
  });

  function submit() {
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
  }

  return (
    <aside className="rs-chat" role="complementary" aria-label="Asistente IA">
      <header className="rs-chat-head">
        <span className="rs-chat-title">Asistente IA</span>
        <div className="rs-chat-actions">
          <button type="button" className="rs-chat-new" onClick={onNew} title="Conversación nueva">Nueva</button>
          <button type="button" className="rs-chat-x" onClick={onClose} aria-label="Cerrar chat">
            <svg width="18" height="18" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.6">
              <line x1="5" y1="5" x2="19" y2="19" strokeLinecap="round" />
              <line x1="19" y1="5" x2="5" y2="19" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </header>

      <div className="rs-chat-msgs" ref={scrollRef}>
        {messages.map((m, i) => {
          if (m.role === "note") return <div key={i} className="rs-chat-note">{m.text}</div>;
          if (m.role === "error") return <div key={i} className="rs-chat-banner">{m.text}</div>;
          if (m.role === "user") return <div key={i} className="rs-chat-msg rs-chat-u">{m.text}</div>;
          return (
            <div key={i} className="rs-chat-msg rs-chat-a"
                 dangerouslySetInnerHTML={{ __html: mdLite(m.text) }} />
          );
        })}
        {busy && (
          status
            ? <div className="rs-chat-tool"><i className="rs-chat-pulse" />{status}…</div>
            : <div className="rs-chat-think"><i /><i /><i /></div>
        )}
      </div>

      <div className="rs-chat-composer">
        <textarea
          ref={taRef}
          value={text}
          rows={1}
          placeholder={voice.listening ? "Escuchando…" : "Pregunta, pide una comparativa, filtra…"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {voice.supported && (
          <button
            type="button"
            className={`rs-chat-mic${voice.listening ? " is-rec" : ""}`}
            onClick={voice.toggle}
            aria-label={voice.listening ? "Parar dictado" : "Hablar con el asistente"}
            title={voice.listening ? "Parar dictado" : "Hablar con el asistente"}
          >
            <MicIcon small />
          </button>
        )}
        <button type="button" className="rs-chat-send" onClick={submit} disabled={busy} aria-label="Enviar">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z" />
          </svg>
        </button>
      </div>
    </aside>
  );
}
