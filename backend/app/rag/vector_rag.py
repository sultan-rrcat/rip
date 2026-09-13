from app.rag.pipeline import RagPipeline
from app.core.logging import setup_logging
from app.core.db import pg_connection

logger = setup_logging()

class VectorRAG(RagPipeline):
    def retrieve_context(
        self, notebook_id, user_prompt, top_k=5, vector_threshold=0.1, rerank_threshold=0.05
    ):
        try:
            # logger.info(f"User Prompt: {user_prompt[:50]}...")

            prompt_embeddings = self.embedding_model.embed_query(user_prompt)

            # Vector and Keyword Search
            with pg_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Vector Search
                    cur.execute(
                        """
                        SELECT chunk_text, metadata, 1-(embedding<=>%s::vector) as similarity
                        FROM embeddings
                        WHERE file_id IN (select file_id from files where notebook_id=%s)
                        ORDER BY embedding<=>%s::vector
                        LIMIT %s
                        """,
                        (prompt_embeddings, notebook_id, prompt_embeddings, top_k),
                    )
                    vector_results = cur.fetchall()
                    logger.info(f"Vector search returned {len(vector_results)} raw results")
                    # logger.info(f"Vector search results: \n{vector_results}")

                    # 2. Keyword (Full Text) Search
                    cur.execute(
                        """
                        SELECT chunk_text, metadata,
                            ts_rank_cd(to_tsvector('english', chunk_text), websearch_to_tsquery('english', %s)) AS rank
                        FROM embeddings
                        WHERE file_id IN (select file_id from files where notebook_id=%s)
                        AND to_tsvector('english', chunk_text) @@ websearch_to_tsquery('english', %s)
                        ORDER BY rank DESC
                        LIMIT %s
                        """,
                        (user_prompt, notebook_id, user_prompt, top_k),
                    )
                    keyword_results = cur.fetchall()
                    logger.info(f"Keyword search returned {len(keyword_results)} raw results")
                    # logger.info(f"Keyword search result : {keyword_results}")

            # Filtering Vector Results
            initial_count = len(vector_results)
            vector_results = [r for r in vector_results if r[2] > vector_threshold]
            logger.info(
                f"Filtered vector results from {initial_count} down to {len(vector_results)} (threshold > {vector_threshold})"
            )

            # Hybrid Formatting
            contexts = {}

            # VECTOR RESULTS
            for i, (text, metadata, score) in enumerate(vector_results):
                key = hash(text)

                contexts[key] = {
                    "text": text,
                    "metadata": metadata,
                    "vector_rank": i + 1,
                    "keyword_rank": None,
                }

            # KEYWORD RESULTS
            for i, (text, metadata, score) in enumerate(keyword_results):
                key = hash(text)

                if key not in contexts:
                    contexts[key] = {
                        "text": text,
                        "metadata": metadata,
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

            # Reranking
            rerank_k = top_k * 3
            rerank_subset = sorted_contexts[:rerank_k]

            if rerank_subset:
                # logger.info(f"Sending {len(rerank_subset)} candidates to reranker model")
                pairs = [(user_prompt, str(c.get("text", ""))) for c in rerank_subset]
                scores = self.reranker_model.predict(pairs)

                for i, score in enumerate(scores):
                    # Cast to plain float: numpy scalars break LangGraph's
                    # msgpack checkpoint serde (StepResult) + SSE json.dumps
                    # + Postgres Json() downstream (2026-09-13 live crash).
                    rerank_subset[i]["rerank_score"] = float(score)

                # Sort by rerank score
                rerank_subset.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)

                # Re-combine (keeping top reranked items first)
                final_list = rerank_subset + sorted_contexts[rerank_k:]
            else:
                logger.info("No results found to rerank.")
                final_list = sorted_contexts

            # logger.info(f"Rerank subset: {rerank_subset}")

            # Adding threshold
            # Final Formatting
            # top_results = final_list[:top_k]
            filtered = [
                c for c in final_list if c.get("rerank_score", 0) > rerank_threshold
            ]

            if not filtered:
                logger.warning("⚠️ No reranked results passed threshold — using fallback")
                filtered = final_list[:3]

            top_results = filtered

            logger.info(f"Retrieved top {len(top_results)} final contexts")

            structured_context = {
                "query": user_prompt,
                "results": [
                    {
                        "content": c["text"],
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