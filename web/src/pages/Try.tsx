import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { Link } from "react-router";
import { ArrowUpRight, FileAudio, Mic, Play, RotateCcw, Square, Upload } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Segmented";
import { ErrorState } from "@/components/ui/State";
import { cn } from "@/lib/cn";
import { clipCost, runTry, TRY_LANGUAGES, TRY_MODELS, TryError, type TryLanguage, type TryResult } from "@/try/api";
import { ACCEPT, decodeFile, useRecorder, type Loaded } from "@/try/audio";
import { CaptionPlayer } from "@/try/CaptionPlayer";
import { CompareView } from "@/try/Compare";
import { ResultCard, stoppedEarly, usd } from "@/try/Results";
import { useTurnstile } from "@/try/turnstile";
import { clock, Waveform, type Selection } from "@/try/Waveform";
import { downmix, encodeWav, MAX_CLIP_SECONDS, MIN_CLIP_SECONDS, TARGET_RATE, toMono16k } from "@/try/wav";

/** Where the honest note sends people for real numbers. */
const EARLY_LOOK_PATH = "/early";

const LANG_LABEL: Record<TryLanguage, string> = { auto: "Auto", en: "EN", fr: "FR", ja: "JA", ko: "KO", es: "ES", hi: "HI" };
const LANG_ARIA: Record<TryLanguage, string> = { auto: "Detect the language", en: "English", fr: "French", ja: "Japanese", ko: "Korean", es: "Spanish", hi: "Hindi" };

type Phase = "idle" | "preparing" | "running" | "done";

/** The most common detected language among answers, for "auto" runs (decides word or character diffs). */
function majorityLanguage(results: TryResult[]): string | null {
  const counts = new Map<string, number>();
  for (const r of results) if (r.detected_language && !r.error) counts.set(r.detected_language, (counts.get(r.detected_language) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

export default function Try() {
  const [clip, setClip] = useState<Loaded | null>(null);
  const [mono, setMono] = useState<Float32Array | null>(null);
  const [sel, setSel] = useState<Selection>({ start: 0, end: 0 });
  const [loadError, setLoadError] = useState<string | null>(null);
  const [decoding, setDecoding] = useState(false);
  const [language, setLanguage] = useState<TryLanguage>("auto");
  const [phase, setPhase] = useState<Phase>("idle");
  const [runError, setRunError] = useState<TryError | null>(null);
  const [results, setResults] = useState<Record<string, TryResult | null>>({});
  const [sent, setSent] = useState<{ samples: Float32Array; language: TryLanguage } | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [baseline, setBaseline] = useState(TRY_MODELS[0]!.provider);
  const [captionModel, setCaptionModel] = useState(TRY_MODELS[0]!.provider);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const widget = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const turnstile = useTurnstile(widget);

  const accept = useCallback(async (blob: Blob, name: string, source: Loaded["source"]) => {
    setLoadError(null);
    setDecoding(true);
    try {
      const buffer = await decodeFile(blob, name);
      setClip({ name, buffer, source });
      setMono(downmix(Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c))));
      setSel({ start: 0, end: Math.min(buffer.duration, MAX_CLIP_SECONDS) });
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "This browser could not decode that file.");
    } finally {
      setDecoding(false);
    }
  }, []);

  const rec = useRecorder((blob) => void accept(blob, "Microphone recording", "mic"));

  useEffect(() => () => abort.current?.abort(), []);

  const onFiles = (files: FileList | null) => {
    const f = files?.[0];
    if (f) void accept(f, f.name, "file");
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    onFiles(e.dataTransfer.files);
  };

  const span = Math.max(0, sel.end - sel.start);
  const estimate = clipCost(span);
  const running = phase === "preparing" || phase === "running";
  const canRun = Boolean(clip && turnstile.token && !running && span >= MIN_CLIP_SECONDS && span <= MAX_CLIP_SECONDS + 0.01);

  const run = async () => {
    if (!clip || !turnstile.token) return;
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setRunError(null);
    setTotal(null);
    setPhase("preparing");
    try {
      const samples = await toMono16k(clip.buffer, sel.start, sel.end);
      const wav = encodeWav(samples);
      setSent({ samples, language });
      setResults(Object.fromEntries(TRY_MODELS.map((m) => [m.provider, null])));
      setPhase("running");
      await runTry(
        wav,
        language,
        turnstile.token,
        (e) => {
          if (e.type === "result") {
            const { type: _t, ...r } = e;
            setResults((prev) => ({ ...prev, [r.provider]: r }));
          } else if (e.type === "done") setTotal(e.cost_usd);
        },
        ctrl.signal,
      );
      setPhase("done");
    } catch (e) {
      if (ctrl.signal.aborted) return;
      setRunError(e instanceof TryError ? e : new TryError("unavailable", "Something went wrong preparing the clip.", 0));
      setPhase("idle");
      setSent(null);
    } finally {
      // A Turnstile token is single use; get a fresh one for the next run.
      turnstile.reset();
    }
  };

  const reset = () => {
    abort.current?.abort();
    setClip(null);
    setMono(null);
    setPhase("idle");
    setResults({});
    setSent(null);
    setRunError(null);
    setTotal(null);
  };

  const done = useMemo(() => TRY_MODELS.map((m) => results[m.provider]).filter((r): r is TryResult => Boolean(r)), [results]);
  const stopped = useMemo(() => stoppedEarly(results), [results]);
  const diffLanguage = sent && sent.language !== "auto" ? sent.language : majorityLanguage(done);
  const answered = done.filter((r) => !r.error);

  return (
    <>
      <PageHeader
        eyebrow="Playground"
        title="Try it"
        description="Run one short clip of your own through every model in the benchmark and see how their transcripts and captions differ."
      />
      <div className="frame grid gap-8 py-8">
        <Figure
          n={1}
          title="Your clip"
          caption={clip ? "Drag the handles to choose what is sent. Only the selected span leaves your browser." : "A file or a recording. It is decoded here, in your browser."}
          controls={
            clip ? (
              <Button variant="ghost" size="sm" onClick={reset} disabled={running}>
                <RotateCcw size={14} /> Start over
              </Button>
            ) : null
          }
          bodyClassName="p-0 sm:p-0"
        >
          <div className="grid lg:grid-cols-[minmax(0,1fr)_380px]">
            {/* Input: dropzone, recorder, or the decoded waveform with the trim window. */}
            <div className="min-w-0 border-b border-line p-4 sm:p-5 lg:border-r lg:border-b-0">
              {!clip ? (
                <div
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={onDrop}
                  className={cn(
                    "grid min-h-[300px] content-center justify-items-center gap-5 rounded-md border border-dashed px-5 py-10 text-center transition-colors duration-150",
                    dragging ? "border-ink bg-sunken" : "border-line-strong bg-sunken/40",
                  )}
                >
                  <span className="grid size-10 place-items-center rounded-sm border border-line bg-surface text-ink-3">
                    <FileAudio size={18} strokeWidth={1.5} aria-hidden />
                  </span>
                  <div>
                    <h2 className="display text-[28px] leading-[1.1] text-ink sm:text-[34px]">Drop a clip, read every caption.</h2>
                    <p className="mx-auto mt-2 max-w-[48ch] text-[14px] text-ink-2">
                      mp3, m4a, wav, webm, mp4 or mov. Video files give up their audio track. Up to {MAX_CLIP_SECONDS} s is sent; a longer file gets a trim window.
                    </p>
                  </div>
                  {rec.status === "recording" ? (
                    <div className="grid w-full max-w-sm gap-3" aria-live="polite">
                      <div className="flex items-center justify-center gap-2 font-mono text-[13px] text-ink">
                        <span className="size-2 animate-blink rounded-full bg-ink" aria-hidden />
                        Recording {clock(rec.elapsed)} / {clock(MAX_CLIP_SECONDS)}
                      </div>
                      <div className="h-1 overflow-hidden rounded-full bg-line" aria-hidden>
                        <div className="h-full bg-ink" style={{ width: `${(rec.elapsed / MAX_CLIP_SECONDS) * 100}%` }} />
                      </div>
                      <Button variant="primary" onClick={rec.stop} className="justify-self-center">
                        <Square size={13} /> Stop
                      </Button>
                    </div>
                  ) : (
                    <div className="flex flex-wrap justify-center gap-2">
                      <Button variant="primary" onClick={() => fileInput.current?.click()} disabled={decoding}>
                        <Upload size={14} /> {decoding ? "Opening..." : "Choose a file"}
                      </Button>
                      <Button variant="secondary" onClick={() => void rec.start()} disabled={decoding || rec.status === "asking"}>
                        <Mic size={14} /> {rec.status === "asking" ? "Waiting for the mic..." : "Record"}
                      </Button>
                    </div>
                  )}
                  <input ref={fileInput} type="file" accept={ACCEPT} className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => onFiles(e.target.files)} />
                  {(loadError || rec.status === "denied" || rec.status === "unsupported") && (
                    <p role="alert" className="max-w-[52ch] text-[13.5px] text-signal-ink">
                      {loadError ??
                        (rec.status === "denied"
                          ? "Microphone access was blocked. Allow it in your browser's site settings, or choose a file."
                          : "This browser cannot record audio here. Choose a file instead.")}
                    </p>
                  )}
                </div>
              ) : (
                <div className="grid gap-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="min-w-0 truncate text-[14px] font-medium text-ink">{clip.name}</p>
                    <p className="num text-[12.5px] text-ink-3">{clock(clip.buffer.duration)} total</p>
                  </div>
                  <div className="rounded-md border border-line bg-sunken/50 px-3 py-3">
                    {mono && (
                      <Waveform
                        data={mono}
                        duration={clip.buffer.duration}
                        height={112}
                        selection={sel}
                        onSelection={setSel}
                        minSpan={Math.min(MIN_CLIP_SECONDS, clip.buffer.duration)}
                        maxSpan={MAX_CLIP_SECONDS}
                        label={`Waveform of ${clip.name}`}
                      />
                    )}
                  </div>
                  <p className="num text-[12.5px] text-ink-2" aria-live="polite">
                    Sending {clock(sel.start)} to {clock(sel.end)} ({span.toFixed(1)} s), resampled to 16 kHz mono.
                  </p>
                </div>
              )}
            </div>

            {/* Settings, verification, consent and Run. */}
            <div className="grid content-start gap-5 p-4 sm:p-5">
              <div className="grid gap-2">
                <span className="label" id="lang-label">
                  Language
                </span>
                <Segmented
                  label="Language"
                  value={language}
                  onChange={setLanguage}
                  options={TRY_LANGUAGES.map((l) => ({ value: l, label: LANG_LABEL[l], aria: LANG_ARIA[l] }))}
                  className="w-fit max-w-full overflow-x-auto"
                />
                <p className="text-[12.5px] text-ink-3">Auto lets each model detect it. Naming it usually helps.</p>
              </div>

              <div className="grid gap-2">
                <span className="label">Verification</span>
                <div ref={widget} className="min-h-[65px]" />
                {turnstile.status === "error" && (
                  <p role="alert" className="text-[12.5px] text-signal-ink">
                    The verification check could not load. Reload the page, or allow challenges.cloudflare.com.
                  </p>
                )}
              </div>

              <div className="grid gap-2 rounded-md border border-line bg-sunken/50 p-3.5 text-[12.5px] leading-relaxed text-ink-2">
                <p>
                  <span className="font-medium text-ink">Before you run it.</span> Run sends the selected audio to Cloudflare Workers AI, Groq, Deepgram and ElevenLabs to transcribe.
                  clipbench does not store your audio or the transcripts. Some providers may keep it, or train on it, under their own terms:
                </p>
                <ul className="grid gap-1.5">
                  {TRY_MODELS.map((m) => (
                    <li key={m.provider} className="flex gap-1.5">
                      <span aria-hidden className="text-ink-3">
                        /
                      </span>
                      <span>
                        <span className="text-ink">{m.label}.</span> {m.policy}{" "}
                        <a href={m.policy_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 underline decoration-line-strong underline-offset-2 hover:decoration-ink">
                          Policy
                          <ArrowUpRight size={11} aria-hidden />
                        </a>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="grid gap-2">
                <Button variant="primary" size="lg" onClick={() => void run()} disabled={!canRun}>
                  <Play size={15} />
                  {phase === "preparing" ? "Preparing the clip..." : phase === "running" ? "Transcribing..." : "Run every model"}
                </Button>
                <p className="num text-center text-[12px] text-ink-3">
                  {clip ? `About ${usd(estimate)} at list prices for this clip, paid by clipbench.` : "Add a clip to see what it costs to run."}
                  {clip && !turnstile.token && !running ? " Waiting for verification." : ""}
                </p>
              </div>
              {runError && (
                <ErrorState
                  title={runError.status === 429 ? "Not right now." : "That run did not go through."}
                  body={runError.message}
                  onRetry={runError.status === 429 ? undefined : turnstile.token ? () => void run() : undefined}
                />
              )}
            </div>
          </div>
        </Figure>

        {sent && (
          <>
            <Figure
              n={2}
              title="Transcripts"
              caption="Each card fills in as its provider answers. One provider failing does not stop the others."
              source={`source: POST /api/try, this clip only (n=1), ${(sent.samples.length / TARGET_RATE).toFixed(1)} s${total != null ? `, total ${usd(total)}` : ""}`}
            >
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {TRY_MODELS.map((m) => (
                  <ResultCard key={m.provider} provider={m.provider} label={m.label} model_id={m.model_id} result={results[m.provider] ?? null} language={sent.language === "auto" ? null : sent.language} stopped={stopped[m.provider]} />
                ))}
              </div>
            </Figure>

            <Figure n={3} title="Where the models differ" caption="Pick a baseline; each other model is marked where it departs from it. Different is not the same as wrong.">
              {answered.length >= 2 ? (
                <CompareView results={answered} baseline={baseline} onBaseline={setBaseline} language={diffLanguage} />
              ) : (
                <p className="text-[13.5px] text-ink-2">{phase === "running" ? "Waiting for at least two transcripts." : "Not enough models answered to compare."}</p>
              )}
            </Figure>

            <Figure n={4} title="Captions" caption="The clip as the models heard it, with live captions from each model's word timings, cut by the benchmark's line-break rule.">
              {answered.some((r) => r.words.length) ? (
                <CaptionPlayer samples={sent.samples} results={answered} model={captionModel} onModel={setCaptionModel} language={diffLanguage} />
              ) : (
                <p className="text-[13.5px] text-ink-2">{phase === "running" ? "Captions appear when the first model with word timings answers." : "No model returned word timings for this clip."}</p>
              )}
            </Figure>
          </>
        )}

        <aside className="grid gap-2 rounded-md border border-line bg-surface p-4 text-[13.5px] leading-relaxed text-ink-2 sm:p-5">
          <p className="label">Read this before drawing conclusions</p>
          <p>
            This is a single clip with no reference transcript, so it shows how the models differ, not which one is right. Latency here is measured with every
            provider called at once from a Cloudflare Worker, which is not how the benchmark measures it. Costs are list prices.
          </p>
          <p className="flex flex-wrap gap-x-4 gap-y-1">
            <Link to="/methodology" className="inline-flex items-center gap-1 font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink">
              How the benchmark measures <ArrowUpRight size={13} aria-hidden />
            </Link>
            <Link to={EARLY_LOOK_PATH} className="inline-flex items-center gap-1 font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink">
              The Early look board <ArrowUpRight size={13} aria-hidden />
            </Link>
          </p>
        </aside>
      </div>
    </>
  );
}
