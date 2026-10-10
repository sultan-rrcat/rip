CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─── users ───────────────────────────────────────────────────────────────────
-- Simple internal auth: username + salted password hash.

CREATE TABLE IF NOT EXISTS public.users
(
    user_id       uuid NOT NULL DEFAULT gen_random_uuid(),
    username      text COLLATE pg_catalog."default" NOT NULL,
    password_hash text COLLATE pg_catalog."default" NOT NULL,
    created_at    timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT users_pkey PRIMARY KEY (user_id),
    CONSTRAINT users_username_key UNIQUE (username)
) TABLESPACE pg_default;

-- ─── notebooks ──────────────────────────────────────────────────────────────
-- One notebook = one conversation = one chat history.
-- conversation_summary is internal (context-window compression), not user-visible.
-- Distinct from SSE event type "summary" (final user-visible answer).

CREATE TABLE IF NOT EXISTS public.notebooks
(
    notebook_id   uuid NOT NULL DEFAULT gen_random_uuid(),
    notebook_name text  COLLATE pg_catalog."default" NOT NULL,
    conversation_summary       text,                         -- rolling Ollama summary (internal)
    summary_message_count integer DEFAULT 0,                 -- folded_count dedup
    owner_id      uuid,                                     -- object-level authZ (S1)
    created_at    timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT notebooks_pkey PRIMARY KEY (notebook_id),
    CONSTRAINT notebooks_owner_id_fkey FOREIGN KEY (owner_id)
        REFERENCES public.users (user_id)
        ON UPDATE NO ACTION
        ON DELETE CASCADE
) TABLESPACE pg_default;

-- ─── files ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.files
(
    file_id     uuid NOT NULL DEFAULT gen_random_uuid(),
    notebook_id uuid,
    file_name   text COLLATE pg_catalog."default" NOT NULL,
    file_size   integer,
    file_status character varying(20) COLLATE pg_catalog."default",
    created_at  timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT files_pkey PRIMARY KEY (file_id),
    CONSTRAINT files_notebook_id_fkey FOREIGN KEY (notebook_id)
        REFERENCES public.notebooks (notebook_id)
        ON UPDATE NO ACTION
        ON DELETE CASCADE
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_files_notebook
    ON public.files (notebook_id);

-- ─── embeddings ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.embeddings
(
    embedding_id uuid NOT NULL DEFAULT gen_random_uuid(),
    file_id      uuid,
    chunk_text   text COLLATE pg_catalog."default",
    embedding    vector(1024),
    chunk_index  integer,
    metadata     jsonb,
    text_search  tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, chunk_text)) STORED,
    created_at   timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT embeddings_pkey PRIMARY KEY (embedding_id)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS embeddings_embedding_idx
    ON public.embeddings USING hnsw (embedding vector_cosine_ops)
    TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS text_search_idx
    ON public.embeddings USING gin (text_search)
    TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_embeddings_file
    ON public.embeddings (file_id);

ALTER TABLE public.embeddings
    ADD CONSTRAINT embeddings_file_id_fkey FOREIGN KEY (file_id)
    REFERENCES public.files (file_id)
    ON UPDATE NO ACTION
    ON DELETE CASCADE;

-- ─── messages ───────────────────────────────────────────────────────────────
-- Single messages table. Owned by notebook_id.
-- No conversation_id — notebook = conversation.

CREATE TABLE IF NOT EXISTS public.messages
(
    message_id  uuid NOT NULL DEFAULT gen_random_uuid(),
    notebook_id uuid,
    role        character varying(10) COLLATE pg_catalog."default" NOT NULL,
    text        text COLLATE pg_catalog."default" NOT NULL,
    sources     jsonb,
    artifacts   jsonb,
    created_at  timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT messages_pkey PRIMARY KEY (message_id),
    CONSTRAINT messages_notebook_id_fkey FOREIGN KEY (notebook_id)
        REFERENCES public.notebooks (notebook_id)
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT messages_role_check
        CHECK (role::text = ANY (ARRAY['user','assistant','error']::text[]))
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_messages_notebook
    ON public.messages (notebook_id);

-- ─── runs ───────────────────────────────────────────────────────────────────
-- Ephemeral orchestration units. Persisted to survive page refresh.
-- Only the stop button (POST /v1/runs/{id}/cancel) terminates a run.

CREATE TABLE IF NOT EXISTS public.runs
(
    id          uuid NOT NULL DEFAULT gen_random_uuid(),
    notebook_id uuid NOT NULL,
    status      character varying(20) COLLATE pg_catalog."default" NOT NULL DEFAULT 'pending',
    goal        text,
    plan        jsonb,
    result      jsonb,
    created_at  timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at  timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT runs_pkey PRIMARY KEY (id),
    CONSTRAINT runs_notebook_id_fkey FOREIGN KEY (notebook_id)
        REFERENCES public.notebooks (notebook_id)
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT runs_status_check
        CHECK (status::text = ANY (ARRAY['pending','running','completed','failed','cancelled']::text[]))
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_runs_notebook
    ON public.runs (notebook_id);

-- ─── run_events ─────────────────────────────────────────────────────────────
-- SSE event log for each run. Used for full replay on reconnect.

CREATE TABLE IF NOT EXISTS public.run_events
(
    id         serial PRIMARY KEY,
    run_id     uuid NOT NULL,
    seq        integer NOT NULL,
    event_type character varying(30) COLLATE pg_catalog."default" NOT NULL,
    payload    jsonb,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT run_events_run_id_fkey FOREIGN KEY (run_id)
        REFERENCES public.runs (id)
        ON UPDATE NO ACTION
        ON DELETE CASCADE
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_run_events_run_seq
    ON public.run_events (run_id, seq);

-- ─── sessions ────────────────────────────────────────────────────────────────
-- Opaque bearer tokens stored server-side; cookie holds only the token.

CREATE TABLE IF NOT EXISTS public.sessions
(
    token      text COLLATE pg_catalog."default" NOT NULL,
    user_id    uuid NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone NOT NULL,
    CONSTRAINT sessions_pkey PRIMARY KEY (token),
    CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES public.users (user_id)
        ON UPDATE NO ACTION
        ON DELETE CASCADE
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_sessions_user
    ON public.sessions (user_id);

CREATE INDEX IF NOT EXISTS idx_sessions_expires
    ON public.sessions (expires_at);

-- ─── runtime_config ──────────────────────────────────────────────────────────
-- Admin-tunable runtime variables (Option A: write-through cache).
-- DB is the source of truth; the process mirrors rows onto the Settings
-- singleton at boot (load_runtime_overrides) and on every PUT (strong
-- consistency within this instance — NOT eventual). Restart-apply keys take
-- full effect after `docker compose restart backend`; live keys apply to the
-- next request immediately.

CREATE TABLE IF NOT EXISTS public.runtime_config
(
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_by text,
    updated_at timestamptz NOT NULL DEFAULT NOW()
) TABLESPACE pg_default;
