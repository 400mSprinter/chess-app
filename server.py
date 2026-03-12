# Created: 2026-03-12 10:00
"""
Chess multiplayer WebSocket server.
Serves index.html and relays moves between two players in a room.

Run from project root:
  python -m uvicorn server:app --host 0.0.0.0 --port 8080 --app-dir chess
"""
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

app = FastAPI()
HERE = os.path.dirname(os.path.abspath(__file__))

# room_id -> {white: ws|None, black: ws|None, fen: str|None, last_move: dict|None}
rooms: dict = {}


@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(HERE, "index.html"))


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
