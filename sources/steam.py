"""
Steam catalog → Fangorn: one `sidequest.game.v1` record per game, from FronkonGames'
steam-games-dataset (MIT, https://huggingface.co/datasets/FronkonGames/steam-games-dataset),
streamed to data/games.ndjson. A snapshot: each run is the namespace's whole state.

Kept: games with at least MIN_REVIEWS reviews (what a browser tab can load, and what can be
recommended with any confidence). Dropped before staging, because every published record is
public and permanent: anything tagged or described as sexual content or nudity.
Published: facts and the publisher's short description, never review text.

    python3 sources/steam.py --output-dir .ship/stage/games            # stage only
    python3 sources/steam.py --output-dir … --publish --namespace games --fangorn-bin fangorn
    python3 sources/steam.py --check
"""
import argparse, html, json, os, re, shlex, subprocess, sys

TAG = "sidequest.game.v1"
MIN_REVIEWS = 10
ADULT = re.compile(r"Nudity|Sexual Content|NSFW|Hentai|\bsex|\bnud|porn|erotic", re.I)   # = eval's hard check


def length_of(minutes):
    """Median playtime → a bucket agents can filter on; None where SteamSpy has no data."""
    if not minutes:
        return None
    return "short" if minutes <= 360 else "medium" if minutes <= 1200 else "long"


def rating_of(pos, neg):
    n = pos + neg
    pct = pos / n
    label = ("Overwhelmingly Positive" if pct >= .95 and n >= 500 else "Very Positive" if pct >= .8 and n >= 50 else
             "Positive" if pct >= .8 else "Mostly Positive" if pct >= .7 else "Mixed" if pct >= .4 else
             "Mostly Negative" if pct >= .2 else "Negative")
    return round(pct * 100), label


def record(g):
    pos, neg = g.get("positive") or 0, g.get("negative") or 0
    if pos + neg < MIN_REVIEWS:
        return None
    tags = g.get("tags") or {}
    tags = sorted(tags, key=tags.get, reverse=True)[:15] if isinstance(tags, dict) else list(tags)[:15]
    desc = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", g.get("short_description") or ""))).strip()
    notes = (g.get("notes") or "").strip()
    if ADULT.search(" ".join(tags) + " " + notes + " " + desc):
        return None
    year = (re.search(r"\b(19|20)\d{2}\b", g.get("release_date") or "") or [None])[0]
    pct, label = rating_of(pos, neg)
    length = length_of(g.get("median_playtime_forever"))
    genres = g.get("genres") or []
    dev = ", ".join((g.get("developers") or [])[:2])
    text = (f"{g['name']}. {desc} Tags: {', '.join(tags)}. Genres: {', '.join(genres)}."
            + (f" A {length} game." if length else "") + f" {label} reviews.")
    f = {"entityType": TAG, "game_id": f"steam:{g['appid']}", "appid": g["appid"], "title": g["name"],
         "description": desc, "tags": tags, "genres": genres, "developer": dev, "year": year,
         "released": g.get("release_date"), "length": length, "rating": label, "review_pct": pct,
         "reviews": pos + neg, "price": g.get("price"), "notes": notes or None, "text": text,
         "url": f"https://store.steampowered.com/app/{g['appid']}/"}
    return {"name": f"steam:{g['appid']}", "fields": {k: v for k, v in f.items() if v not in (None, "", [])}}


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="data/games.ndjson")
    p.add_argument("--output-dir", required=True)
    p.add_argument("--publish", action="store_true")
    p.add_argument("--namespace", default="games")
    p.add_argument("--fangorn-bin", default="fangorn")
    a = p.parse_args(argv)
    with open(a.file) as f:
        recs = [r for r in map(lambda l: record(json.loads(l)), f) if r]
    os.makedirs(a.output_dir, exist_ok=True)
    stage = os.path.join(a.output_dir, "volume_1_games.json")
    with open(stage, "w") as f:
        json.dump(recs, f)
    print(f"[steam] {len(recs)} game(s) staged → {stage}")
    if a.publish:
        # ponytail: the by-hand step 6 (commit --replace, push) from the stage dir; untested until go-live
        with open(os.path.join(a.output_dir, "graph.json"), "w") as f:
            json.dump({"vertices": [{"id": r["name"], "tag": TAG, "payload": r["fields"]} for r in recs]}, f)
        cli, run = shlex.split(a.fangorn_bin), lambda *c: subprocess.run([*cli, *c], cwd=a.output_dir, check=True)
        if not os.path.exists(os.path.join(a.output_dir, ".fangorn", "repo.json")):
            run("repo", "init", a.namespace)
        run("commit", "graph.json", "-m", f"steam snapshot: {len(recs)} games", "--replace")
        run("push")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:
        base = {"appid": "1", "name": "X", "positive": 90, "negative": 10, "short_description": "A <b>game</b> &amp; more",
                "tags": {"Indie": 5, "Metroidvania": 9}, "release_date": "Feb 24, 2017", "median_playtime_forever": 300}
        r = record(base)["fields"]
        assert r["tags"] == ["Metroidvania", "Indie"] and r["description"] == "A game & more" and r["year"] == "2017"
        assert r["length"] == "short" and r["review_pct"] == 90 and r["game_id"] == "steam:1"
        assert record({**base, "positive": 5, "negative": 0}) is None, "too few reviews"
        assert record({**base, "tags": {"Nudity": 1}}) is None and record({**base, "notes": "Sexual themes"}) is None
        assert record({**base, "notes": "Frequent violence and gore"}) is not None, "violence is not adult content"
        print("ok"); sys.exit(0)
    main(sys.argv[1:])
