-- Table: public.embeddings_test

-- DROP TABLE IF EXISTS public.embeddings_test;

CREATE TABLE IF NOT EXISTS public.embeddings_test
(
    embedding_id uuid NOT NULL DEFAULT gen_random_uuid(),
    file_id uuid,
    chunk_text text COLLATE pg_catalog."default",
    embedding vector(1024),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    metadata jsonb,
    text_search tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, chunk_text)) STORED,
    chunk_index integer,
    CONSTRAINT embeddings_test_pkey PRIMARY KEY (embedding_id),
    CONSTRAINT embeddings_test_file_id_fkey FOREIGN KEY (file_id)
        REFERENCES public.files (file_id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.embeddings_test
    OWNER to trainee;
-- Index: embeddings_test_embedding_idx

-- DROP INDEX IF EXISTS public.embeddings_test_embedding_idx;

CREATE INDEX IF NOT EXISTS embeddings_test_embedding_idx
    ON public.embeddings_test USING hnsw
    (embedding vector_cosine_ops)
    TABLESPACE pg_default;
-- Index: text_search_idx

-- DROP INDEX IF EXISTS public.text_search_idx;

CREATE INDEX IF NOT EXISTS text_search_idx
    ON public.embeddings_test USING gin
    (text_search)
    TABLESPACE pg_default;


-- Table: public.files

-- DROP TABLE IF EXISTS public.files;

CREATE TABLE IF NOT EXISTS public.files
(
    file_id uuid NOT NULL DEFAULT gen_random_uuid(),
    notebook_id uuid,
    file_name text COLLATE pg_catalog."default" NOT NULL,
    file_size integer,
    file_status character varying(20) COLLATE pg_catalog."default",
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT files_pkey PRIMARY KEY (file_id),
    CONSTRAINT files_notebook_id_fkey FOREIGN KEY (notebook_id)
        REFERENCES public.notebooks (notebook_id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.files
    OWNER to trainee;


-- Table: public.messages

-- DROP TABLE IF EXISTS public.messages;

CREATE TABLE IF NOT EXISTS public.messages
(
    message_id uuid NOT NULL DEFAULT gen_random_uuid(),
    notebook_id uuid,
    role character varying(10) COLLATE pg_catalog."default" NOT NULL,
    text text COLLATE pg_catalog."default" NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    sources jsonb,
    CONSTRAINT messages_pkey PRIMARY KEY (message_id),
    CONSTRAINT messages_notebook_id_fkey FOREIGN KEY (notebook_id)
        REFERENCES public.notebooks (notebook_id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT messages_role_check CHECK (role::text = ANY (ARRAY['user'::character varying, 'assistant'::character varying, 'error'::character varying]::text[]))
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.messages
    OWNER to trainee;

-- Table: public.notebooks

-- DROP TABLE IF EXISTS public.notebooks;

CREATE TABLE IF NOT EXISTS public.notebooks
(
    notebook_id uuid NOT NULL DEFAULT gen_random_uuid(),
    notebook_name text COLLATE pg_catalog."default" NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT notebooks_pkey PRIMARY KEY (notebook_id)
)

TABLESPACE pg_default;

ALTER TABLE IF EXISTS public.notebooks
    OWNER to trainee;