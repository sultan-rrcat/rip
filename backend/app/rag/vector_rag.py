from app.rag.pipeline import RagPipeline
from app.core.logging import setup_logging
from app.core.db import pg_connection

logger = setup_logging()

def _interleave_by_source(items: list[dict]) -> list[dict]:
    """Round-robin items so no single file starves the others.

    Groups by ``metadata.source`` (first-seen file order decides the
    rotation), preserving relative order within each file. Single-file
    and empty inputs come back unchanged. Applied to the rerank-sorted
    list, so global ranking still decides *within* a file — interleaving
    only decides *across* files. Without this, a compare-two-documents
    request collapses to whichever file ranks higher globally (observed
    live: 2 ready files, both fan-out queries returned one document).
    """
    groups: dict[str, list[dict]] = {}
    for item in items:
        metadata = item.get("metadata") or {}
        source = metadata.get("source", "unknown")
        groups.setdefault(source, []).append(item)
    if len(groups) <= 1:
        return items
    ordered = list(groups.values())
    longest = max(len(g) for g in ordered)
    merged: list[dict] = []
    for i in range(longest):
        for group in ordered:
            if i < len(group):
                merged.append(group[i])
    return merged


#: Section names that signal an overview chunk (abstract/summary/index...).
#: Used ONLY as a metadata boost in overview mode — never joined into the
#: embedding query string (slash-joined keywords dilute BGE-M3 + break FTS).
_OVERVIEW_SECTION_KEYWORDS = frozenset(
    {
        "introduction",
        "summary",
        "abstract",
        "overview",
        "index",
        "conclusion",
        "limitations",
        "limitation",
        "implementation",
        "results",
        "methodology",
    }
)

_OVERVIEW_BOOST = 0.15

_VALID_MODES = frozenset({"specific", "overview"})


def _matches_overview_section(metadata: dict) -> bool:
    """True when H1/H2/H3 contains an overview keyword (case-insensitive)."""
    for key in ("H1", "H2", "H3"):
        value = (metadata or {}).get(key) or ""
        lowered = str(value).lower()
        for kw in _OVERVIEW_SECTION_KEYWORDS:
            if kw in lowered:
                return True
    return False


def _section_prefix(metadata: dict) -> str:
    """`Section: H1 > H2 > H3` from chunk metadata, or `""` when headerless.

    MarkdownHeaderTextSplitter keeps section titles in metadata only, so the
    highest-signal phrase of a chunk (e.g. `2.1 Functional Requirements`) is
    invisible to the reranker and the LLM. Observed live: the answer chunks
    scored 0.015/0.017 text-only vs 0.68/0.22 with the prefix — without it
    they never reach the answer step.
    """
    path = " > ".join(
        filter(
            None,
            [
                (metadata or {}).get("H1"),
                (metadata or {}).get("H2"),
                (metadata or {}).get("H3"),
            ],
        )
    )
    return f"Section: {path}" if path else ""


def _rerank_text(item: dict) -> str:
    """Text the CrossEncoder scores: section prefix + chunk text."""
    prefix = _section_prefix(item.get("metadata") or {})
    text = str(item.get("text", ""))
    return f"{prefix}\n{text}" if prefix else text


def _select_top(
    final_list: list[dict], top_k: int, rerank_threshold: float
) -> list[dict]:
    """Threshold filter topped up to `top_k` in rerank order.

    The threshold expresses preference, not a quota: returning fewer than
    `top_k` when scored candidates exist starves the answer step (observed
    live: 4 candidates in, 3 out, answer lost). Below-threshold items fill
    the remainder best-first; an empty pass falls back to list order.
    """
    top_results = [c for c in final_list if c.get("rerank_score", 0) > rerank_threshold][
        :top_k
    ]
    if len(top_results) < top_k:
        seen_ids = {id(c) for c in top_results}
        for c in final_list:
            if len(top_results) >= top_k:
                break
            if id(c) not in seen_ids:
                top_results.append(c)
                seen_ids.add(id(c))
    return top_results


def _stratify_overview(items: list[dict], top_k: int) -> list[dict]:
    """Pick at most one top chunk per H1 section, in document order.

    Input is rerank-sorted (boost already applied). Groups by H1 (fallback
    H2, then "unknown"), takes the best item per group, orders picks by
    ``chunk_index`` for narrative flow, then fills remaining slots with the
    next-best unpicked items. Empty/unknown-header docs come back unchanged.
    """
    if not items or top_k <= 0:
        return []
    groups: dict[str, list[dict]] = {}
    for item in items:
        metadata = item.get("metadata") or {}
        key = (
            str(metadata.get("H1") or "").strip()
            or str(metadata.get("H2") or "").strip()
            or "unknown"
        )
        groups.setdefault(key, []).append(item)
    if len(groups) <= 1:
        return items[:top_k]
    picks = [members[0] for members in groups.values()]
    picks.sort(key=lambda c: c.get("chunk_index", 0))
    if len(picks) >= top_k:
        return picks[:top_k]
    picked_ids = {id(p) for p in picks}
    for item in items:
        if len(picks) >= top_k:
            break
        if id(item) not in picked_ids:
            picks.append(item)
    return picks[:top_k]


class VectorRAG(RagPipeline):
    def retrieve_context(
        self,
        notebook_id,
        user_prompt,
        top_k=4,
        vector_threshold=0.1,
        rerank_threshold=0.05,
        file_id: str | None = None,
        file_name: str | None = None,
        mode: str = "specific",
    ):
        try:
            # logger.info(f"User Prompt: {user_prompt[:50]}...")
            normalized_mode = (mode or "specific").strip().lower()
            if normalized_mode not in _VALID_MODES:
                raise ValueError(
                    f"unknown retrieval mode {mode!r} (expected one of {sorted(_VALID_MODES)})"
                )
            fid = (file_id or "").strip() or None
            fname = (file_name or "").strip() or None
            file_scoped = fid is not None or fname is not None
            # Wide candidate pool in both modes: a narrow LIMIT cuts
            # relevant chunks before the reranker ever sees them (observed
            # live: answer chunks at vector ranks 9/11 with LIMIT 4).
            # The reranker + top_k cut below restore precision.
            fetch_k = top_k * 3

            prompt_embeddings = self.embedding_model.embed_query(user_prompt)

            # Vector and Keyword Search (file-scoped when fid/fname set)
            with pg_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Vector Search
                    if fid and fname:
                        cur.execute(
                            """
                            SELECT chunk_text, metadata, chunk_index, 1-(embedding<=>%s::vector) as similarity
                            FROM embeddings
                            WHERE file_id IN (select file_id from files where notebook_id=%s)
                            AND file_id = %s
                            AND file_id IN (select file_id from files where notebook_id=%s AND file_name ILIKE %s)
                            ORDER BY embedding<=>%s::vector
                            LIMIT %s
                            """,
                            (
                                prompt_embeddings,
                                notebook_id,
                                fid,
                                notebook_id,
                                fname,
                                prompt_embeddings,
                                fetch_k,
                            ),
                        )
                    elif fid:
                        cur.execute(
                            """
                            SELECT chunk_text, metadata, chunk_index, 1-(embedding<=>%s::vector) as similarity
                            FROM embeddings
                            WHERE file_id IN (select file_id from files where notebook_id=%s)
                            AND file_id = %s
                            ORDER BY embedding<=>%s::vector
                            LIMIT %s
                            """,
                            (prompt_embeddings, notebook_id, fid, prompt_embeddings, fetch_k),
                        )
                    elif fname:
                        cur.execute(
                            """
                            SELECT chunk_text, metadata, chunk_index, 1-(embedding<=>%s::vector) as similarity
                            FROM embeddings
                            WHERE file_id IN (select file_id from files where notebook_id=%s AND file_name ILIKE %s)
                            ORDER BY embedding<=>%s::vector
                            LIMIT %s
                            """,
                            (prompt_embeddings, notebook_id, fname, prompt_embeddings, fetch_k),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT chunk_text, metadata, chunk_index, 1-(embedding<=>%s::vector) as similarity
                            FROM embeddings
                            WHERE file_id IN (select file_id from files where notebook_id=%s)
                            ORDER BY embedding<=>%s::vector
                            LIMIT %s
                            """,
                            (prompt_embeddings, notebook_id, prompt_embeddings, fetch_k),
                        )
                    vector_results = cur.fetchall()
                    logger.info(f"Vector search returned {len(vector_results)} raw results")
                    # logger.info(f"Vector search results: \n{vector_results}")

                    # 2. Keyword (Full Text) Search
                    if fid and fname:
                        cur.execute(
                            """
                            SELECT chunk_text, metadata, chunk_index,
                                ts_rank_cd(to_tsvector('english', chunk_text), websearch_to_tsquery('english', %s)) AS rank
                            FROM embeddings
                            WHERE file_id IN (select file_id from files where notebook_id=%s)
                            AND file_id = %s
                            AND file_id IN (select file_id from files where notebook_id=%s AND file_name ILIKE %s)
                            AND to_tsvector('english', chunk_text) @@ websearch_to_tsquery('english', %s)
                            ORDER BY rank DESC
                            LIMIT %s
                            """,
                            (
                                user_prompt,
                                notebook_id,
                                fid,
                                notebook_id,
                                fname,
                                user_prompt,
                                fetch_k,
                            ),
                        )
                    elif fid:
                        cur.execute(
                            """
                            SELECT chunk_text, metadata, chunk_index,
                                ts_rank_cd(to_tsvector('english', chunk_text), websearch_to_tsquery('english', %s)) AS rank
                            FROM embeddings
                            WHERE file_id IN (select file_id from files where notebook_id=%s)
                            AND file_id = %s
                            AND to_tsvector('english', chunk_text) @@ websearch_to_tsquery('english', %s)
                            ORDER BY rank DESC
                            LIMIT %s
                            """,
                            (user_prompt, notebook_id, fid, user_prompt, fetch_k),
                        )
                    elif fname:
                        cur.execute(
                            """
                            SELECT chunk_text, metadata, chunk_index,
                                ts_rank_cd(to_tsvector('english', chunk_text), websearch_to_tsquery('english', %s)) AS rank
                            FROM embeddings
                            WHERE file_id IN (select file_id from files where notebook_id=%s AND file_name ILIKE %s)
                            AND to_tsvector('english', chunk_text) @@ websearch_to_tsquery('english', %s)
                            ORDER BY rank DESC
                            LIMIT %s
                            """,
                            (user_prompt, notebook_id, fname, user_prompt, fetch_k),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT chunk_text, metadata, chunk_index,
                                ts_rank_cd(to_tsvector('english', chunk_text), websearch_to_tsquery('english', %s)) AS rank
                            FROM embeddings
                            WHERE file_id IN (select file_id from files where notebook_id=%s)
                            AND to_tsvector('english', chunk_text) @@ websearch_to_tsquery('english', %s)
                            ORDER BY rank DESC
                            LIMIT %s
                            """,
                            (user_prompt, notebook_id, user_prompt, fetch_k),
                        )
                    keyword_results = cur.fetchall()
                    logger.info(f"Keyword search returned {len(keyword_results)} raw results")
                    # logger.info(f"Keyword search result : {keyword_results}")

            # Filtering Vector Results (4-tuple: text, metadata, chunk_index, score)
            initial_count = len(vector_results)
            vector_results = [r for r in vector_results if r[3] > vector_threshold]
            logger.info(
                f"Filtered vector results from {initial_count} down to {len(vector_results)} (threshold > {vector_threshold})"
            )

            # Hybrid Formatting
            contexts = {}

            # VECTOR RESULTS
            for i, (text, metadata, chunk_index, score) in enumerate(vector_results):
                key = hash(text)

                contexts[key] = {
                    "text": text,
                    "metadata": metadata if isinstance(metadata, dict) else {},
                    "chunk_index": chunk_index if isinstance(chunk_index, int) else 0,
                    "vector_rank": i + 1,
                    "keyword_rank": None,
                }

            # KEYWORD RESULTS
            for i, (text, metadata, chunk_index, score) in enumerate(keyword_results):
                key = hash(text)

                if key not in contexts:
                    contexts[key] = {
                        "text": text,
                        "metadata": metadata if isinstance(metadata, dict) else {},
                        "chunk_index": chunk_index if isinstance(chunk_index, int) else 0,
                        "vector_rank": None,
                        "keyword_rank": i + 1,
                    }
                else:
                    contexts[key]["keyword_rank"] = i + 1

            # logger.info(f"Hybrid formatted context: \n{contexts}")

            # RRF Implementation
            k = 60
            for c in contexts.values():
                rrf_score = 0
                if c["vector_rank"] is not None:
                    rrf_score += 1 / (k + c["vector_rank"])
                if c["keyword_rank"] is not None:
                    rrf_score += 1 / (k + c["keyword_rank"])
                c["rrf_score"] = rrf_score

            # Initial Sort after Hybrid Merge
            sorted_contexts = sorted(
                contexts.values(), key=lambda x: x["rrf_score"], reverse=True
            )
            logger.info(f"Total unique contexts merged: {len(sorted_contexts)}")
            # logger.info(f"RRF Contexts : {sorted_contexts}")

            # Reranking (overview reranks a bounded pool for stratification).
            # Full-pool rerank kept CrossEncoder work unbounded (trace
            # cfbaa9c3: two parallel overviews timed out at 30s); cap at
            # top_k*3 (12 for the standard top_k=4) so latency stays flat
            # while stratification still sees a stratified candidate set.
            rerank_k = min(len(sorted_contexts), top_k * 3) if normalized_mode == "overview" else top_k * 3
            rerank_subset = sorted_contexts[:rerank_k]

            if rerank_subset:
                # logger.info(f"Sending {len(rerank_subset)} candidates to reranker model")
                # Score section-prefixed text: headers live in metadata only,
                # and the pair must match what the LLM receives below.
                pairs = [(user_prompt, _rerank_text(c)) for c in rerank_subset]
                scores = self.reranker_model.predict(pairs)

                for i, score in enumerate(scores):
                    # Cast to plain float: numpy scalars break LangGraph's
                    # msgpack checkpoint serde (StepResult) + SSE json.dumps
                    # + Postgres Json() downstream (2026-09-13 live crash).
                    rerank_subset[i]["rerank_score"] = float(score)

                # Overview boost: section-name signal (never a slash-joined query).
                if normalized_mode == "overview":
                    for c in rerank_subset:
                        if _matches_overview_section(c.get("metadata") or {}):
                            c["rerank_score"] = float(c.get("rerank_score", 0)) + _OVERVIEW_BOOST

                # Sort by rerank score
                rerank_subset.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

                # Re-combine (keeping top reranked items first)
                final_list = rerank_subset + sorted_contexts[rerank_k:]
            else:
                logger.info("No results found to rerank.")
                final_list = sorted_contexts

            # Diversity: round-robin by file for GLOBAL queries only.
            # File-scoped shards are single-source — interleaving is a no-op.
            if not file_scoped:
                final_list = _interleave_by_source(final_list)

            # Overview: stratify one chunk per H1 section in doc order.
            if normalized_mode == "overview" and final_list:
                final_list = _stratify_overview(final_list, top_k)

            # logger.info(f"Rerank subset: {rerank_subset}")

            # Threshold preference, topped up to top_k (never starve the
            # answer step when scored candidates exist).
            if not any(c.get("rerank_score", 0) > rerank_threshold for c in final_list):
                logger.warning("⚠️ No reranked results passed threshold — using fallback")
            top_results = _select_top(final_list, top_k, rerank_threshold)

            logger.info(f"Retrieved top {len(top_results)} final contexts")

            structured_context = {
                "query": user_prompt,
                "mode": normalized_mode,
                "results": [
                    {
                        # Same section-prefixed text the reranker scored:
                        # the LLM needs the section title inline to ground
                        # the answer (and to say "not in the documents"
                        # honestly when it is absent).
                        "content": _rerank_text(c),
                        "source": c["metadata"].get("source", "unknown"),
                        "section": " > ".join(
                            filter(
                                None,
                                [
                                    c["metadata"].get("H1"),
                                    c["metadata"].get("H2"),
                                    c["metadata"].get("H3"),
                                ],
                            )
                        ),
                        "rerank_score": c['rerank_score']
                    }
                    for c in top_results
                ],
            }
            logger.info(f"structured_context: {structured_context}")
            return structured_context

            # return final_context

        except Exception as e:
            logger.exception(f"Error during context retrieval: {str(e)}")
            raise