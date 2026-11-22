from typing import List, Dict, Optional, Generator
from app.llm.local_chat_provider import LocalChatProvider
from app.llm.local_provider import LocalProvider


class LocalLLM:
    def __init__(
            self,
            model: Optional[str] = None,
            embedding_model: Optional[str] = None,
            temperature: float = 0.7,
            max_tokens: Optional[int] = None,
            system_prompt: Optional[str] = None
    ):
        self.chat_provider = LocalChatProvider(model=model, temperature=temperature)
        self.embedding_provider = LocalProvider(model_name=embedding_model)
        self.max_tokens = max_tokens
        self.history: List[Dict[str, str]] = []

        if system_prompt:
            self.history.append({"role": "system", "content": system_prompt})

    def reset_history(self):
        if self.history and self.history[0].get("role") == "system":
            self.history = [self.history[0]]
        else:
            self.history = []

    def chat(self, message: str, save_history: bool = True) -> str:
        return "".join(self.stream(message, save_history))

    def stream(self, message: str, save_history: bool = True) -> Generator[str, None, None]:
        user = {"role": "user", "content": message}
        chunks = []
        try:
            for chunk in self.chat_provider.chat_messages(self.history + [user], self.max_tokens):
                chunks.append(chunk)
                yield chunk
            if save_history:
                self.history.extend([user, {"role": "assistant", "content": "".join(chunks)}])
        except Exception as exc:
            yield f"Error: {exc}"

    def get_embedding(self, text: str) -> List[float]:
        return self.embedding_provider.embed(text)

    def __call__(self, message: str) -> str:
        return self.chat(message)

    def get_history(self) -> List[Dict[str, str]]:
        return self.history