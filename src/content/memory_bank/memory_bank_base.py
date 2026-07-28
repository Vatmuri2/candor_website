from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import os
import json
import random
import string
from datetime import datetime



from src.content.memory_bank.memory import Memory, MemorySearchResult
from src.utils.storage import research_db

class MemoryBankBase(ABC):
    """Abstract base class for memory bank implementations.
    
    This class defines the standard interface that all memory bank implementations
    must follow. Concrete implementations (e.g., VectorDB, GraphRAG) should inherit
    from this class and implement the abstract methods.
    """
    
    def __init__(self):
        self.memories: List[Memory] = []
        self.session_id: Optional[str] = None
    
    def set_session_id(self, session_id: str) -> None:
        """Set the current session ID for the memory bank.
        
        Args:
            session_id: The ID of the current interview session
        """
        self.session_id = session_id
    
    def generate_memory_id(self) -> str:
        """Generate a short, unique memory ID.
        Format: MEM_MMDDHHMM_{random_chars}
        Example: MEM_03121423_X7K (March 12, 14:23)
        """
        timestamp = datetime.now().strftime("%m%d%H%M")
        random_chars = ''.join(random.choices(string.ascii_uppercase 
                                              + string.digits, k=3))
        return f"MEM_{timestamp}_{random_chars}"
    
    @abstractmethod
    def add_memory(
        self,
        title: str,
        text: str,
        subtopic_links: List[Dict[str, Any]],
        source_interview_question: str,
        source_interview_response: str,
        metadata: Optional[Dict] = None,
        question_ids: Optional[List[str]] = None
    ) -> Memory:
        """Add a new memory to the database.
        
        Args:
            title: Title of the memory
            text: Content of the memory
            subtopic_links: List of subtopic linked with the memory
            source_interview_question: Original question from interview
            source_interview_response: Original response from interview
            metadata: Optional metadata dictionary
            question_ids: Optional list of question IDs that generated this memory
            
        Returns:
            Memory: The created memory object
        """
        pass
    
    @abstractmethod
    def search_memories(self, query: str, k: int = 5) -> List[MemorySearchResult]:
        """Search for similar memories using the query text.
        
        Args:
            query: The search query text
            k: Number of results to return
            
        Returns:
            List[MemorySearchResult]: List of memory search results 
            with similarity scores
        """
        pass
    
    def save_to_file(self, user_id: str) -> None:
        """Persist the memory bank.

        Postgres (via research_db) when a DB is configured — durable, and the
        only thing that actually survives across serverless invocations on
        Vercel; local disk under LOGS_DIR doesn't. Falls back to local disk
        otherwise (laptop testing, ablation runs that read these files back
        directly).
        """
        memories_data = [memory.to_dict() for memory in self.memories]
        embeddings_data = self._get_implementation_data()

        if research_db.is_configured():
            research_db.save_memory_bank(user_id, memories_data, embeddings_data)
            # Also keep a local session-directory copy when running a logged
            # session locally (ablation runs, etc.) — this is a courtesy copy
            # for log inspection, not the source of truth when DB is configured.
            if self.session_id and os.getenv("LOGS_DIR"):
                self._write_local_copy(
                    f"{user_id}/execution_logs/session_{self.session_id}",
                    memories_data, embeddings_data
                )
            return

        self._write_local_copy(user_id, memories_data, embeddings_data)
        if self.session_id:
            self._write_local_copy(
                f"{user_id}/execution_logs/session_{self.session_id}",
                memories_data, embeddings_data
            )

    def _write_local_copy(self, path: str, memories_data: list, embeddings_data: Any) -> None:
        content_filepath = os.getenv("LOGS_DIR", "logs") + f"/{path}/memory_bank_content.json"
        os.makedirs(os.path.dirname(content_filepath), exist_ok=True)
        with open(content_filepath, 'w') as f:
            json.dump({'memories': memories_data}, f, indent=2)

        embedding_filepath = os.getenv("LOGS_DIR", "logs") + f"/{path}/memory_bank_embeddings.json"
        os.makedirs(os.path.dirname(embedding_filepath), exist_ok=True)
        with open(embedding_filepath, 'w') as f:
            json.dump(embeddings_data, f)

    @abstractmethod
    def _get_implementation_data(self) -> Any:
        """Return implementation-specific data (e.g. embeddings) to persist
        alongside the memory list — as plain JSON-serializable data, not a
        file write. The base class decides where it goes (DB or disk).
        """
        pass

    @classmethod
    def load_from_file(cls, user_id: str, base_path: Optional[str] = None) -> 'MemoryBankBase':
        """Load a memory bank.

        With no base_path, uses Postgres when configured (the durable,
        cross-session source of truth), else falls back to local disk.
        An explicit base_path always reads from that local directory
        directly — used for inspecting a specific logged session's saved
        state, not for the live cross-session lookup.
        """
        memory_bank = cls()

        if base_path is None and research_db.is_configured():
            row = research_db.get_memory_bank(user_id)
            if row is not None:
                for memory_data in row.get('memories', []):
                    memory_bank.memories.append(Memory.from_dict(memory_data))
                memory_bank._load_implementation_data(row.get('embeddings', []))
            return memory_bank

        # Determine content filepath based on base_path
        if base_path:
            content_filepath = os.path.join(base_path, "memory_bank_content.json")
        else:
            content_filepath = os.getenv("LOGS_DIR", "logs") + \
                f"/{user_id}/memory_bank_content.json"

        try:
            # Load content
            with open(content_filepath, 'r') as f:
                content_data = json.load(f)

            # Reconstruct memories
            for memory_data in content_data['memories']:
                memory = Memory.from_dict(memory_data)
                memory_bank.memories.append(memory)

            # Load implementation-specific data
            embedding_filepath = os.path.join(base_path, "memory_bank_embeddings.json") if base_path \
                else os.getenv("LOGS_DIR", "logs") + f"/{user_id}/memory_bank_embeddings.json"
            try:
                with open(embedding_filepath, 'r') as f:
                    memory_bank._load_implementation_data(json.load(f))
            except FileNotFoundError:
                pass

        except FileNotFoundError:
            # Create new empty memory bank if files don't exist
            memory_bank.save_to_file(user_id)

        return memory_bank

    @abstractmethod
    def _load_implementation_data(self, data: Any) -> None:
        """Restore implementation-specific data (e.g. embeddings, rebuild a
        vector index) from a previously-saved JSON-serializable structure.
        """
        pass
    
    def get_memory_by_id(self, memory_id: str) -> Optional[Memory]:
        """Get a memory by its ID."""
        return next((m for m in self.memories if m.id == memory_id), None)

    def link_question(self, memory_id: str, question_id: str) -> None:
        """Link a question to a memory.
        
        Args:
            memory_id: ID of the memory
            question_id: ID of the question to link
        """
        memory = self.get_memory_by_id(memory_id)
        if memory and question_id not in memory.question_ids:
            memory.question_ids.append(question_id)

    def get_memories_by_question(self, question_id: str) -> List[Memory]:
        """Get all memories linked to a specific question.
        
        Args:
            question_id: ID of the question
            
        Returns:
            List[Memory]: List of memories linked to the question
        """
        return [m for m in self.memories if question_id in m.question_ids]

    def get_formatted_memories_from_ids(self, memory_ids: List[str], include_source: bool = True) -> str:
        """Get and format memories from memory IDs into XML format.
        
        Args:
            memory_ids: List of memory IDs to format
            include_source: Whether to include source interview response in output
            
        Returns:
            str: XML formatted string of memories, or empty string if no memories
        """
        if not memory_ids:
            return ""
            
        # Track seen source responses to avoid duplicates
        seen_sources = {}  # source_text -> first_memory_id
        memory_texts = []
        
        for memory_id in memory_ids:
            memory = self.get_memory_by_id(memory_id)
            if not memory:
                continue
                
            if include_source:
                source_text = memory.source_interview_response
                if source_text in seen_sources:
                    # Reference the first memory with this source
                    source_xml = (
                        f'<source_interview_response>\n'
                        f'Same as {seen_sources[source_text]}\n'
                        f'</source_interview_response>'
                    )
                else:
                    # First time seeing this source
                    seen_sources[source_text] = memory.id
                    source_xml = (
                        f'<source_interview_response>\n'
                        f'{source_text}\n'
                        f'</source_interview_response>'
                    )
                
                # Build memory XML with modified source
                memory_xml = [
                    '<memory>',
                    f'<title>{memory.title}</title>',
                    f'<summary>{memory.text}</summary>',
                    f'<id>{memory.id}</id>',
                    source_xml,
                    '</memory>'
                ]
                memory_texts.append('\n'.join(memory_xml))
            else:
                memory_texts.append(memory.to_xml(include_source=False))
        
        return "\n\n".join(memory_texts) if memory_texts else ""
