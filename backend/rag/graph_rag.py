from rag.pipeline import RagPipeline
from core.logging import setup_logging
from core.db import pg_connection

logger = setup_logging()


class GraphRAG(RagPipeline):

    # =========================
    # 🔎 VECTOR ENTRY POINTS
    # =========================
    def _vector_entry_points(self, user_prompt: str, notebook_id: str, top_k: int = 5) -> list[str]:
        """
        Postgres vector search → returns embedding_ids (real UUIDs)
        which are the same chunk_ids stored in Neo4j.
        """
        prompt_embedding = self.embedding_model.embed_query(user_prompt)

        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT e.embedding_id
                    FROM embeddings_test e
                    JOIN files f ON e.file_id = f.file_id
                    WHERE f.notebook_id = %s
                    ORDER BY e.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (notebook_id, prompt_embedding, top_k),
                )
                rows = cur.fetchall()

        chunk_ids = [str(row[0]) for row in rows]
        logger.info(f"Vector entry points: {len(chunk_ids)} chunks")
        return chunk_ids

    # =========================
    # 🔎 ENTITY ENTRY POINTS
    # =========================
    def _entity_entry_points(self, user_prompt: str, doc_type: str) -> list[str]:
        """
        Extract entities from user prompt via LLM →
        entity names used as graph entry points.
        """
        result = self.extract_entities(user_prompt, doc_type)
        entity_names = [
            e.get("name", "").strip()
            for e in result.get("entities", [])
            if e.get("name", "").strip()
        ]
        logger.info(f"Entity entry points from prompt: {entity_names}")
        return entity_names

    # =========================
    # 🕸️ HYBRID TRAVERSAL
    # =========================
    def _traverse(
        self,
        driver,
        chunk_ids: list[str],
        entity_names: list[str],
        notebook_id: str,
    ) -> list[dict]:
        """
        Path A — Chunk-centric:
            entry Chunk
            → NEXT (±2 hops for surrounding context)
            → MENTIONS → Entity → back to sibling Chunks

        Path B — Entity-centric:
            Entity name match (from prompt)
            → 1-hop entity relationships
            → back to Chunks that MENTION those entities

        Both filtered by notebook_id. Results deduped by chunk_id.
        """
        results = {}

        with driver.session() as session:

            # ─── Path A: Chunk-centric ───
            if chunk_ids:
                records = session.run(
                    """
                    UNWIND $chunk_ids AS cid

                    MATCH (entry:Chunk {chunk_id: cid, notebook_id: $notebook_id})

                    // sequential neighbors
                    OPTIONAL MATCH (entry)-[:NEXT*1..2]->(forward:Chunk)
                    WHERE forward.notebook_id = $notebook_id

                    OPTIONAL MATCH (backward:Chunk)-[:NEXT*1..2]->(entry)
                    WHERE backward.notebook_id = $notebook_id

                    // entity-connected sibling chunks
                    OPTIONAL MATCH (entry)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(sibling:Chunk)
                    WHERE sibling.notebook_id = $notebook_id
                      AND sibling.chunk_id <> entry.chunk_id

                    WITH entry,
                         collect(DISTINCT forward)  AS fwd,
                         collect(DISTINCT backward) AS bwd,
                         collect(DISTINCT sibling)  AS siblings

                    UNWIND ([entry] + fwd + bwd + siblings) AS c

                    RETURN DISTINCT
                        c.chunk_id  AS chunk_id,
                        c.text      AS text,
                        c.section   AS section,
                        c.file_id   AS file_id,
                        'chunk'     AS traversal_type
                    """,
                    chunk_ids=chunk_ids,
                    notebook_id=notebook_id,
                )

                for r in records:
                    cid = r["chunk_id"]
                    if cid and cid not in results:
                        results[cid] = {
                            "chunk_id": cid,
                            "text": r["text"],
                            "section": r["section"] or "",
                            "file_id": r["file_id"],
                            "traversal_type": r["traversal_type"],
                        }

            # ─── Path B: Entity-centric ───
            if entity_names:
                records = session.run(
                    """
                    UNWIND $entity_names AS ename

                    // case-insensitive entity match
                    MATCH (e:Entity)
                    WHERE toLower(e.name) CONTAINS toLower(ename)

                    // 1-hop entity relationships
                    OPTIONAL MATCH (e)-[]-(connected:Entity)

                    WITH collect(DISTINCT e) + collect(DISTINCT connected) AS all_entities
                    UNWIND all_entities AS target_entity

                    // back to chunks
                    MATCH (c:Chunk)-[:MENTIONS]->(target_entity)
                    WHERE c.notebook_id = $notebook_id

                    RETURN DISTINCT
                        c.chunk_id  AS chunk_id,
                        c.text      AS text,
                        c.section   AS section,
                        c.file_id   AS file_id,
                        'entity'    AS traversal_type
                    """,
                    entity_names=entity_names,
                    notebook_id=notebook_id,
                )

                for r in records:
                    cid = r["chunk_id"]
                    if cid and cid not in results:
                        results[cid] = {
                            "chunk_id": cid,
                            "text": r["text"],
                            "section": r["section"] or "",
                            "file_id": r["file_id"],
                            "traversal_type": r["traversal_type"],
                        }

        logger.info(
            f"Traversal complete — {len(results)} unique chunks "
            f"(chunk path: {sum(1 for v in results.values() if v['traversal_type'] == 'chunk')}, "
            f"entity path: {sum(1 for v in results.values() if v['traversal_type'] == 'entity')})"
        )
        return list(results.values())

    # =========================
    # 🏆 RERANK
    # =========================
    def _rerank(
        self, user_prompt: str, chunks: list[dict], threshold: float = 0.05
    ) -> list[dict]:
        if not chunks:
            return []

        pairs = [(user_prompt, c["text"]) for c in chunks]
        scores = self.reranker_model.predict(pairs)

        for i, score in enumerate(scores):
            chunks[i]["rerank_score"] = float(score)

        chunks.sort(key=lambda x: x["rerank_score"], reverse=True)

        filtered = [c for c in chunks if c["rerank_score"] > threshold]

        if not filtered:
            logger.warning("No chunks passed rerank threshold — using top 3 fallback")
            filtered = chunks[:3]

        logger.info(f"Reranked: {len(filtered)} chunks passed threshold")
        return filtered

    # =========================
    # 🚀 RETRIEVE CONTEXT
    # =========================
    def retrieve_context(
        self,
        user_prompt: str,
        notebook_id: str,
        driver,
        top_k: int = 5,
    ) -> dict:
        try:
            # Step 1: detect doc type for entity extraction prompt
            doc_type = self.detect_document_type(user_prompt)
            logger.info(f"Detected doc type for query: {doc_type}")

            # Step 2: parallel entry points
            chunk_ids = self._vector_entry_points(user_prompt, notebook_id, top_k)
            entity_names = self._entity_entry_points(user_prompt, doc_type)

            # Step 3: hybrid traversal
            expanded_chunks = self._traverse(driver, chunk_ids, entity_names, notebook_id)

            if not expanded_chunks:
                logger.warning("Graph traversal returned no results")
                return {"query": user_prompt, "results": []}

            # Step 4: rerank
            reranked = self._rerank(user_prompt, expanded_chunks)

            # Step 5: format — same structure as VectorRAG for drop-in compatibility
            structured_context = {
                "query": user_prompt,
                "results": [
                    {
                        "content": c["text"],
                        "source": c["file_id"],
                        "section": c["section"],
                        "rerank_score": c["rerank_score"],
                        "traversal_type": c["traversal_type"],
                    }
                    for c in reranked
                ],
            }

            logger.info(f"GraphRAG returned {len(reranked)} final chunks")
            return structured_context

        except Exception as e:
            logger.exception(f"GraphRAG retrieve_context failed: {e}")
            raise