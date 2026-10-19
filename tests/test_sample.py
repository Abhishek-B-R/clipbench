import pandas as pd

from clipbench.data.manifest import COLUMNS
from clipbench.data.sample import channel_cap, stratified_sample, write_sample


def row(i: int, channel: str, duration: float = 30.0, creator: bool = True) -> dict:
    vid = f"v{i:010d}"
    return {
        "video_id": vid,
        "language": "ja",
        "duration_s": duration,
        "channel": f"name {channel}",
        "channel_id": channel,
        "has_creator_track": creator,
        "has_auto_track": not creator,
        "creator_track_key": "ja" if creator else None,
        "creator_track_path": f"/x/{vid}.ja.json3" if creator else None,
        "auto_track_key": None if creator else "ja-orig",
        "auto_track_path": None,
        "info_path": None,
        "info_source": "clipbench",
    }


def manifest() -> pd.DataFrame:
    rows = []
    i = 0
    # one huge channel, many small ones
    for _ in range(100):
        rows.append(row(i, "UCbig"))
        i += 1
    for c in range(20):
        for _ in range(6):
            rows.append(row(i, f"UCsmall{c:02d}"))
            i += 1
    # ineligible rows
    rows.append(row(i, "UCx", duration=4.0))
    rows.append(row(i + 1, "UCx", duration=91.0))
    rows.append(row(i + 2, "UCx", creator=False))
    rows.append(row(i + 3, "UCx", duration=float("nan")))
    return pd.DataFrame(rows, columns=COLUMNS)


def test_cap_and_size():
    s = stratified_sample(manifest(), n=50, seed=7)
    assert len(s) == 50
    assert channel_cap(50) == 5
    assert s["channel_id"].value_counts().max() <= 5
    assert "UCx" not in set(s["channel_id"])
    assert list(s["sample_rank"]) == list(range(50))
    assert s["video_id"].is_unique


def test_not_enough_takes_what_the_cap_allows():
    m = manifest()
    m = m[m["channel_id"].isin(["UCbig", "UCsmall00", "UCsmall01"])]
    s = stratified_sample(m, n=150, seed=7)
    # cap 15: big gives 15, the two small channels give 6 each
    assert len(s) == 27
    assert s["channel_id"].value_counts()["UCbig"] == 15


def test_deterministic_and_seed_matters():
    m = manifest()
    a = stratified_sample(m, n=40, seed=7)
    b = stratified_sample(m.sample(frac=1, random_state=1), n=40, seed=7)
    c = stratified_sample(m, n=40, seed=8)
    assert list(a["video_id"]) == list(b["video_id"])
    assert list(a["video_id"]) != list(c["video_id"])


def test_write_sample(tmp_settings):
    tmp_settings.data_dir.mkdir(parents=True)
    manifest().to_parquet(tmp_settings.manifest_path, index=False)
    res = write_sample(n=300, seed=7, settings=tmp_settings)
    assert res.path.exists()
    assert "SHORT" in res.message
    back = pd.read_parquet(res.path)
    assert "sample_rank" in back.columns and len(back) == len(res.df)


def window_row(i: int, channel: str) -> dict:
    r = row(i, channel, duration=40.0)
    yt = r["video_id"]
    r.update(
        video_id=f"{yt}@100-140",
        clip_id=f"{yt}@100-140",
        youtube_id=yt,
        clip_source="window",
        window_start_s=100.0,
        window_end_s=140.0,
    )
    return r


def test_shorts_first_then_windows_with_shared_cap():
    rows = [dict(row(i, f"UCs{i % 3}"), clip_source="short") for i in range(9)]
    rows += [window_row(100 + i, f"UCs{i % 3}") for i in range(30)]
    rows += [window_row(200 + i, f"UCw{i % 20}") for i in range(40)]
    rows.append(dict(row(999, "UCs0", duration=300.0), clip_source="long"))
    m = pd.DataFrame(rows)
    s = stratified_sample(m, n=60, seed=7)
    assert len(s) == 58  # 3 channels x cap 6 + 20 channels x 2 windows
    assert set(s.head(9)["clip_source"]) == {"short"}  # every Short, first
    assert (s["clip_source"] == "short").sum() == 9
    assert s["channel_id"].value_counts().max() <= 6  # cap counts Shorts and windows together
    assert "long" not in set(s["clip_source"])
    only = stratified_sample(m, n=60, seed=7, shorts_only=True)
    assert len(only) == 9 and set(only["clip_source"]) == {"short"}


def test_clip_meta(tmp_settings):
    from clipbench.data.sample import clip_meta

    tmp_settings.data_dir.mkdir(parents=True)
    m = pd.DataFrame([dict(row(1, "UCa"), clip_source="short", youtube_id="v0000000001"), window_row(2, "UCb")])
    m["sample_rank"] = range(2)
    m.to_parquet(tmp_settings.sample_path, index=False)
    meta = clip_meta(tmp_settings)
    assert meta["v0000000001"]["kind"] == "short"
    assert meta["v0000000001"]["youtube_url"] == "https://www.youtube.com/shorts/v0000000001"
    w = meta["v0000000002@100-140"]
    assert w["kind"] == "window" and w["start_s"] == 100.0 and w["youtube_id"] == "v0000000002"
    assert w["youtube_url"] == "https://www.youtube.com/watch?v=v0000000002&t=100s"
