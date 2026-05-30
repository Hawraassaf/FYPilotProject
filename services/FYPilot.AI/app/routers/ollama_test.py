from fastapi import APIRouter
import requests

router = APIRouter()


@router.get("/test-ollama")
def test_ollama():
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "phi3",
                "prompt": "Say hello from FYPilot in one short sentence.",
                "stream": False
            },
            timeout=60
        )

        response.raise_for_status()
        data = response.json()

        return {
            "status": "ok",
            "model": "phi3",
            "response": data.get("response")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }