"""Build karatap/data/kanji_readings.txt from KANJIDIC2.

    .venv/Scripts/python packaging/build_kanji_readings.py path/to/kanjidic2.xml.gz

KANJIDIC2 comes from the Electronic Dictionary Research and Development
Group (https://www.edrdg.org/kanjidic/kanjidic2.xml.gz) under CC BY-SA 4.0.
Only the readings are kept: on'yomi converted to hiragana, kun'yomi with the
okurigana after a dot (あつ.い) and without the prefix/suffix dashes.
"""
import gzip
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "karatap" / "data" / "kanji_readings.txt"

HEADER = """\
# Kanji readings for splitting furigana per kanji (karatap).
# Derived from KANJIDIC2 by the Electronic Dictionary Research and Development Group,
# https://www.edrdg.org/edrdg/licence.html - licensed under CC BY-SA 4.0.
# Format: kanji<TAB>readings separated by spaces (hiragana; okurigana after a dot: かた.る).
# KANJIDIC2 database version {version}, created {date}.
"""


def to_hiragana(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def main() -> int:
    src = Path(sys.argv[1])
    entries: list[tuple[str, list[str]]] = []
    version = date = "?"
    with gzip.open(src, "rb") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == "database_version":
                version = el.text or "?"
            elif el.tag == "date_of_creation":
                date = el.text or "?"
            elif el.tag == "character":
                literal = el.findtext("literal")
                readings: list[str] = []
                for r in el.iter("reading"):
                    kind = r.get("r_type")
                    if kind not in ("ja_on", "ja_kun") or not r.text:
                        continue
                    text = to_hiragana(r.text).replace("-", "")
                    if text and text not in readings:
                        readings.append(text)
                if literal and readings:
                    entries.append((literal, readings))
                el.clear()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as out:
        out.write(HEADER.format(version=version, date=date))
        for literal, readings in entries:
            out.write(f"{literal}\t{' '.join(readings)}\n")
    print(f"{len(entries)} kanji -> {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
