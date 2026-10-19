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
