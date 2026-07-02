import { useEffect, useRef, useState } from "react";

// Dictado por voz con la Web Speech API (Chrome/Edge/Safari; requiere HTTPS o localhost).
// La API sigue siendo experimental y no está en lib.dom -> tipos locales mínimos.
interface RecResultEvent {
  results: { length: number; [i: number]: { 0: { transcript: string } } };
}
interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  onresult: ((e: RecResultEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

const SR: (new () => Recognition) | undefined =
  (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;

export function MicIcon({ small = false }: { small?: boolean }) {
  const s = small ? 16 : 20;
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="9" y="2.5" width="6" height="11.5" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0" strokeLinecap="round" />
      <line x1="12" y1="18" x2="12" y2="21.5" strokeLinecap="round" />
    </svg>
  );
}

interface Options {
  onInterim: (text: string) => void; // transcripción en vivo mientras se habla
  onFinal: (text: string) => void;   // frase completa (al callar o al parar a mano)
}

// Toggle de dictado: start() escucha hasta que el usuario calla (continuous=false) y
// entonces emite onFinal con lo reconocido. Los callbacks se leen de un ref para que
// vean siempre el estado actual del componente, no el del render que creó el reconocedor.
export function useSpeech(opts: Options) {
  const [listening, setListening] = useState(false);
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const recRef = useRef<Recognition | null>(null);
  const heardRef = useRef(""); // lo último transcrito (aunque sea provisional)

  // al desmontar, corta el dictado sin emitir onFinal (p. ej. cerrar el chat a mitad de frase)
  useEffect(() => () => {
    const rec = recRef.current;
    if (rec) { rec.onend = null; rec.abort(); }
  }, []);

  function start() {
    if (!SR || recRef.current) return;
    const rec = new SR();
    rec.lang = "es-ES";
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    heardRef.current = "";
    rec.onresult = (e) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      heardRef.current = text;
      optsRef.current.onInterim(text);
    };
    // onend llega siempre (también tras un error tipo "no-speech" o permiso denegado);
    // si se paró a mano con solo un resultado provisional, ese texto vale como final.
    rec.onend = () => {
      recRef.current = null;
      setListening(false);
      const text = heardRef.current.trim();
      if (text) optsRef.current.onFinal(text);
    };
    recRef.current = rec;
    setListening(true);
    rec.start();
  }

  function toggle() {
    if (recRef.current) recRef.current.stop();
    else start();
  }

  return { supported: !!SR, listening, toggle };
}
