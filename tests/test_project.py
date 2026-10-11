import codecs
from pathlib import Path

from karatap.ass_doc import AssDocument
from karatap.project import new_project_bytes

TEMPLATE_LINES = [
    "[Script Info]",
    "Title: old song",
    "ScriptType: v4.00+",
    "PlayResX: 1920",
    "",
    "[Aegisub Project Garbage]",
    "Audio File: old.mp4",
    "Video File: old.mp4",
    "Video AR Mode: 4",
    "Active Line: 12",
    "",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    "Style: Default,Arial,68,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,25,25,25,1",
    "Style: Odd,Arial,68,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,1,25,25,165,1",
    "Style: Even,Arial,68,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,3,25,25,0,1",
    "",
    "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    "Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,code once,x = 1",
    "Comment: 0,0:00:00.00,0:00:00.00,Odd,,0,0,0,template noblank syl,{\\an5\\pos($center,$middle)}",
    "Comment: 0,0:00:05.00,0:00:07.00,Odd,,0,0,0,karaoke,{\\k50}あ{\\k150}い",
    "Dialogue: 0,0:00:05.00,0:00:07.00,Odd,,0,0,0,fx,{\\an5}あ",
    "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,作詞：someone",
]


def template(lines=TEMPLATE_LINES, eol="\r\n", bom=codecs.BOM_UTF8):
    return AssDocument(bom + (eol.join(lines) + eol).encode("utf-8"))


def test_keeps_template_lines_and_points_at_new_media(tmp_path):
    out = new_project_bytes(template(), tmp_path / "media" / "new.mp4", tmp_path / "new.ass",
                            ["{\\k1}空|<そら", "{\\k1}ラ{\\k1}ラ"])
    assert out.startswith(codecs.BOM_UTF8)
    text = out[3:].decode("utf-8")
    assert "\n" not in text.replace("\r\n", "")
    lines = text.split("\r\n")
    assert lines[:6] == TEMPLATE_LINES[:6]
    assert lines[6:9] == ["Audio File: media\\new.mp4" if "\\" in lines[6] else "Audio File: media/new.mp4",
                          lines[7], "Video AR Mode: 4"]
    assert lines[7].startswith("Video File: media")
    assert "Active Line: 12" not in lines
    assert TEMPLATE_LINES[11:21] == lines[10:20]          # styles, events format, code + template
    assert lines[20:] == [
        "Comment: 0,0:00:00.00,0:00:00.00,Odd,,0,0,0,karaoke,{\\k1}空|<そら",
        "Comment: 0,0:00:00.00,0:00:00.00,Odd,,0,0,0,karaoke,{\\k1}ラ{\\k1}ラ",
        "",
    ]
    doc = AssDocument(out)
    assert [e.text for e in doc.karaoke_events()] == ["{\\k1}空|<そら", "{\\k1}ラ{\\k1}ラ"]


def test_adds_the_project_section_when_missing(tmp_path):
    lines = TEMPLATE_LINES[:5] + TEMPLATE_LINES[11:]
    out = new_project_bytes(template(lines, eol="\n", bom=b""), tmp_path / "song.mp3",
                            tmp_path / "song.ass", ["{\\k1}あ"])
    text = out.decode("utf-8").split("\n")
    i = text.index("[Aegisub Project Garbage]")
    assert text[i - 2:i + 3] == ["PlayResX: 1920", "", "[Aegisub Project Garbage]",
                                 "Audio File: song.mp3", ""]
    assert text[i + 3] == "[V4+ Styles]"


def test_style_falls_back_to_default(tmp_path):
    lines = [l for l in TEMPLATE_LINES if not l.startswith(("Style: Odd", "Style: Even"))]
    out = new_project_bytes(template(lines), tmp_path / "a.mp4", tmp_path / "a.ass", ["{\\k1}あ"])
    assert AssDocument(out).karaoke_events()[0].style == "Default"
    assert AssDocument(out).style_names() == ["Default"]


def test_relative_path_uses_the_ass_folder(tmp_path):
    out = new_project_bytes(template(), tmp_path / "a.mp4", tmp_path / "sub" / "a.ass", ["{\\k1}あ"])
    ref = AssDocument(out).section_value("Aegisub Project Garbage", "Audio File")
    assert Path(tmp_path / "sub" / ref).resolve() == (tmp_path / "a.mp4").resolve()
