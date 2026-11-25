import subprocess
from pathlib import Path

import pytest

from app.services import boundaries, detector, sources, subtitles, tiktok
from app import security


def lines(*spans):
    return [{"t_start": a, "t_end": b, "text": f"frase {i} con palabras distintas {i}"} for i, (a, b) in enumerate(spans)]


L = lines((0, 3), (3.5, 7), (7.5, 12), (12.4, 16), (16.5, 20), (21, 25), (26, 30), (31, 40), (41, 60), (61, 70))


def test_refine_snaps_to_sentence_and_adds_context():
    b = boundaries.refine(8.0, 14.0, L, 0, 100, before=4, after=2, min_len=5, max_len=90)
    assert b.peak_start == 7.5 and b.peak_end == 16       # frases completas
    assert b.clip_start <= 3.5 and b.clip_end >= 16.5     # contexto sin cortar frases
    assert b.clip_start == pytest.approx(3.25) and b.clip_end == pytest.approx(20.25)


def test_refine_extends_to_min_length_and_caps_max():
    b = boundaries.refine(21.5, 24.0, L, 0, 100, before=1, after=1, min_len=15, max_len=90)
    assert b.duration >= 15
    b = boundaries.refine(0.5, 69, L, 0, 100, before=0, after=0, min_len=5, max_len=30)
    assert b.duration <= 30
    assert any(abs(b.clip_end - (ln["t_end"] + boundaries.GAP_PAD)) < 1e-6 for ln in L)


def test_refine_respects_available_buffer():
    b = boundaries.refine(4, 10, L, 5.0, 11.0, before=4, after=4, min_len=1, max_len=90)
    assert b.clip_start >= 5.0 and b.clip_end <= 11.0


def test_duplicates():
    ex = [{"start": 100, "end": 130, "transcript": "no me puedo creer lo que acaba de decir el invitado"}]
    assert boundaries.is_duplicate({"start": 110, "end": 140, "transcript": "x"}, ex)
    assert boundaries.is_duplicate({"start": 400, "end": 420, "transcript": "no me puedo creer lo que acaba de decir el invitado hoy"}, ex)
    assert not boundaries.is_duplicate({"start": 400, "end": 420, "transcript": "hablemos ahora de fútbol y del partido"}, ex)


def test_subtitle_cues_and_ass():
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35} for i, w in enumerate("Hola, esto es una prueba de subtítulos muy largos {raro}".split())]
    cues = subtitles.words_to_cues(words)
    assert all(len(c["words"]) <= subtitles.MAX_WORDS for c in cues)
    assert cues[0]["text"] == "Hola,"  # corta tras la coma
    ass = subtitles.to_ass(cues, "highlight")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "{raro}" not in ass  # llaves escapadas
    edited = subtitles.validate_cues([{"start": 1, "end": 3, "text": "texto nuevo editado"}], 10)
    assert [w["w"] for w in edited[0]["words"]] == ["texto", "nuevo", "editado"]
    assert edited[0]["words"][-1]["e"] == pytest.approx(3)


def test_youtube_ids():
    for url in ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ",
                "https://www.youtube.com/live/dQw4w9WgXcQ?si=x", "m.youtube.com/watch?v=dQw4w9WgXcQ&t=1"]:
        assert sources.youtube_id(url) == "dQw4w9WgXcQ"
    assert sources.classify("https://cdn.example.com/live.m3u8") == ("hls", None)
    assert sources.classify("rtmp://server/live/key")[0] == "hls"
    with pytest.raises(sources.SourceError):
        sources.classify("ftp://x")


def test_hashtags_clean():
    assert detector.clean_hashtags(["#Humor", "humor", "Viral TikTok", "#", "ñandú"]) == ["humor", "viraltiktok", "ñandú"]


def test_transcript_block_annotations():
    block = detector.build_transcript_block(
        [{"t_start": 105, "t_end": 108, "text": "¡No puede ser!", "loud_spike": True, "scene_change": True}], 100)
    assert "[VOLUMEN ALTO]" in block and "[CAMBIO DE ESCENA]" in block and "5.0s" in block


def test_session_token_and_encryption():
    tok = security.make_session_token()
    assert security.verify_session_token(tok)
    assert not security.verify_session_token(tok[:-1] + ("0" if tok[-1] != "0" else "1"))
    assert security.decrypt(security.encrypt("secreto")) == "secreto"


def test_tiktok_chunk_plan():
    assert tiktok._chunk_plan(20 * 1024 * 1024) == (20 * 1024 * 1024, 1)
    size = 150 * 1024 * 1024 + 123
    chunk, count = tiktok._chunk_plan(size)
    assert chunk == 10 * 1024 * 1024 and count == 15
    assert size - chunk * (count - 1) <= 128 * 1024 * 1024


def test_render_produces_tiktok_format(tmp_path):
    from app.services import media, render
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=6",
                    "-f", "lavfi", "-i", "sine=f=440:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(src)], check=True)
    cues = subtitles.words_to_cues([{"w": "hola", "s": 1, "e": 1.5}, {"w": "mundo", "s": 1.6, "e": 2.2}])
    res = render.render_clip(src, tmp_path / "o.mp4", tmp_path / "o.jpg", trim_start=1, trim_end=1, layout="auto",
                             cues=cues, subtitle_style="highlight", thumb_at=3)
    info = media.video_info(tmp_path / "o.mp4")
    assert (info["width"], info["height"]) == (1080, 1920) and info["has_audio"]
    assert 3.8 < res["duration"] < 4.3
    assert (tmp_path / "o.jpg").exists()
