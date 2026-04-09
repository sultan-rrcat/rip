-- enable uuid generator
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE notebooks (
    notebook_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notebook_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notebook_id UUID REFERENCES notebooks(notebook_id) ON DELETE CASCADE,
    role VARCHAR(10) CHECK (role IN ('user', 'assistant', 'error')) NOT NULL,
    text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE files (
    file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    notebook_id UUID REFERENCES notebooks(notebook_id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    file_size INTEGER,
    file_status VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- Drop dependent tables first
-- DROP TABLE IF EXISTS files CASCADE;
-- DROP TABLE IF EXISTS messages CASCADE;
-- DROP TABLE IF EXISTS notebooks CASCADE;

-- -- Optionally drop the extension if you no longer need UUID generation
-- DROP EXTENSION IF EXISTS "pgcrypto";