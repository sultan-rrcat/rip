-- enable uuid generator
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS public.notebooks
(
    notebook_id uuid NOT NULL DEFAULT gen_random_uuid(),
    notebook_name text COLLATE pg_catalog."default" NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT notebooks_pkey PRIMARY KEY (notebook_id)
)

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


CREATE TABLE IF NOT EXISTS public.embeddings_test
(
    embedding_id uuid NOT NULL DEFAULT gen_random_uuid(),
    file_id uuid,
    chunk_text text COLLATE pg_catalog."default",
    embedding vector(384),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    metadata jsonb,
    CONSTRAINT embeddings_test_pkey PRIMARY KEY (embedding_id),
    CONSTRAINT embeddings_test_file_id_fkey FOREIGN KEY (file_id)
        REFERENCES public.files (file_id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
)

-- Drop dependent tables first
-- DROP TABLE IF EXISTS files CASCADE;
-- DROP TABLE IF EXISTS messages CASCADE;
-- DROP TABLE IF EXISTS notebooks CASCADE;
-- DROP TABLE IF EXISTS embeddings CASCADE;

-- -- Optionally drop the extension if you no longer need UUID generation
-- DROP EXTENSION IF EXISTS "pgcrypto";