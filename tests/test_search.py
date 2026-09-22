from search_app.schema import coerce, passage_document, passage_from_legacy
from search_app.chunking import chunk_cues, chunk_transcript
from search_app.srt import Cue, parse_srt_cues, srt_to_text
from search_app.share import (
    apple_listen_url,
    primary_share_url,
    spotify_episode_id_from_transcript_url,
    spotify_listen_url,
    youtube_listen_url,
)
from search_app.search import (
    FUZZY,
    group_passages,
    highlight_html,
    lexical_pipeline,
    levenshtein,
    rank_fusion_pipeline,
    reciprocal_rank_fusion,
)
from search_app.transcripts import SNIPPETS


def test_coerce_maps_legacy_podcast_fields():
    doc = coerce(
        {
            "podcast_id": "mongodb-podcast",
            "podcast_title": "The MongoDB Podcast",
            "podcast_author": "MongoDB",
            "episode_id": "abc",
            "episode_title": "Hello",
            "episode_url": "https://example.test",
            "spotify_episode_id": "sp1",
            "text": "hi",
            "chunk_index": 0,
        }
    )
    assert doc["schema_version"] == 3
    assert doc["asset"] == {
        "type": "episode",
        "id": "abc",
        "title": "Hello",
        "url": "https://example.test",
    }
    assert doc["parent"]["type"] == "podcast"
    assert doc["parent"]["id"] == "mongodb-podcast"
    assert doc["parent"]["title"] == "The MongoDB Podcast"
    assert doc["attrs"]["spotify_episode_id"] == "sp1"
    assert "podcast_id" not in doc
    assert "episode_id" not in doc
    again = passage_from_legacy(doc)
    assert again == doc


def test_passage_document_shapes():
    video = passage_document(
        asset_type="video",
        asset_id="vid1",
        title="Talk",
        url="https://www.youtube.com/watch?v=vid1",
        parent={
            "type": "channel",
            "id": "mongodb-youtube",
            "title": "MongoDB on YouTube",
            "author": "MongoDB",
        },
        chunk_index=0,
        text="hello",
        attrs={"youtube_video_id": "vid1"},
    )
    assert video["schema_version"] == 3
    assert video["asset"]["type"] == "video"
    assert video["parent"]["type"] == "channel"
    assert "podcast_id" not in video
    assert "episode_id" not in video

    snippet = passage_document(
        asset_type="snippet",
        asset_id="snippet:8f3a",
        title="Retry with jitter",
        author="sig",
        chunk_index=0,
        text="def retry():\n    pass",
        attrs={"language": "python"},
    )
    assert "parent" not in snippet
    assert snippet["asset"]["author"] == "sig"
    assert snippet["attrs"]["language"] == "python"

    repo = passage_document(
        asset_type="repo",
        asset_id="mongodb/mongo",
        title="mongodb/mongo",
        url="https://github.com/mongodb/mongo",
        author="mongodb",
        chunk_index=0,
        text="The MongoDB Database.",
        attrs={"host": "github", "path": "README.md"},
    )
    assert "parent" not in repo
    assert repo["asset"]["type"] == "repo"


def test_spotify_and_apple_timestamp_urls():
    assert youtube_listen_url("T323-B5v1oI", 64000) == (
        "https://www.youtube.com/watch?v=T323-B5v1oI&t=64s"
    )
    assert (
        primary_share_url({"youtube_video_id": "abc"}, 5000)
        == "https://www.youtube.com/watch?v=abc&t=5s"
    )
    assert (
        spotify_listen_url("0T15bGPizAqgJgQA3rnsv4", 11120)
        == "https://open.spotify.com/episode/0T15bGPizAqgJgQA3rnsv4?t=11"
    )
    apple = apple_listen_url("1000771353400", 64000)
    assert "i=1000771353400" in apple
    assert apple.endswith("t=64") or "&t=64" in apple
    assert primary_share_url(
        {"spotify_episode_id": "abc"}, 5000
    ) == "https://open.spotify.com/episode/abc?t=5"
    assert (
        spotify_episode_id_from_transcript_url(
            "https://transcript-files.spotifycdn.com/0ibUtrJG4JVgwfvB2MXMSb/0T15bGPizAqgJgQA3rnsv4/transcript.srt"
        )
        == "0T15bGPizAqgJgQA3rnsv4"
    )


def test_lexical_pipeline_uses_fuzzy_on_standard_multi_fields():
    pipeline = lexical_pipeline("gsming", "podcast_search_index", 10)
    clauses = pipeline[0]["$search"]["compound"]["should"]
    fuzzy_paths = {
        clause["text"]["path"]
        for clause in clauses
        if clause["text"].get("fuzzy")
    }
    assert "asset.title.fuzzy" in fuzzy_paths
    assert "parent.title.fuzzy" in fuzzy_paths
    assert "text.fuzzy" in fuzzy_paths
    assert FUZZY["maxEdits"] == 2
    assert FUZZY["prefixLength"] == 0


def test_rank_fusion_pipeline_blends_vector_and_search():
    pipeline = rank_fusion_pipeline(
        "document modeling",
        search_index="podcast_search_index",
        vector_index="podcast_vector_index",
        model="voyage-4",
        limit=10,
    )
    fusion = pipeline[0]["$rankFusion"]["input"]["pipelines"]
    vector = fusion["vectorPipeline"][0]["$vectorSearch"]
    assert vector["path"] == "text"
    assert vector["query"] == {"text": "document modeling"}
    assert vector["model"] == "voyage-4"
    assert "$search" in fusion["textPipeline"][0]


def test_levenshtein_gsming_gaming():
    assert levenshtein("gsming", "gaming") == 1


def test_rrf_prefers_docs_in_both_lists():
    lexical = [
        {"_id": "a", "episode_id": "e1", "text": "alpha", "score": 4},
        {"_id": "b", "episode_id": "e2", "text": "beta", "score": 3},
    ]
    vector = [
        {"_id": "c", "episode_id": "e3", "text": "gamma", "score": 0.9},
        {"_id": "a", "episode_id": "e1", "text": "alpha", "score": 0.8},
    ]
    ranked = reciprocal_rank_fusion(lexical, vector)
    assert ranked[0]["_id"] == "a"
    assert set(ranked[0]["match_types"]) == {"keyword", "semantic"}


def _hit(asset, parent, chunk_index, score, match_types, html):
    return {
        "asset": asset,
        "parent": parent,
        "published_at": "2025-01-01",
        "chunk_index": chunk_index,
        "score": score,
        "match_types": match_types,
        "snippet_html": html,
        "audio_url": "",
        "attrs": {},
    }


def test_group_passages_nests_episodes_and_lifts_top_level_assets():
    episode = {
        "type": "episode",
        "id": "e1",
        "title": "Ep 1",
        "url": "http://example.test/e1",
    }
    show = {"type": "podcast", "id": "p1", "title": "Show One", "author": "A"}
    docs = [
        _hit(episode, show, 0, 0.9, ["keyword"], "one"),
        _hit(episode, show, 1, 0.4, ["semantic"], "two"),
        _hit(episode, show, 2, 0.2, ["semantic"], "three"),
        _hit(
            {
                "type": "episode",
                "id": "e2",
                "title": "Ep 2",
                "url": "http://example.test/e2",
            },
            {"type": "podcast", "id": "p2", "title": "Show Two", "author": "B"},
            0,
            0.7,
            ["keyword"],
            "other",
        ),
        _hit(
            {
                "type": "repo",
                "id": "mongodb/mongo",
                "title": "mongodb/mongo",
                "author": "mongodb",
            },
            None,
            0,
            0.5,
            ["keyword"],
            "readme",
        ),
    ]
    grouped = group_passages(docs, passages_per_asset=2)
    assert [group["id"] for group in grouped] == ["p1", "p2", "mongodb/mongo"]
    assert grouped[0]["type"] == "podcast"
    assert len(grouped[0]["assets"][0]["passages"]) == 2
    assert grouped[0]["assets"][0]["match_types"] == ["keyword", "semantic"]
    repo = grouped[2]
    assert repo["type"] == "repo"
    assert repo["assets"][0]["type"] == "repo"
    assert repo["assets"][0]["parent"] is None


def test_snippets_are_uniquely_keyed():
    keys = [(s["asset"]["type"], s["asset"]["id"], s["chunk_index"]) for s in SNIPPETS]
    assert len(keys) == len(set(keys))
    assert all(s["text"].strip() for s in SNIPPETS)
    assert all(s["asset"]["type"] == "episode" for s in SNIPPETS)
    assert all(s["parent"]["type"] == "podcast" for s in SNIPPETS)
    assert all("podcast_id" not in s and "episode_id" not in s for s in SNIPPETS)


def test_srt_to_text_strips_indexes_and_timestamps():
    srt = """1
00:00:11,120 --> 00:00:14,040
Hello everyone.

2
00:00:14,040 --> 00:00:17,200
Welcome to <b>MongoDB</b>.
"""
    assert srt_to_text(srt) == "Hello everyone. Welcome to MongoDB."
    cues = parse_srt_cues(srt)
    assert cues[0].start_ms == 11120
    assert cues[1].end_ms == 17200


def test_chunk_cues_keeps_start_and_end():
    cues = [
        Cue(start_ms=1000, end_ms=2000, text="Hello there."),
        Cue(start_ms=2000, end_ms=3500, text="This is later."),
        Cue(start_ms=90000, end_ms=95000, text="A much later sentence that should start a new chunk because it will not fit."),
    ]
    chunks = chunk_cues(cues, max_chars=40)
    assert chunks[0]["start_ms"] == 1000
    assert chunks[0]["end_ms"] == 3500
    assert chunks[-1]["start_ms"] == 90000


def test_chunk_transcript_respects_max_chars():
    text = "Hello world. " * 40
    chunks = chunk_transcript(text, max_chars=80)
    assert chunks
    assert all(len(chunk) <= 80 for chunk in chunks)


def test_highlight_wraps_hits():
    html = highlight_html(
        [{"texts": [{"value": "hello ", "type": "text"}, {"value": "world", "type": "hit"}]}],
        "hello world",
    )
    assert html == "hello <mark>world</mark>"
