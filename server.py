# Created: 2026-03-12 10:00
"""
Chess multiplayer WebSocket server.
Serves index.html and relays moves between two players in a room.

Run from project root:
  python -m uvicorn server:app --host 0.0.0.0 --port 8080 --app-dir chess
"""
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI()
HERE = os.path.dirname(os.path.abspath(__file__))

# room_id -> {white: ws|None, black: ws|None, fen: str|None, last_move: dict|None}
rooms: dict = {}

with open(os.path.join(HERE, "index.html"), "r", encoding="utf-8") as _f:
    _INDEX_HTML = _f.read()


_anthropic = None

def _get_anthropic():
    global _anthropic
    if _anthropic is None:
        import anthropic
        _anthropic = anthropic.Anthropic()
    return _anthropic

_HINT_SYSTEM = [{
    "type": "text",
    "text": (
        "You are a friendly chess coach explaining moves to an elementary school student (ages 6-10). "
        "Keep your explanation to 2-3 short, simple sentences. Use everyday words a kid would understand. "
        "Be encouraging and include 1-2 fun emojis. "
        "Focus on WHY this is the best move — what danger it creates, what danger it stops, or what advantage it wins. "
        "Describe pieces by their names (King, Queen, Rook, etc.), not chess notation."
    ),
    "cache_control": {"type": "ephemeral"}
}]


class HintRequest(BaseModel):
    fen: str
    move_san: str
    piece: str
    from_sq: str
    to_sq: str
    captures: Optional[str] = None
    gives_check: bool = False
    checkmate: bool = False
    promotion: Optional[str] = None


@app.post("/hint")
async def get_hint(req: HintRequest):
    try:
        client = _get_anthropic()
        desc = f"{req.piece} moves from {req.from_sq} to {req.to_sq}"
        if req.captures:
            desc += f", capturing the opponent's {req.captures}"
        if req.promotion:
            desc += f", and promotes to a {req.promotion}"
        if req.checkmate:
            desc += " — and this wins the game with checkmate!"
        elif req.gives_check:
            desc += " — and this puts the opponent's King in check!"

        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=300,
            thinking={"type": "adaptive"},
            system=_HINT_SYSTEM,
            messages=[{"role": "user", "content": (
                f"The best chess move is: {desc} (chess notation: {req.move_san}). "
                "Please explain in 2-3 simple sentences why this is the best move right now."
            )}],
        )
        explanation = next((b.text for b in response.content if b.type == "text"), "")
        return {"explanation": explanation}
    except Exception as e:
        return {"explanation": None, "error": str(e)}


@app.get("/")
async def serve_index():
    return HTMLResponse(_INDEX_HTML)


@app.websocket("/ws/{room_id}/{color}")
async def ws_endpoint(ws: WebSocket, room_id: str, color: str):
    if color not in ("white", "black"):
        await ws.close(1008)
        return

    await ws.accept()

    if room_id not in rooms:
        rooms[room_id] = {"white": None, "black": None, "fen": None, "last_move": None}

    room = rooms[room_id]
    other = "black" if color == "white" else "white"

    if room[color] is not None:
        await ws.send_json({"type": "error", "msg": "That seat is already taken."})
        await ws.close()
        return

    room[color] = ws
    await ws.send_json({
        "type": "init",
        "color": color,
        "fen": room["fen"],
        "last_move": room["last_move"],
        "opponent_online": room[other] is not None,
    })

    if room[other]:
        try:
            await room[other].send_json({"type": "opponent_joined"})
        except Exception:
            room[other] = None

    try:
        while True:
            msg = await ws.receive_json()
            # Persist game state for late joiners
            if msg.get("type") == "move":
                room["fen"] = msg.get("fen")
                room["last_move"] = msg.get("move")
            # Relay to the other player
            if room[other]:
                try:
                    await room[other].send_json(msg)
                except Exception:
                    room[other] = None
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        room[color] = None
        if room[other]:
            try:
                await room[other].send_json({"type": "opponent_left"})
            except Exception:
                room[other] = None
        if not any(room[c] for c in ("white", "black")):
            rooms.pop(room_id, None)
