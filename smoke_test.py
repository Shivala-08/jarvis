"""Smoke test: send one prompt to Ollama and print the response."""
import ollama

from core.config import get_default_model


def main():
    response = ollama.chat(
        model=get_default_model(),
        messages=[{"role": "user", "content": "Say hello in one sentence."}],
    )
    print("Response:", response["message"]["content"])
    print("\n✅ Ollama round-trip successful — no paid API used.")


if __name__ == "__main__":
    main()
