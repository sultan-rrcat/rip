from sentence_transformers import SentenceTransformer
import config
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from uuid import uuid4


class RagPipeline:
    def __init__(self):
        self.embedding_model = SentenceTransformer(config.ALL_MINILM_L6_V2_MODEL_PATH)

    def document_loader(self, file):
        try:
            loader = PyPDFLoader(file)
            documents = loader.load_and_split()
            print(f"Loaded {len(documents)} pages.")
            # print(f"pages[0]: {documents[0].page_content}")
            return documents
        except Exception as e:
            print(f"Error {e} while loading pdf: {file}")
            return 0
        # return documents

    def chunk_documents(self, documents):
        try:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=500,
                chunk_overlap=50,  # increase for large documents with contextual overlaping
            )
            chunk_documents = splitter.split_documents(documents)
            chunks = [chunk.page_content for chunk in chunk_documents]
            print(f"Created {len(chunks)} chunks.")
            return chunks
        except Exception as e:
            print(f"Error splitting: {e}")
            return 0

    def generate_embeddings(self, chunks):
        try:
            embeddings = self.embedding_model.encode(chunks)
            print(f"Shape of embeddings: {embeddings.shape}")
            return embeddings
        except Exception as e:
            print(f"Error while generating embeddings: {e}")
            return 0

    def store_chunks_and_embeddings(self, file_id, chunks, embeddings):
        try:
            print(
                f"Type of chunks: {type(chunks)}\nType of embeddings: {type(embeddings)}"
            )
            with config.pg_connection() as conn:
                with conn.cursor() as cur:

                    for chunk, embedding in zip(chunks, embeddings):
                        cur.execute(
                            """
                            INSERT INTO embeddings_test(file_id, chunk_text, embedding)
                            VALUES(%s, %s, %s)
                            """,
                            (file_id, chunk, embedding.tolist()),
                        )
        except Exception as e:
            print(f"Error while storing embeddings: {e}")
            return 0

    def retrieve_context():
        pass




if __name__ == "__main__":
    obj = RagPipeline()

    file_path = r"C:\Users\trainee\Desktop\Projects\CD_lab_report.pdf"
    documents = obj.document_loader(file_path)
    if documents:
        chunks = obj.chunk_documents(documents)

    if chunks:
        embeddings = obj.generate_embeddings(chunks)
        obj.store_chunks_and_embeddings(chunks, embeddings)
