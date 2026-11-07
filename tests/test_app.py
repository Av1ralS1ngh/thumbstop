"""The hosted app runs end to end offline on its built-in demo show (no keys, no network, no real scripts)."""
from __future__ import annotations

import importlib.util
import io
import os
import zipfile
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app.py"


def _load_app_module():
    spec = importlib.util.spec_from_file_location("thumbstop_app", APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # main() only runs as __main__, so this just defines the helpers
    return mod


def _run_demo(mode_prefix: str):
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception
    radio = at.sidebar.radio(key="mode")
    radio.set_value(next(o for o in radio.options if o.startswith(mode_prefix))).run()
    assert at.radio(key="source").value.startswith("Demo")
    at.button(key="run").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    return at


def test_recorded_ai_demo_replays_with_no_key_or_network(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_PROFILE", "no-such-profile")   # any live call would fail; the budget is 0 anyway
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert at.sidebar.radio(key="mode").value.startswith("Recorded")   # the default a visitor sees
    at = _run_demo("Recorded")
    doc = at.session_state["result"]["doc"]
    assert doc["promos"] and doc["meta"]["usage"]["calls"] == 0 and doc["meta"]["usage"]["cache_hits"] > 0
    assert "opus" in str(doc["meta"]["models"]) and all("(rules)" not in (r["why_it_works"] or "") for r in doc["promos"])


def test_recorded_mode_reads_an_extra_gz_file_and_refuses_unrecorded_scripts(monkeypatch, tmp_path):
    import gzip
    import json

    monkeypatch.setenv("AWS_PROFILE", "no-such-profile")
    app = _load_app_module()
    extra = tmp_path / "extra.json.gz"
    with gzip.open(extra, "wt", encoding="utf-8") as f:
        json.dump(json.loads(app.DEMO_ANSWERS.read_text(encoding="utf-8")), f)
    monkeypatch.setattr(app, "DEMO_ANSWERS", tmp_path / "missing.json")   # so every answer must come from the gz
    monkeypatch.setattr(app, "EXTRA_ANSWERS", str(extra))
    assert app.has_extra_answers()
    res = app.execute(app.RECORDED, {}, app.recorded_options(), None, app.DEMO_NAME, lambda m: None)
    assert res["doc"]["promos"] and res["doc"]["meta"]["usage"]["calls"] == 0

    buf = io.BytesIO()   # scripts with no recorded answers: a clear message, never a live call or a rules run
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted((APP.parent / "tests" / "fixtures" / "new_show").glob("*.txt")):
            zf.write(p, p.name)
    with pytest.raises(app.NotRecorded, match="no recorded AI run"):
        app.execute(app.RECORDED, {}, app.recorded_options(), buf.getvalue(), "other", lambda m: None)


def test_offline_demo_run_shows_ranked_table():
    at = _run_demo("Offline")
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    ranked = [df.value for df in at.dataframe if "Cliffhanger" in df.value.columns]
    assert ranked and len(ranked[0]) >= 1
    assert {"Rank", "Segments", "Length (s)", "Hook", "Why it works", "Cold viewer", "Policy"} <= set(ranked[0].columns)
    assert len([e for e in at.main.expander if e.label.startswith("#")]) == len(ranked[0])   # a cut sheet per promo
    assert len(at.get("download_button")) == 4
    assert "result" in at.session_state


def test_demo_show_is_valid_kuku_format(tmp_path):
    from thumbstop.parse import parse_show

    app = _load_app_module()
    show = parse_show(app.write_demo_show(tmp_path))
    assert [e.num for e in show.episodes] == [1, 2, 3]
    assert all(70 <= e.duration <= 200 and not e.warnings for e in show.episodes)


def test_zip_extraction_keeps_only_scripts_and_blocks_path_tricks(tmp_path):
    app = _load_app_module()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("show/episode-001-Episode 1.txt", "x")
        zf.writestr("__MACOSX/show/._episode-001-Episode 1.txt", "junk")
        zf.writestr("show/notes.pdf", "ignored")
        zf.writestr("show/ep02.srt", "1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        zf.writestr("show/ep03.VTT", "WEBVTT\n\n00:01.000 --> 00:02.000\nhi\n")
    folder = app.extract_zip(buf.getvalue(), tmp_path / "a", "My Show")
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == \
        ["show/ep02.srt", "show/ep03.VTT", "show/episode-001-Episode 1.txt"]

    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../../escape.txt", "x")
    with pytest.raises(ValueError, match="unsafe path"):
        app.extract_zip(bad.getvalue(), tmp_path / "b", "show")
    assert not (tmp_path / "escape.txt").exists()


def test_scoped_env_restores_variables(monkeypatch):
    app = _load_app_module()
    monkeypatch.setenv("LLM_API_KEY", "before")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with app.scoped_env({"LLM_API_KEY": "secret", "LLM_MODEL": "m"}):
        assert os.environ["LLM_API_KEY"] == "secret" and os.environ["LLM_MODEL"] == "m"
    assert os.environ["LLM_API_KEY"] == "before" and "LLM_MODEL" not in os.environ
