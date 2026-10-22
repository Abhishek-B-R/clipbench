/** /try helpers: WAV encoder and resampling, the caption rule port (checked against captions.py output), the diff, NDJSON. */
import { describe, expect, it } from "vitest";
import { buildCues, splitLines, toSrt, toVtt, type Word } from "@/try/captions";
import { diffMode, diffText, tokenize } from "@/try/diff";
import { ndjson } from "@/try/api";
import { downmix, encodeWav, peaks, resampledLength, resampleLinear, TARGET_RATE } from "@/try/wav";

const timed = (tokens: string[], pauseBefore: number | null = null): Word[] => {
  let t = 0;
  return tokens.map((text, i) => {
    if (i === pauseBefore) t += 900;
    const w = { text, start_ms: t, end_ms: t + 250 };
    t += 300;
    return w;
  });
};

describe("WAV encoder", () => {
  it("writes a 16 kHz mono 16-bit PCM header the Worker accepts", () => {
    const buf = encodeWav(new Float32Array(16_000), TARGET_RATE);
    const v = new DataView(buf);
    const tag = (at: number) => String.fromCharCode(...new Uint8Array(buf, at, 4));
    expect(tag(0)).toBe("RIFF");
    expect(tag(8)).toBe("WAVE");
    expect(tag(12)).toBe("fmt ");
    expect(v.getUint32(4, true)).toBe(36 + 32_000);
    expect(v.getUint16(20, true)).toBe(1); // PCM
    expect(v.getUint16(22, true)).toBe(1); // mono
    expect(v.getUint32(24, true)).toBe(16_000);
    expect(v.getUint32(28, true)).toBe(32_000); // byte rate
    expect(v.getUint16(32, true)).toBe(2); // block align
    expect(v.getUint16(34, true)).toBe(16);
    expect(tag(36)).toBe("data");
    expect(v.getUint32(40, true)).toBe(32_000);
    expect(buf.byteLength).toBe(44 + 32_000);
  });

  it("clips and scales samples to int16", () => {
    const v = new DataView(encodeWav(Float32Array.from([1, -1, 2, -2, 0, 0.5])));
    expect([0, 1, 2, 3, 4, 5].map((i) => v.getInt16(44 + i * 2, true))).toEqual([32767, -32768, 32767, -32768, 0, 16383]);
  });

  it("a minute at 16 kHz fits the Worker's 2.5 MB limit", () => {
    expect(encodeWav(new Float32Array(60 * TARGET_RATE)).byteLength).toBeLessThan(2.5 * 1024 * 1024);
  });
});

describe("resampling", () => {
  it("lengths follow the rate ratio", () => {
    expect(resampledLength(48_000, 48_000)).toBe(16_000);
    expect(resampledLength(44_100 * 3, 44_100)).toBe(48_000);
    expect(resampledLength(441, 44_100)).toBe(160);
    expect(resampleLinear(new Float32Array(44_100 * 2), 44_100)).toHaveLength(32_000);
    expect(resampleLinear(new Float32Array(16_000), 16_000)).toHaveLength(16_000);
  });

  it("keeps a slow signal's shape and downmixes channels", () => {
    const ramp = Float32Array.from({ length: 48 }, (_, i) => i / 47);
    const out = resampleLinear(ramp, 48_000);
    expect(out[0]).toBeCloseTo(0);
    expect(out[8]!).toBeCloseTo(24 / 47, 5);
    expect([...downmix([Float32Array.from([1, 0]), Float32Array.from([0, 1])])]).toEqual([0.5, 0.5]);
    const p = peaks(Float32Array.from([0, 1, -1, 0.5]), 2);
    expect([...p.max]).toEqual([1, 0.5]);
    expect([...p.min]).toEqual([0, -1]);
  });
});

describe("captions: the benchmark's line-break rule (same output as clipbench/scoring/captions.py)", () => {
  it("English SRT", () => {
    const words = timed("Hello there, this is a fairly long sentence that should wrap onto two lines nicely. Then a pause comes and a new cue starts here".split(" "), 22);
    expect(toSrt(buildCues(words, "en"))).toBe(
      "1\n00:00:00,000 --> 00:00:04,450\nHello there, this is a fairly long sentence\nthat should wrap onto two lines nicely.\n\n2\n00:00:04,500 --> 00:00:06,550\nThen a pause comes and a new\n\n3\n00:00:07,500 --> 00:00:08,350\ncue starts here\n",
    );
  });

  it("Japanese VTT joins without spaces", () => {
    const words = timed(["これは", "日本語", "の", "字幕", "テスト", "です。", "次の", "文は", "少し", "長めに", "書いて", "あります"]);
    expect(toVtt(buildCues(words, "ja"))).toBe(
      "WEBVTT\n\n00:00:00.000 --> 00:00:01.750\nこれは日本語の字幕テストです。\n\n00:00:01.800 --> 00:00:03.550\n次の文は少し長めに書いてあります\n",
    );
  });

  it("cues record where line one ends, and empty input is empty", () => {
    const cues = buildCues(timed("Hello there, this is a fairly long sentence that should wrap onto two lines nicely.".split(" ")), "en");
    expect(cues[0]).toMatchObject({ first: 0, count: 15, firstLine: 8 });
    expect(toSrt([])).toBe("");
    expect(toVtt([])).toBe("WEBVTT\n\n");
    expect(splitLines(["short"], "en")).toEqual(["short"]);
  });

  it("timestamps carry hours and milliseconds", () => {
    expect(toSrt([{ start_ms: 3_723_004, end_ms: 3_724_500, lines: ["x"], first: 0, count: 1, firstLine: 1 }])).toBe("1\n01:02:03,004 --> 01:02:04,500\nx\n");
  });
});

describe("diff against a baseline", () => {
  it("English compares words, ignoring case and punctuation", () => {
    expect(diffMode("en")).toBe("word");
    const d = diffText("Hello, world. This is clipbench.", "hello world this was clipbench", "word");
    expect(d.ops).toEqual([
      { op: "same", text: "hello" },
      { op: "same", text: "world" },
      { op: "same", text: "this" },
      { op: "del", text: "is" },
      { op: "ins", text: "was" },
      { op: "same", text: "clipbench" },
    ]);
    expect(d.disagreement).toBeCloseTo(1 / 5);
    expect(diffText("Same text.", "same text", "word").disagreement).toBe(0);
  });

  it("Japanese compares characters", () => {
    expect(diffMode("ja")).toBe("char");
    expect(diffMode("ko")).toBe("char");
    expect(tokenize("字幕、です。", "char").map((t) => t.text)).toEqual(["字", "幕", "で", "す"]);
    const d = diffText("今日は晴れです。", "今日は雨です", "char");
    expect(d.ops.map((o) => `${o.op}:${o.text}`)).toEqual(["same:今", "same:日", "same:は", "del:晴", "del:れ", "ins:雨", "same:で", "same:す"]);
    expect(d.same).toBe(5);
    expect(d.disagreement).toBeCloseTo(2 / 7);
  });
});

describe("NDJSON stream", () => {
  it("parses lines split across chunks", async () => {
    const enc = new TextEncoder();
    const parts = ['{"type":"start"', ',"seconds":1}\n{"type":"res', 'ult","provider":"a"}\n', '{"type":"done","cost_usd":0}'];
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (const p of parts) c.enqueue(enc.encode(p));
        c.close();
      },
    });
    const out: unknown[] = [];
    for await (const l of ndjson(body)) out.push(l);
    expect(out).toEqual([{ type: "start", seconds: 1 }, { type: "result", provider: "a" }, { type: "done", cost_usd: 0 }]);
  });
});
