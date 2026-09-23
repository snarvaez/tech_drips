import json
import os

import pytest

from search_app.schema import coerce, passage_document, passage_from_legacy
from search_app.blog import article_from_html, parse_sitemap
from search_app.blog_ingest import post_state
from search_app.chunking import chunk_cues, chunk_sections, chunk_source, chunk_transcript
from search_app.describe import describe_source, grounded_fallback
from search_app.search import vector_pipeline as _vector_pipeline
from search_app.srt import Cue, parse_srt_cues, srt_to_text
from search_app.share import (
    apple_listen_url,
    passage_href,
    primary_share_url,
    spotify_episode_id_from_transcript_url,
    spotify_listen_url,
    with_passage_hrefs,
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


def test_passage_href_matches_the_player_link():
    video = passage_href(
        {"type": "video", "id": "T323-B5v1oI", "attrs": {}},
        64000,
        listen_base="http://127.0.0.1:5000",
    )
    assert video == "https://www.youtube.com/watch?v=T323-B5v1oI&t=64s"
    episode = passage_href(
        {
            "type": "episode",
            "id": "sn-1043",
            "attrs": {"spotify_episode_id": "0T15bGPizAqgJgQA3rnsv4"},
        },
        11120,
    )
    assert episode == "https://open.spotify.com/episode/0T15bGPizAqgJgQA3rnsv4?t=11"
    local = passage_href(
        {"type": "snippet", "id": "snippet:8f3a", "attrs": {}},
        5000,
        listen_base="http://127.0.0.1:5000",
    )
    assert local == "http://127.0.0.1:5000/listen?asset=snippet%3A8f3a&t=5"
    payload = with_passage_hrefs(
        {
            "groups": [
                {
                    "assets": [
                        {
                            "type": "video",
                            "id": "abc",
                            "attrs": {},
                            "passages": [{"start_ms": 1000}],
                        }
                    ]
                }
            ]
        }
    )
    assert payload["groups"][0]["assets"][0]["passages"][0]["href"].startswith(
        "https://www.youtube.com/watch?v=abc&t="
    )


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


POST_URL = "https://www.mongodb.com/company/blog/technical/prisma-next"


def test_passage_document_accepts_a_blog_post():
    doc = passage_document(
        asset_type="post",
        asset_id="bltabc",
        title="Prisma Next",
        url=POST_URL,
        author="Alex Bevilacqua, Will Madden",
        parent={
            "type": "blog",
            "id": "mongodb-blog",
            "title": "MongoDB Blog",
            "author": "MongoDB",
            "url": "https://www.mongodb.com/company/blog",
        },
        chunk_index=0,
        text="You fetch your first document.",
        attrs={"slug": "/company/blog/technical/prisma-next", "channel": "technical"},
    )
    assert doc["asset"]["type"] == "post"
    assert doc["parent"]["type"] == "blog"
    assert doc["parent"]["id"] == "mongodb-blog"
    assert "start_ms" not in doc
    with pytest.raises(ValueError):
        passage_document(
            asset_type="post",
            asset_id="bltabc",
            title="Prisma Next",
            chunk_index=0,
            text="missing parent",
        )


def test_passage_href_for_a_post_opens_the_article():
    href = passage_href(
        {"type": "post", "id": "bltabc", "url": POST_URL, "attrs": {}},
        None,
        listen_base="http://127.0.0.1:5000",
    )
    assert href == POST_URL
    assert "/listen" not in href


def test_sitemap_keeps_english_blog_posts_only():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.mongodb.com/company/blog/technical/prisma-next</loc><lastmod>2026-06-03</lastmod></url>
  <url><loc>https://www.mongodb.com/company/research/how-to-test</loc><lastmod>2026-09-23</lastmod></url>
  <url><loc>https://www.mongodb.com/company/bloghow-to-select-for-update-inside-mongodb-transactions</loc></url>
  <url><loc>https://www.mongodb.com/company/blog/channel/home</loc></url>
  <url><loc>https://www.mongodb.com/company/blog/innovating-with-mongodb-customer-successes-may-2025/</loc><lastmod>2026-08-04</lastmod></url>
</urlset>
"""
    entries = parse_sitemap(xml)
    assert [entry.url for entry in entries] == [
        "https://www.mongodb.com/company/blog/technical/prisma-next",
        "https://www.mongodb.com/company/blog/innovating-with-mongodb-customer-successes-may-2025/",
    ]
    assert entries[0].lastmod == "2026-06-03"


def test_rich_text_sections_keep_headings_and_code():
    sentence = "Atlas keeps the order in the same document. "
    page = {
        "_content_type_uid": "blog_article",
        "uid": "bltabc",
        "title": "Prisma Next",
        "url": "/company/blog/technical/prisma-next",
        "locale": "en-us",
        "entry_settings": {"published_date": "2026-05-20"},
        "contributors": [{"title": "Alex Bevilacqua"}, {"title": "Will Madden"}],
        "entry_content": [
            {"side_bar": {}},
            {
                "rich_text": {
                    "json_rich_text": {
                        "type": "doc",
                        "children": [
                            {"type": "p", "children": [{"text": "Intro paragraph."}]},
                            {
                                "type": "h2",
                                "children": [{"text": "You fetch your first document"}],
                            },
                            {"type": "p", "children": [{"text": sentence * 40}]},
                            {
                                "type": "reference",
                                "attrs": {
                                    "content-type-uid": "code_panel",
                                    "entry-uid": "bltcode",
                                },
                            },
                            {
                                "type": "reference",
                                "attrs": {
                                    "content-type-uid": "media",
                                    "entry-uid": "bltmedia",
                                },
                            },
                        ],
                    }
                }
            },
        ],
        "_embedded_items": {
            "entry_content.rich_text.json_rich_text": [
                {
                    "uid": "bltcode",
                    "_content_type_uid": "code_panel",
                    "entry_content": {
                        "code_snippets": [
                            {
                                "language": "TypeScript",
                                "code": "const user = await db.collection('users').findOne({ email });",
                            }
                        ]
                    },
                },
                {
                    "uid": "bltmedia",
                    "_content_type_uid": "media",
                    "entry_content": {"caption": "ignored figure"},
                },
            ]
        },
    }
    html = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"page": page}}})
        + "</script></html>"
    )
    article = article_from_html(html, POST_URL)
    assert article is not None
    assert article.uid == "bltabc"
    assert article.author == "Alex Bevilacqua, Will Madden"
    assert article.channel == "technical"
    assert article.slug == "/company/blog/technical/prisma-next"
    chunks = chunk_sections(article.sections, max_chars=900)
    headed = [chunk for chunk in chunks if chunk.startswith("You fetch your first document")]
    assert len(headed) >= 2
    code = [chunk for chunk in chunks if "findOne" in chunk]
    assert code
    assert "TypeScript\nconst user" in code[0]
    assert "ignored figure" not in "\n".join(chunks)
    assert article_from_html(
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"page": {"_content_type_uid": "page"}}}})
        + "</script></html>",
        POST_URL,
    ) is None


def test_html_article_fallback_when_next_data_is_absent():
    html = """
    <html><head>
    <title>Voyage | MongoDB</title>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[{"@type":"Article","headline":"Voyage","datePublished":"2025-02-01","author":{"@type":"Person","name":"Ada"}}]}
    </script>
    </head><body>
    <article>
      <style>.x{color:red}</style>
      <h2>Why this matters</h2>
      <p>MongoDB acquired Voyage AI.</p>
      <pre>db.posts.find()</pre>
    </article>
    </body></html>
    """
    article = article_from_html(html, "https://www.mongodb.com/company/blog/news/voyage")
    assert article is not None
    assert article.uid == "/company/blog/news/voyage"
    assert article.title == "Voyage"
    assert article.author == "Ada"
    assert article.channel == "news"
    chunks = chunk_sections(article.sections, max_chars=900)
    assert chunks[0].startswith("Why this matters")
    assert any("db.posts.find()" in chunk for chunk in chunks)
    assert ".x{color:red}" not in "\n".join(chunks)
    assert article_from_html("<html><p>no article</p></html>", POST_URL) is None


def test_file_passage_links_to_the_source_line():
    url = "https://github.com/mongodb-developer/typescript-multiplayer-gaming-example/blob/abc/backend/game.ts"
    doc = passage_document(
        asset_type="file",
        asset_id="mongodb-developer/typescript-multiplayer-gaming-example:backend/game.ts",
        title="game.ts",
        url=url,
        parent={
            "type": "repository",
            "id": "mongodb-developer/typescript-multiplayer-gaming-example",
            "title": "Coin Race",
            "url": "https://github.com/mongodb-developer/typescript-multiplayer-gaming-example",
        },
        chunk_index=0,
        text="backend/game.ts defines collectCoin.",
        code="export function collectCoin(player) {\n  return player.score + 1\n}",
        attrs={"path": "backend/game.ts", "language": "typescript", "start_line": 12, "ref": "blob"},
    )
    assert doc["parent"]["type"] == "repository"
    assert "code" in doc
    href = passage_href({"type": "file", "id": doc["asset"]["id"], "url": url, "attrs": {}}, None)
    payload = with_passage_hrefs(
        {
            "groups": [
                {
                    "assets": [
                        {
                            "type": "file",
                            "id": doc["asset"]["id"],
                            "url": url,
                            "attrs": {},
                            "passages": [{"start_line": 12, "start_ms": None}],
                        }
                    ]
                }
            ]
        }
    )
    assert href == url
    assert payload["groups"][0]["assets"][0]["passages"][0]["href"] == url + "#L12"
    with pytest.raises(ValueError):
        passage_document(
            asset_type="file",
            asset_id="x",
            title="game.ts",
            chunk_index=0,
            text="missing parent",
        )


def test_source_chunks_keep_symbols_and_line_breaks():
    source = "\n".join(
        [
            "import { db } from './db'",
            "",
            "export function collectCoin(player) {",
            "  return player.score + 1",
            "}",
            "",
            "export function updateZoneKeyRange(admin) {",
            "  return admin.command({ updateZoneKeyRange: 'players' })",
            "}",
        ]
    )
    chunks = chunk_source(source, max_lines=80)
    names = [chunk["symbol"] for chunk in chunks]
    assert "collectCoin" in names
    assert "updateZoneKeyRange" in names
    zone = next(chunk for chunk in chunks if chunk["symbol"] == "updateZoneKeyRange")
    assert "\n" in zone["code"]
    assert zone["start_line"] > 1


def test_grounded_description_does_not_invent_behavior():
    source = "export function collectCoin(player) {\n  return player.score + 1\n}\n"
    readme = "A two-player coin collecting game. MongoDB stores players and matches."
    text = grounded_fallback(readme, "backend/game.ts", source)
    assert "collectCoin" in text
    assert "coin collecting" in text
    assert "zone" not in text.lower()
    assert "shard" not in text.lower()
    saved = os.environ.pop("XAI_API_KEY", None)
    try:
        assert describe_source(readme, "backend/game.ts", source) == text
    finally:
        if saved is not None:
            os.environ["XAI_API_KEY"] = saved


def test_code_vector_pipeline_uses_voyage_code_4():
    pipeline = _vector_pipeline(
        "updateZoneKeyRange",
        "passage_code_index",
        "voyage-code-4",
        5,
        path="code",
    )
    stage = pipeline[0]["$vectorSearch"]
    assert stage["path"] == "code"
    assert stage["model"] == "voyage-code-4"
    assert stage["index"] == "passage_code_index"


def test_rrf_includes_a_code_hit_without_dropping_prose():
    prose = [{"_id": "readme", "text": "multiplayer game", "score": 1, "match_types": ["keyword", "semantic"]}]
    code = [{"_id": "zones", "text": "zones.ts updateZoneKeyRange", "code": "updateZoneKeyRange", "score": 1}]
    ranked = reciprocal_rank_fusion(
        prose,
        [],
        code=code,
        lexical_weight=1.0,
        code_weight=0.8,
        preserve_lexical_types=True,
    )
    by_id = {doc["_id"]: doc for doc in ranked}
    assert "readme" in by_id and "zones" in by_id
    assert "keyword" in by_id["readme"]["match_types"]
    assert "code" in by_id["zones"]["match_types"]
    assert by_id["readme"]["score"] > by_id["zones"]["score"]


def test_post_state_skips_a_current_contiguous_post():
    bucket = {
        "indexes": [0, 1],
        "lastmods": {"2026-06-03"},
        "complete": [True, True],
    }
    assert post_state(bucket, "2026-06-03", force=False) == "complete"
    assert post_state(bucket, "2026-06-04", force=False) == "stale"
    assert post_state({"indexes": [0, 2], "lastmods": {"2026-06-03"}, "complete": [True, True]}, "2026-06-03", force=False) == "partial"
    assert post_state(None, "2026-06-03", force=False) == "missing"
