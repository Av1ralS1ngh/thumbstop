"""The hosted app (server.py + thumbstop/hosted.py) runs end to end with no keys and no network, on its built-in
demo show (original fiction, never the real scripts)."""
from __future__ import annotations

import gzip
import io
import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from thumbstop import hosted

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "new_show"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "no-such-profile")   # any live call would fail; recorded runs never make one
    import server

    with TestClient(server.app) as c:
        yield c


def _run(client, body: dict, timeout: float = 120) -> dict:
    r = client.post("/api/run", json=body)
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/jobs/{jid}").json()
        if d["state"] != "running":
            return d | {"job_id": jid}
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def _fixture_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(FIXTURE.glob("*.txt")):
            zf.write(p, p.name)
    return buf.getvalue()


def test_page_and_info(client):
    r = client.get("/")
    assert r.status_code == 200 and "Find the promo" in r.text and "/assets/review.js" in r.text
    assert client.get("/assets/review.js").status_code == 200 and client.get("/health").text == "ok"
    info = client.get("/api/info").json()
    assert info["default"] == "recorded" and [m["id"] for m in info["modes"]] == ["recorded", "offline", "bedrock", "custom"]


def test_recorded_demo_replays_with_no_key_or_network(client):
    d = _run(client, {"mode": "recorded", "source": "demo"})
    assert d["state"] == "done" and d["stage"] == 5, d
    res = d["result"]
    assert res["meta"]["usage"]["calls"] == 0 and res["meta"]["usage"]["cache_hits"] > 0
    assert "opus" in str(res["meta"]["models"])
    view = res["view"]
    assert view["promos"] and all("(rules)" not in p["why"] for p in view["promos"])
    p = view["promos"][0]
    assert p["lines"] and any(x.get("hook") for x in p["lines"]) and p["segments"][0]["in_tc"]
    csv = client.get(f"/api/jobs/{d['job_id']}/files/promos.csv")
    assert csv.status_code == 200 and csv.text.startswith("rank,promo_id")
    page = client.get(f"/api/jobs/{d['job_id']}/files/review.html").text
    assert "ThumbstopReview.mount" in page and 'id="review-data"' in page


def test_offline_demo_run(client):
    d = _run(client, {"mode": "offline", "source": "demo", "settings": {"top": 5, "min": 20, "max": 60, "fast": True}})
    assert d["state"] == "done", d
    assert d["result"]["view"]["promos"] and d["result"]["meta"]["usage"]["calls"] == 0


def test_upload_checks_the_zip_and_recorded_mode_refuses_unrecorded_scripts(client):
    bad = client.post("/api/upload", content=b"not a zip", headers={"X-Filename": "x.zip"})
    assert bad.status_code == 400 and "not a zip" in bad.json()["error"]
    up = client.post("/api/upload", content=_fixture_zip(), headers={"X-Filename": "new show.zip"}).json()
    assert up["episodes"] == len(list(FIXTURE.glob("*.txt")))
    d = _run(client, {"mode": "recorded", "source": "upload", "upload_id": up["upload_id"]})
    assert d["state"] == "error" and d["kind"] == "not_recorded" and "no recorded AI run" in d["error"]
    assert client.post("/api/run", json={"mode": "recorded", "source": "upload", "upload_id": "nope"}).status_code == 400


def test_live_modes_need_credentials(client, monkeypatch):
    import server

    monkeypatch.setattr(server, "_have_aws_login", lambda: False)
    r = client.post("/api/run", json={"mode": "bedrock", "source": "demo", "keys": {}})
    assert r.status_code == 400 and "AWS access keys" in r.json()["error"]
    r = client.post("/api/run", json={"mode": "custom", "source": "demo", "keys": {"base_url": "", "model": ""}})
    assert r.status_code == 400
    r = client.post("/api/run", json={"mode": "offline", "source": "demo", "settings": {"min": 60, "max": 20}})
    assert r.status_code == 400 and "length range" in r.json()["error"]


def test_recorded_mode_reads_an_extra_gz_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_PROFILE", "no-such-profile")
    extra = tmp_path / "extra.json.gz"
    with gzip.open(extra, "wt", encoding="utf-8") as f:
        json.dump(json.loads(hosted.DEMO_ANSWERS.read_text(encoding="utf-8")), f)
    monkeypatch.setattr(hosted, "DEMO_ANSWERS", tmp_path / "missing.json")   # so every answer must come from the gz
    monkeypatch.setattr(hosted, "EXTRA_ANSWERS", str(extra))
    assert hosted.has_extra_answers()
    res = hosted.execute(hosted.RECORDED, {}, hosted.recorded_options(), None, hosted.DEMO_NAME, lambda m: None)
    assert res["doc"]["promos"] and res["doc"]["meta"]["usage"]["calls"] == 0
    with pytest.raises(hosted.NotRecorded, match="no recorded AI run"):
        hosted.execute(hosted.RECORDED, {}, hosted.recorded_options(), _fixture_zip(), "other", lambda m: None)


def test_demo_show_is_valid_kuku_format(tmp_path):
    from thumbstop.parse import parse_show

    show = parse_show(hosted.write_demo_show(tmp_path))
    assert [e.num for e in show.episodes] == [1, 2, 3]
    assert all(70 <= e.duration <= 200 and not e.warnings for e in show.episodes)


def test_zip_extraction_keeps_only_scripts_and_blocks_path_tricks(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("show/episode-001-Episode 1.txt", "x")
        zf.writestr("__MACOSX/show/._episode-001-Episode 1.txt", "junk")
        zf.writestr("show/notes.pdf", "ignored")
        zf.writestr("show/ep02.srt", "1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        zf.writestr("show/ep03.VTT", "WEBVTT\n\n00:01.000 --> 00:02.000\nhi\n")
    assert hosted.episode_files(buf.getvalue()) == 3
    folder = hosted.extract_zip(buf.getvalue(), tmp_path / "a", "My Show")
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == \
        ["show/ep02.srt", "show/ep03.VTT", "show/episode-001-Episode 1.txt"]

    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../../escape.txt", "x")
    with pytest.raises(ValueError, match="unsafe path"):
        hosted.extract_zip(bad.getvalue(), tmp_path / "b", "show")
    assert not (tmp_path / "escape.txt").exists()


def test_scoped_env_restores_variables(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "before")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with hosted.scoped_env({"LLM_API_KEY": "secret", "LLM_MODEL": "m"}):
        assert os.environ["LLM_API_KEY"] == "secret" and os.environ["LLM_MODEL"] == "m"
    assert os.environ["LLM_API_KEY"] == "before" and "LLM_MODEL" not in os.environ
