import os
import re
import logging
from typing import List, Dict, Any
import chromadb
from app.config import Config

logger = logging.getLogger(__name__)

class CodeIndexer:
    """Manages ChromaDB vector indexing and RAG-based semantic code search for Flora."""
    
    def __init__(self, chroma_dir: str = Config.CHROMA_DB_DIR):
        self.chroma_dir = chroma_dir
        os.makedirs(self.chroma_dir, exist_ok=True)
        # Persistent Client keeps index files safe on VPS Docker volume
        self.client = chromadb.PersistentClient(path=self.chroma_dir)

    def _get_collection(self, repo_name: str):
        """Get or create a collection for a specific project repository."""
        # Clean collection name (Chroma accepts alphanumeric, underscore, hyphen, 3-63 chars)
        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', repo_name)[:60]
        # ChromaDB built-in default embedding function will be used (all-MiniLM-L6-v2)
        return self.client.get_or_create_collection(name=clean_name)

    def _chunk_code(self, code: str, file_path: str, chunk_size: int = 1500, overlap: int = 200) -> List[Dict[str, Any]]:
        """Split a code file into smart overlapping chunks, preserving function contexts."""
        lines = code.split("\n")
        chunks = []
        current_chunk = []
        current_size = 0
        
        # Simple logical splitter: accumulate lines, try not to break in the middle of functions
        for line_num, line in enumerate(lines, 1):
            current_chunk.append(f"{line_num}|{line}")
            current_size += len(line) + 1
            
            if current_size >= chunk_size:
                # Store chunk with metadata
                chunk_text = "\n".join(current_chunk)
                chunks.append({
                    "text": f"File: {file_path}\n---\n{chunk_text}",
                    "start_line": int(current_chunk[0].split("|")[0]),
                    "end_line": line_num
                })
                # Handle overlap by taking last few lines
                overlap_lines = current_chunk[-max(1, int(overlap / 50)):]
                current_chunk = overlap_lines
                current_size = sum(len(l) for l in current_chunk)
                
        # Append remaining lines
        if current_chunk:
            chunk_text = "\n".join(current_chunk)
            chunks.append({
                "text": f"File: {file_path}\n---\n{chunk_text}",
                "start_line": int(current_chunk[0].split("|")[0]),
                "end_line": len(lines)
            })
            
        return chunks

    def index_project(self, repo_name: str, repo_path: str) -> dict:
        """Scan project repository, extract, chunk, and index all supported code files."""
        collection = self._get_collection(repo_name)
        
        # Extensions we want to index for the startup
        allowed_extensions = {
            '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', 
            '.json', '.yml', '.yaml', '.md', '.go', '.rs', '.sql', '.sh'
        }
        
        ignored_dirs = {
            'node_modules', '.git', '__pycache__', 'venv', '.venv', 'env', 
            'dist', 'build', 'out', '.next', '.nuxt', 'chroma'
        }
        
        documents = []
        metadatas = []
        ids = []
        
        logger.info(f"Indexing project at {repo_path}...")
        
        # Clear existing indexes for this collection to rebuild fresh index
        try:
            # We can delete all items by listing IDs or just recreated the collection
            self.client.delete_collection(collection.name)
            collection = self._get_collection(repo_name)
        except Exception as e:
            logger.warning(f"Could not recreate collection: {e}")

        chunk_counter = 0
        for root, dirs, files in os.walk(repo_path):
            # Prune ignored directories in-place
            dirs[:] = [d for dirs in [dirs] for d in dirs if d not in ignored_dirs]
            
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext not in allowed_extensions:
                    continue
                    
                full_path = os.path.join(root, file)
                # Get relative path inside the repo
                rel_path = os.path.relpath(full_path, repo_path)
                
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        code = f.read()
                        
                    if not code.strip():
                        continue
                        
                    chunks = self._chunk_code(code, rel_path)
                    for chunk in chunks:
                        documents.append(chunk["text"])
                        metadatas.append({
                            "file_path": rel_path,
                            "start_line": chunk["start_line"],
                            "end_line": chunk["end_line"]
                        })
                        ids.append(f"{rel_path}_chunk_{chunk_counter}")
                        chunk_counter += 1
                        
                except Exception as e:
                    logger.error(f"Error reading file {rel_path} for indexing: {e}")

        # Chroma limit batch sizes, let's add in batches of 200 documents
        batch_size = 200
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i+batch_size]
            batch_metas = metadatas[i:i+batch_size]
            batch_ids = ids[i:i+batch_size]
            collection.add(
                documents=batch_docs,
                metadatas=batch_metas,
                ids=batch_ids
            )
            
        logger.info(f"Successfully indexed project '{repo_name}': {chunk_counter} code chunks stored in Vector DB.")
        return {
            "success": True,
            "total_chunks": chunk_counter,
            "message": f"Проект '{repo_name}' полностью проиндексирован! Я разложила твой код по полочкам (создано {chunk_counter} смысловых блоков в ChromaDB) и теперь отлично в нем ориентируюсь. 😉🧠"
        }

    def search_code(self, repo_name: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search the codebase semantically for code snippets or bugs."""
        collection = self._get_collection(repo_name)
        try:
            results = collection.query(
                query_texts=[query],
                n_results=limit
            )
            
            formatted_results = []
            if results and results["documents"]:
                for i in range(len(results["documents"][0])):
                    formatted_results.append({
                        "content": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i],
                        "id": results["ids"][0][i],
                        "distance": results["distances"][0][i] if "distances" in results else None
                    })
            return formatted_results
        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return []
