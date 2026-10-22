"""Open Graph images (1200x630 PNG) for the web app, written at export time into web/public/og/.

- og/home.png: the positioning line and per-language progress (never results unless published).
- og/<lang>.png: the language page. Published: verified clips, providers and the top finding.
  Unpublished: the positioning line and progress.
- og/<lang>/<slug>.png: one per finding in findings/<lang>.json, with its one-line result.

Look: the site's "lab notebook" style. Warm paper, ink, a hairline frame and one vermilion accent. SVG is
rendered to PNG with vl-convert (already a dependency). Fonts: Gloock for the headline when
web/node_modules/@fontsource/gloock is installed (its .woff is unpacked to a TTF in a temp dir, since the
renderer reads only TTF/OTF); Mona Sans and JetBrains Mono ship only as woff2, so body and labels fall back
to system sans and mono fonts. No caption text is ever drawn: only numbers, provider ids and labels.
"""

from __future__ import annotations

import struct
import tempfile
import textwrap
import zlib
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

from clipbench.report import rules
from clipbench.report.findings import LANGUAGE_NAMES

W, H = 1200, 630
PAPER = "#f7f4ee"
INK = "#1d1a16"
MUTED = "#6b665d"
LINE = "#d8d2c6"
SIGNAL = "#d9462b"  # vermilion, the one accent
REPO = "github.com/Abhishek-B-R/clipbench"
POSITIONING = "Which speech-to-text should your video app use?"
POSITIONING_SUB = (
    "The independent benchmark for captions on real short-form video: accuracy, word timing, "
    "hallucination over music, and cost, in six languages."
)
SERIF = "Gloock, 'New York', Georgia, serif"
SANS = "'Mona Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', Menlo, Monaco, 'Courier New', monospace"


# ---------------------------------------------------------------- fonts


def woff_to_sfnt(data: bytes) -> bytes:
    """Unpack a WOFF 1.0 font into a plain TTF/OTF (zlib per table). Raises ValueError on anything else."""
    if data[:4] != b"wOFF":
        raise ValueError("not a WOFF 1.0 file")
    flavor, _length, num_tables = struct.unpack(">IIH", data[4:14])
    entries = []
    for i in range(num_tables):
        tag, off, comp, orig, checksum = struct.unpack(">4sIIII", data[44 + 20 * i : 64 + 20 * i])
        raw = data[off : off + comp]
        entries.append((tag, checksum, zlib.decompress(raw) if comp < orig else raw))
    entries.sort(key=lambda e: e[0])
    pow2 = 1
    while pow2 * 2 <= num_tables:
        pow2 *= 2
    search = pow2 * 16
    head = struct.pack(">IHHHH", flavor, num_tables, search, pow2.bit_length() - 1, num_tables * 16 - search)
    offset = 12 + 16 * num_tables
    records, body = b"", b""
    for tag, checksum, table in entries:
        records += struct.pack(">4sIII", tag, checksum, offset + len(body), len(table))
        body += table + b"\0" * (-len(table) % 4)
    return head + records + body


@lru_cache(maxsize=1)
def font_dir(web_root: str) -> str | None:
    """Temp dir with Gloock as TTF, registered with vl-convert. None when the font is not installed."""
    src = Path(web_root) / "node_modules" / "@fontsource" / "gloock" / "files"
    woffs = sorted(src.glob("gloock-latin-400-normal.woff")) if src.exists() else []
    if not woffs:
        return None
    out = Path(tempfile.mkdtemp(prefix="clipbench-og-fonts-"))
    try:
        (out / "Gloock-Regular.ttf").write_bytes(woff_to_sfnt(woffs[0].read_bytes()))
        import vl_convert as vlc

        vlc.register_font_directory(str(out))
    except Exception:  # noqa: BLE001 - fall back to system fonts
        return None
    return str(out)


# ---------------------------------------------------------------- drawing


def _wrap(text: str, size: int, width: int = W - 2 * 96, factor: float = 0.47) -> list[str]:
    per_line = max(10, int(width / (size * factor)))
    return textwrap.wrap(text, per_line) or [""]


def _fit(text: str, sizes=(64, 56, 48, 42, 36, 32), max_lines: int = 4) -> tuple[int, list[str]]:
    for size in sizes:
        lines = _wrap(text, size)
        if len(lines) <= max_lines:
            return size, lines
    size = sizes[-1]
    lines = _wrap(text, size)[:max_lines]
    lines[-1] = lines[-1].rstrip(".,;: ") + "..."
    return size, lines


def _text_block(lines: list[str], x: int, y: int, size: int, family: str, fill: str, lh: float = 1.18) -> str:
    out = []
    for i, line in enumerate(lines):
        out.append(
            f'<text x="{x}" y="{y + i * size * lh:.0f}" font-family="{escape(family)}" font-size="{size}" '
            f'fill="{fill}">{escape(line)}</text>'
        )
    return "".join(out)


def card(
    eyebrow: str,
    headline: str,
    sub: str = "",
    footer_right: str = "",
    progress: float | None = None,
    note: str = "",
) -> str:
    size, lines = _fit(headline)
    top = 220 if len(lines) <= 2 else 190
    head_svg = _text_block(lines, 96, top, size, SERIF, INK)
    sub_y = top + len(lines) * size * 1.18 + 24
    sub_svg = (
        _text_block(_wrap(sub, 26, factor=0.5)[:3], 96, int(sub_y), 26, SANS, MUTED, 1.35) if sub else ""
    )
    bar = ""
    if progress is not None:
        frac = max(0.0, min(1.0, progress))
        bar = (
            f'<rect x="96" y="500" width="1008" height="6" rx="3" fill="{LINE}"/>'
            f'<rect x="96" y="500" width="{1008 * frac:.0f}" height="6" rx="3" fill="{SIGNAL}"/>'
        )
    if note:
        bar += f'<text x="96" y="510" font-family="{escape(MONO)}" font-size="20" fill="{INK}">{escape(note)}</text>'
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<rect width="{W}" height="{H}" fill="{PAPER}"/>
<rect x="32.5" y="32.5" width="{W - 65}" height="{H - 65}" fill="none" stroke="{LINE}" stroke-width="1"/>
<circle cx="102" cy="108" r="6" fill="{SIGNAL}"/>
<text x="120" y="114" font-family="{escape(MONO)}" font-size="20" fill="{MUTED}" letter-spacing="1">{escape(eyebrow.upper())}</text>
{head_svg}
{sub_svg}
{bar}
<line x1="96" y1="540" x2="{W - 96}" y2="540" stroke="{LINE}" stroke-width="1"/>
<text x="96" y="578" font-family="{escape(MONO)}" font-size="20" fill="{INK}">clipbench</text>
<text x="226" y="578" font-family="{escape(MONO)}" font-size="20" fill="{MUTED}">{escape(REPO)}</text>
<text x="{W - 96}" y="578" text-anchor="end" font-family="{escape(MONO)}" font-size="20" fill="{MUTED}">{escape(footer_right)}</text>
</svg>"""


def render_png(svg: str, path: Path) -> Path:
    import vl_convert as vlc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(vlc.svg_to_png(svg, scale=1.0))
    return path


# ---------------------------------------------------------------- pages


def _lang_name(lang: str) -> str:
    return LANGUAGE_NAMES.get(lang, lang)


def home_svg(languages: list[dict[str, Any]]) -> str:
    published = [lang for lang in languages if lang.get("published")]
    progress = "   ".join(
        f"{lang['lang']} {int(lang.get('verified_clips') or 0)}/{rules.MIN_VERIFIED_CLIPS}"
        for lang in languages
    )
    if published:
        sub = (
            "Published: " + ", ".join(_lang_name(lang["lang"]) for lang in published) + ". " + POSITIONING_SUB
        )
    else:
        sub = POSITIONING_SUB
    return card("speech-to-text for captions", POSITIONING, sub, note=f"clips verified  {progress}")


def language_svg(lang: str, status: dict[str, Any], top_finding: dict | None) -> str:
    name = _lang_name(lang)
    v = int(status.get("verified_clips") or 0)
    p = int(status.get("providers_ok") or 0)
    if status.get("published"):
        headline = top_finding["text"] if top_finding else f"{name}: {v} verified clips, {p} providers."
        sub = (
            f"{v} verified clips, {p} providers, {int(status.get('channels') or 0)} channels. Draft findings."
        )
        return card(f"{name} / results", headline, sub, footer_right=lang)
    headline = f"{name}: {v} of {rules.MIN_VERIFIED_CLIPS} clips verified"
    sub = (
        f"First results once {rules.MIN_VERIFIED_CLIPS} clips are verified and {rules.MIN_PROVIDERS_OK} providers "
        f"have run ({p} so far). {POSITIONING}"
    )
    return card(
        f"{name} / in progress", headline, sub, footer_right=lang, progress=v / rules.MIN_VERIFIED_CLIPS
    )


def _finding_label(finding: dict[str, Any]) -> str:
    source = finding.get("source") or {}
    if source.get("table") == "paired":
        return "paired test"
    return (source.get("filters") or {}).get("sort") or finding.get("kind") or "result"


def finding_svg(lang: str, finding: dict[str, Any]) -> str:
    return card(
        f"{_lang_name(lang)} / finding / {_finding_label(finding)}",
        finding["text"],
        "Draft: verify before quoting. Source and method on the findings page.",
        footer_right=f"{lang} / {finding['id']}",
    )


def write_all(
    data, findings_doc: dict[str, Any], languages: list[dict[str, Any]], web_root: Path
) -> list[Path]:
    """Write home, this run's language page and one image per finding. Stale finding images of this
    language are removed so a finding that no longer holds does not keep its image."""
    font_dir(str(web_root))
    og = web_root / "public" / "og"
    written = [render_png(home_svg(languages), og / "home.png")]
    status = next(
        (lang for lang in languages if lang["lang"] == data.language), findings_doc.get("status") or {}
    )
    items = findings_doc.get("findings") or []
    written.append(render_png(language_svg(data.language, status, items[0] if items else None),
                              og / f"{data.language}.png"))  # fmt: skip
    for lang in languages:
        path = og / f"{lang['lang']}.png"
        if lang["lang"] != data.language and not path.exists():
            written.append(render_png(language_svg(lang["lang"], lang, None), path))
    fdir = og / data.language
    keep = set()
    for f in items:
        path = fdir / f"{f['slug']}.png"
        keep.add(path.name)
        written.append(render_png(finding_svg(data.language, f), path))
    if fdir.exists():
        for old in fdir.glob("*.png"):
            if old.name not in keep:
                old.unlink()
    return written
