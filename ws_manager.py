from typing import Dict, List
import jwt
from fastapi import WebSocket
from sqlalchemy.orm import Session

from auth import SECRET_KEY, ALGORITHM
from models.user import User


class ConnectionManager:
    def __init__(self) -> None:
        # Храним активные соединения: websocket -> объект пользователя
        self.active_connections: Dict[WebSocket, User] = {}

    async def connect(self, websocket: WebSocket, user: User) -> None:
        await websocket.accept()
        self.active_connections[websocket] = user

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.pop(websocket, None)

    async def broadcast(self, message: dict) -> None:
        disconnected: List[WebSocket] = []
        for connection in list(self.active_connections.keys()):
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


def authenticate_ws_token(token: str | None, db: Session) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            return None
    except jwt.PyJWTError:
        return None

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None or not user.is_active:
        return None
    return user
