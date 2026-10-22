/**
 * Synthetic providers for local development (TRY_FAKE_PROVIDERS="1", honoured only on a localhost request).
 * No network, no cost. Each model gets a slightly different synthetic sentence so the comparison view and
 * the caption player have something to show; timings are spread evenly over the clip.
 * TRY_FAKE_PROVIDERS="errors" also makes Groq fail, to show an error card.
 */
import { ProviderFailure, type ProviderInput, type TryWord } from "./providers";

const EN = "This is a synthetic transcript from the local fake provider layer, so the playground can be checked without paid calls. Captions break on pauses and sentence ends.";
const JA = "これはローカルの合成文字起こしです。有料の呼び出しなしで字幕の表示を確認できます。";

// Small per model edits so the diff view has something to mark.
const EDITS: Record<string, [string, string][]> = {
  "cf-whisper-large-v3-turbo": [],
  "cf-deepgram-nova-3": [["checked", "tested"], ["表示", "見た目"]],
  "groq-whisper": [["without paid calls", "without any paid calls"], ["字幕", "テロップ"]],
  deepgram: [["Captions break", "Caption lines break"]],
  "elevenlabs-scribe": [["synthetic", "simulated"], ["sentence ends", "the end of sentences"], ["確認", "チェック"]],
};

const DELAY: Record<string, number> = {
  "cf-whisper-large-v3-turbo": 700,
  "cf-deepgram-nova-3": 1100,
  "groq-whisper": 450,
  deepgram: 900,
  "elevenlabs-scribe": 1600,
};

function tokens(text: string, unspaced: boolean): string[] {
  if (!unspaced) return text.split(/\s+/).filter(Boolean);
  // Two to three characters per "word" for Japanese, like Whisper's sub-word timings.
  const out: string[] = [];
  const chars = Array.from(text);
  for (let i = 0; i < chars.length; ) {
    const n = 2 + (i % 2);
    out.push(chars.slice(i, i + n).join(""));
    i += n;
  }
  return out;
}

export function fakeRun(provider: string, mode: string) {
  return async ({ seconds, language }: ProviderInput, _env: Env, signal: AbortSignal) => {
    await new Promise<void>((resolve, reject) => {
      const t = setTimeout(resolve, DELAY[provider] ?? 800);
      signal.addEventListener("abort", () => {
        clearTimeout(t);
        reject(new ProviderFailure("The request was cancelled."));
      });
    });
    if (mode === "errors" && provider === "groq-whisper") throw new ProviderFailure("Groq is rate limiting this server (HTTP 429).");
    const ja = language === "ja";
    let text = ja ? JA : EN;
    for (const [a, b] of EDITS[provider] ?? []) text = text.replace(a, b);
    const toks = tokens(text, ja);
    const span = Math.max(seconds * 1000 - 400, 500);
    const step = span / toks.length;
    const words: TryWord[] = toks.map((t, i) => {
      // A pause after each sentence, so cue breaks show.
      const start = Math.round(200 + i * step);
      return { text: t, start_ms: start, end_ms: Math.round(start + step * 0.85) };
    });
    return { text, words, detected_language: language === "auto" ? "en" : language };
  };
}
