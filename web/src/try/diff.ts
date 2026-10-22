/**
 * Transcript differences against a baseline model, for the /try comparison view. No reference exists here, so
 * this says where models disagree, not who is right. Spaced languages compare words; Japanese and Korean compare
 * characters (the benchmark scores both with CER). Case and punctuation are ignored when matching.
 */

export type DiffMode = "word" | "char";
export type Token = { text: string; key: string };
export type Op = { op: "same" | "ins" | "del"; text: string };

export const diffMode = (language: string | null | undefined): DiffMode => (language === "ja" || language === "ko" ? "char" : "word");

const strip = (s: string) => s.normalize("NFKC").toLowerCase().replace(/[\p{P}\p{S}]/gu, "");

export function tokenize(text: string, mode: DiffMode): Token[] {
  if (mode === "char") {
    return Array.from(text.normalize("NFKC"))
      .filter((ch) => strip(ch) && !/\s/u.test(ch))
      .map((ch) => ({ text: ch, key: strip(ch) }));
  }
  return text
    .split(/\s+/u)
    .filter(Boolean)
    .map((w) => ({ text: w, key: strip(w) }))
    .filter((t) => t.key);
}

/** Longest common subsequence diff of `other` against `base`: "del" is in base only, "ins" in other only. */
export function diffTokens(base: Token[], other: Token[]): Op[] {
  const n = base.length;
  const m = other.length;
  const w = m + 1;
  const L = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      L[i * w + j] = base[i]!.key === other[j]!.key ? L[(i + 1) * w + j + 1]! + 1 : Math.max(L[(i + 1) * w + j]!, L[i * w + j + 1]!);
    }
  }
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (base[i]!.key === other[j]!.key) {
      ops.push({ op: "same", text: other[j]!.text });
      i++;
      j++;
    } else if (L[(i + 1) * w + j]! >= L[i * w + j + 1]!) {
      ops.push({ op: "del", text: base[i++]!.text });
    } else {
      ops.push({ op: "ins", text: other[j++]!.text });
    }
  }
  while (i < n) ops.push({ op: "del", text: base[i++]!.text });
  while (j < m) ops.push({ op: "ins", text: other[j++]!.text });
  return ops;
}

/** Diff two transcripts and summarise: changed = tokens of base not matched plus tokens of other not matched. */
export function diffText(base: string, other: string, mode: DiffMode) {
  const a = tokenize(base, mode);
  const b = tokenize(other, mode);
  const ops = diffTokens(a, b);
  const same = ops.filter((o) => o.op === "same").length;
  // Share of tokens left unmatched on the longer side: 0 when identical, 1 when nothing lines up.
  const longest = Math.max(a.length, b.length);
  const disagreement = longest ? 1 - same / longest : 0;
  return { ops, baseTokens: a.length, otherTokens: b.length, same, disagreement };
}
