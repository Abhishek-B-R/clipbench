/**
 * SYNTHETIC illustration for the hero clip card. The sentence was written for this page; it is not from any
 * caption, video or provider output. The per-model edits illustrate common error types (a reading slip, a
 * dropped phrase under music, a hallucinated sign-off, style rewriting), so the card shows what the
 * benchmark measures. The landing page labels it "Illustration".
 */

export const SEGMENTS = [
  "今日はね", "、", "駅前に", "新しくできた", "カフェに", "行ってきた", "んだけど", "、", "BGMが", "めっちゃ", "大きくて", "、",
  "店員さんの", "声が", "全然", "聞こえなかったの", "。", "でも", "ラテは", "本当に", "おいしかった", "。",
];

export type Op =
  | { t: "eq"; ref: string }
  | { t: "sub"; ref: string; hyp: string }
  | { t: "del"; ref: string }
  | { t: "ins"; hyp: string };

type Edit = { sub: string } | { del: true } | { insAfter: string };

const EDITS: Record<string, Record<number, Edit>> = {
  "model-a": { 5: { sub: "言ってきた" }, 8: { sub: "ビージーエムが" }, 12: { del: true }, 13: { del: true }, 19: { sub: "ほんとに" } },
  "model-b": { 0: { sub: "京はね" }, 9: { sub: "めちゃ" }, 15: { sub: "聞こえなかった" } },
  "model-c": { 9: { sub: "めちゃ" } },
  "model-d": { 0: { sub: "今日は" }, 6: { sub: "けど" }, 9: { sub: "とても" } },
  "model-e": { 12: { del: true }, 13: { del: true }, 21: { insAfter: "ご視聴ありがとうございました" } },
  "model-f": { 2: { sub: "駅まえに" }, 4: { sub: "家へに" }, 8: { sub: "ビジエムが" }, 10: { sub: "多きくて" }, 12: { del: true }, 14: { sub: "前前" }, 19: { sub: "ほんと" } },
};

export const NOTES: Record<string, string> = {
  "model-a": "Loses a whole phrase where the music is loudest, and writes BGM out in katakana.",
  "model-b": "Mostly right; misreads the first word and trims sentence endings.",
  "model-c": "One small slip: shortens めっちゃ to めちゃ.",
  "model-d": "Hears everything but tidies the speech: drops ね, rewrites めっちゃ as とても.",
  "model-e": "Drops the phrase under the music, then adds a sign-off nobody said.",
  "model-f": "Free and local, but slips on many words when the music comes in.",
};

const PUNCT = /[、。,.!?！？\s]/g;
const len = (s: string) => s.replace(PUNCT, "").length;

/** Levenshtein distance on characters; used so a substitution like めっちゃ to めちゃ costs 1, not 4. */
function lev(a: string, b: string) {
  const x = [...a.replace(PUNCT, "")];
  const y = [...b.replace(PUNCT, "")];
  const d = Array.from({ length: x.length + 1 }, (_, i) => [i, ...Array<number>(y.length).fill(0)]);
  for (let j = 1; j <= y.length; j++) d[0]![j] = j;
  for (let i = 1; i <= x.length; i++)
    for (let j = 1; j <= y.length; j++)
      d[i]![j] = Math.min(d[i - 1]![j]! + 1, d[i]![j - 1]! + 1, d[i - 1]![j - 1]! + (x[i - 1] === y[j - 1] ? 0 : 1));
  return d[x.length]![y.length]!;
}

export const REF_LEN = SEGMENTS.reduce((s, x) => s + len(x), 0);

export function opsFor(provider: string): Op[] {
  const edits = EDITS[provider] ?? {};
  const out: Op[] = [];
  SEGMENTS.forEach((ref, i) => {
    const e = edits[i];
    if (!e) out.push({ t: "eq", ref });
    else if ("sub" in e) out.push({ t: "sub", ref, hyp: e.sub });
    else if ("del" in e) out.push({ t: "del", ref });
    else {
      out.push({ t: "eq", ref });
      out.push({ t: "ins", hyp: e.insAfter });
    }
  });
  return out;
}

export function scoreOps(ops: Op[]) {
  let sub = 0;
  let del = 0;
  let ins = 0;
  for (const o of ops) {
    if (o.t === "sub") sub += lev(o.ref, o.hyp);
    else if (o.t === "del") del += len(o.ref);
    else if (o.t === "ins") ins += len(o.hyp);
  }
  return { sub, del, ins, cer: (sub + del + ins) / REF_LEN };
}

/** Character offset (0..1) of each segment's start and end, for lighting up the waveform. */
export function segmentSpans() {
  let at = 0;
  const total = SEGMENTS.reduce((s, x) => s + x.length, 0);
  return SEGMENTS.map((s) => {
    const a = at / total;
    at += s.length;
    return [a, at / total] as const;
  });
}

/** Deterministic waveform: speech envelope over a steady music bed. */
export const WAVE = Array.from({ length: 112 }, (_, i) => {
  const speech = Math.abs(Math.sin(i * 0.38) * 0.55 + Math.sin(i * 0.11 + 0.6) * 0.3 + Math.sin(i * 1.31) * 0.15);
  const pause = [27, 28, 62, 63, 64, 88, 89].includes(i) ? 0.15 : 1;
  const music = 0.18 + 0.1 * Math.abs(Math.sin(i * 0.9)) + (i > 40 && i < 70 ? 0.22 : 0);
  return { speech: Math.min(1, 0.12 + speech * pause * 0.9), music: Math.min(1, music) };
});
