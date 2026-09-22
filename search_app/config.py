import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
    MONGODB_URI = os.environ.get("MONGODB_URI", "")
    MONGODB_DB = os.environ.get("MONGODB_DB", "TechDrip")
    MONGODB_COLLECTION = os.environ.get("MONGODB_COLLECTION", "passages")
    VECTOR_INDEX = os.environ.get("VECTOR_INDEX", "passage_vector_index")
    SEARCH_INDEX = os.environ.get("SEARCH_INDEX", "passage_search_index")
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "voyage-4")
    SEARCH_LIMIT = int(os.environ.get("SEARCH_LIMIT", "10"))
    SNIPPETS_PER_EPISODE = int(os.environ.get("SNIPPETS_PER_EPISODE", "3"))
