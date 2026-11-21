#DONE RIGHT

from datetime import datetime
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)
from database.db import Base, engine, get_db
from models import menu, order, table, user
from schemas.menu import MenuItemCreate, MenuItemResponse
from ws_manager import authenticate_ws_token, manager

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pocket Waiter API", version="1.0.0")


class UserRegister(BaseModel):
    username: str
    email: str
    password: str
    role: str = "CLIENT"


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CategoryCreate(BaseModel):
    name: str


class CategoryResponse(CategoryCreate):
    id: int

    class Config:
        from_attributes = True


class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    ingredients: Optional[str] = None
    allergens: Optional[str] = None
    is_available: Optional[bool] = None
    category_id: Optional[int] = None


class TableCreate(BaseModel):
    number: int
    seats: int
    zone: str


class TableResponse(TableCreate):
    id: int
    is_available: bool

    class Config:
        from_attributes = True


class OrderItemSchema(BaseModel):
    menu_item_id: int
    quantity: int = 1


class OrderCreate(BaseModel):
    table_number: int
    items: List[OrderItemSchema]


class OrderResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    table_number: int
    status: str
    total_price: float
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ReservationCreate(BaseModel):
    table_id: int
    guest_count: int
    reservation_time: datetime


OrderStatus = Literal["NEW", "IN_PROGRESS", "READY", "SERVED", "PAID", "CANCELLED"]
ReservationStatus = Literal["pending", "confirmed", "cancelled", "completed"]


class ReservationStatusUpdate(BaseModel):
    status: ReservationStatus = Field(
        ...,
        description="Allowed reservation statuses: pending, confirmed, cancelled, completed",
    )


class ReservationResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    table_id: int
    guest_count: int
    reservation_time: datetime
    status: str

    class Config:
        from_attributes = True


@app.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    existing_user = (
        db.query(user.User)
        .filter(
            (user.User.username == user_data.username)
            | (user.User.email == user_data.email)
        )
        .first()
    )
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already registered")

    allowed_roles = {"CLIENT", "WAITER", "ADMIN"}
    role_to_set = (
        user_data.role.upper()
        if user_data.role.upper() in allowed_roles
        else "CLIENT"
    )

    new_user = user.User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        role=role_to_set,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@app.post("/auth/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    db_user = (
        db.query(user.User)
        .filter(
            (user.User.username == form_data.username)
            | (user.User.email == form_data.username)
        )
        .first()
    )
    if not db_user or not verify_password(form_data.password, db_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(data={"sub": str(db_user.id), "role": db_user.role})
    return {"access_token": token, "token_type": "bearer"}


@app.get("/auth/me", response_model=UserResponse)
def read_current_user(current_user: user.User = Depends(get_current_user)):
    return current_user


@app.get("/")
def root():
    return {"message": "Pocket Waiter API is running"}


@app.post("/categories/", response_model=CategoryResponse)
def create_category(
    category: CategoryCreate,
    db: Session = Depends(get_db),
    _: user.User = Depends(require_role(["WAITER", "ADMIN"])),
):
    db_cat = menu.Category(name=category.name)
    db.add(db_cat)
    db.commit()
    db.refresh(db_cat)
    return db_cat


@app.post("/menu/", response_model=MenuItemResponse)
def create_menu_item(
    item: MenuItemCreate,
    db: Session = Depends(get_db),
    _: user.User = Depends(require_role(["WAITER", "ADMIN"])),
):
    db_item = menu.MenuItem(**item.model_dump())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


@app.get("/menu/", response_model=List[MenuItemResponse])
def get_menu(
    category_id: Optional[int] = None,
    only_available: bool = False,
    db: Session = Depends(get_db),
):
    query = db.query(menu.MenuItem)
    if category_id is not None:
        query = query.filter(menu.MenuItem.category_id == category_id)
    if only_available:
        query = query.filter(menu.MenuItem.is_available.is_(True))
    return query.all()


@app.patch("/menu/{item_id}", response_model=MenuItemResponse)
def update_menu_item(
    item_id: int,
    item_update: MenuItemUpdate,
    db: Session = Depends(get_db),
    _: user.User = Depends(require_role(["WAITER", "ADMIN"])),
):
    db_item = db.query(menu.MenuItem).filter(menu.MenuItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Menu item not found")

    update_data = item_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_item, field, value)

    db.commit()
    db.refresh(db_item)
    return db_item


@app.post("/tables/", response_model=TableResponse)
def create_table(
    tbl: TableCreate,
    db: Session = Depends(get_db),
    _: user.User = Depends(require_role(["WAITER", "ADMIN"])),
):
    db_table = table.Table(**tbl.model_dump())
    db.add(db_table)
    db.commit()
    db.refresh(db_table)
    return db_table


@app.get("/tables/", response_model=List[TableResponse])
def get_tables(db: Session = Depends(get_db)):
    return db.query(table.Table).all()


@app.post("/orders/", response_model=OrderResponse)
async def create_order(
    ord_data: OrderCreate,
    db: Session = Depends(get_db),
    current_user: user.User = Depends(get_current_user),
):
    db_table = (
        db.query(table.Table)
        .filter(table.Table.number == ord_data.table_number)
        .first()
    )
    if db_table is not None:
        if not db_table.is_available:
            raise HTTPException(
                status_code=400, detail="Table is currently occupied"
            )
        db_table.is_available = False

    total = 0.0

    db_order = order.Order(
        user_id=current_user.id,
        table_number=ord_data.table_number,
        status="NEW",
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    for item_data in ord_data.items:
        menu_item = (
            db.query(menu.MenuItem)
            .filter(menu.MenuItem.id == item_data.menu_item_id)
            .first()
        )
        if not menu_item:
            raise HTTPException(
                status_code=404,
                detail=f"Menu item {item_data.menu_item_id} not found",
            )
        if menu_item.is_available is False:
            raise HTTPException(
                status_code=400,
                detail=f"Menu item '{menu_item.name}' is currently unavailable",
            )

        total += menu_item.price * item_data.quantity
        db_item = order.OrderItem(
            order_id=db_order.id,
            menu_item_id=item_data.menu_item_id,
            quantity=item_data.quantity,
        )
        db.add(db_item)

    db_order.total_price = total
    db.commit()
    db.refresh(db_order)

    await manager.broadcast(
        {
            "event": "order_created",
            "order": {
                "id": db_order.id,
                "table_number": db_order.table_number,
                "status": db_order.status,
                "total_price": db_order.total_price,
            },
        }
    )

    return db_order


@app.get("/orders/", response_model=List[OrderResponse])
def get_orders(db: Session = Depends(get_db)):
    return db.query(order.Order).all()


@app.patch("/orders/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: int,
    new_status: OrderStatus = Query(
        ...,
        description="Allowed order statuses: NEW, IN_PROGRESS, READY, SERVED, PAID, CANCELLED",
    ),
    db: Session = Depends(get_db),
    _: user.User = Depends(require_role(["WAITER", "ADMIN"])),
):
    db_order = db.query(order.Order).filter(order.Order.id == order_id).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")

    previous_status = (db_order.status or "").strip().upper()
    normalized_status = new_status.strip().upper()
    db_order.status = normalized_status

    closed_statuses = {"PAID", "CANCELLED"}
    db_table = (
        db.query(table.Table)
        .filter(table.Table.number == db_order.table_number)
        .first()
    )

    if normalized_status in closed_statuses:
        active_orders_count = (
            db.query(order.Order)
            .filter(
                order.Order.table_number == db_order.table_number,
                order.Order.id != db_order.id,
                order.Order.status.notin_(list(closed_statuses)),
            )
            .count()
        )
        if active_orders_count == 0 and db_table is not None:
            db_table.is_available = True
    elif previous_status in closed_statuses and db_table is not None:
        db_table.is_available = False

    db.commit()
    db.refresh(db_order)

    await manager.broadcast(
        {
            "event": "order_status_updated",
            "order": {
                "id": db_order.id,
                "table_number": db_order.table_number,
                "status": db_order.status,
                "total_price": db_order.total_price,
            },
        }
    )

    return db_order


@app.delete("/orders/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    _: user.User = Depends(require_role(["WAITER", "ADMIN"])),
):
    db_order = db.query(order.Order).filter(order.Order.id == order_id).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")

    table_num = db_order.table_number
    was_closed = (db_order.status or "").strip().upper() in {"PAID", "CANCELLED"}

    db.delete(db_order)
    db.flush()

    if not was_closed:
        active_orders_count = (
            db.query(order.Order)
            .filter(
                order.Order.table_number == table_num,
                order.Order.status.notin_(["PAID", "CANCELLED"]),
            )
            .count()
        )
        if active_orders_count == 0:
            db_table = (
                db.query(table.Table)
                .filter(table.Table.number == table_num)
                .first()
            )
            if db_table is not None:
                db_table.is_available = True

    db.commit()
    return None


def sync_table_availability(db: Session, db_table: table.Table | None) -> None:
    if db_table is None:
        return

    has_active_orders = (
        db.query(order.Order)
        .filter(
            order.Order.table_number == db_table.number,
            order.Order.status.notin_(["PAID", "CANCELLED"]),
        )
        .first()
        is not None
    )

    has_active_reservations = (
        db.query(order.Reservation)
        .filter(
            order.Reservation.table_id == db_table.id,
            order.Reservation.status.notin_(["cancelled", "completed"]),
        )
        .first()
        is not None
    )

    db_table.is_available = not (has_active_orders or has_active_reservations)


@app.post(
    "/reservations/",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_reservation(
    res_data: ReservationCreate,
    db: Session = Depends(get_db),
    current_user: user.User = Depends(get_current_user),
):
    db_table = (
        db.query(table.Table).filter(table.Table.id == res_data.table_id).first()
    )
    if not db_table:
        raise HTTPException(status_code=404, detail="Table not found")
    if res_data.guest_count <= 0 or res_data.guest_count > db_table.seats:
        raise HTTPException(
            status_code=400,
            detail=f"Guest count must be between 1 and {db_table.seats}",
        )

    existing_conflict = (
        db.query(order.Reservation)
        .filter(
            order.Reservation.table_id == res_data.table_id,
            order.Reservation.reservation_time == res_data.reservation_time,
            order.Reservation.status.notin_(["cancelled", "completed"]),
        )
        .first()
    )
    if existing_conflict:
        raise HTTPException(
            status_code=409,
            detail="Table already has a reservation at this time",
        )

    db_res = order.Reservation(
        user_id=current_user.id,
        table_id=res_data.table_id,
        guest_count=res_data.guest_count,
        reservation_time=res_data.reservation_time,
        status="pending",
    )
    db_table.is_available = False
    db.add(db_res)
    db.commit()
    db.refresh(db_res)
    return db_res


@app.get("/reservations/", response_model=List[ReservationResponse])
def get_reservations(
    db: Session = Depends(get_db),
    current_user: user.User = Depends(get_current_user),
):
    role = (current_user.role or "").upper()
    if role in {"WAITER", "ADMIN"}:
        return db.query(order.Reservation).all()
    return (
        db.query(order.Reservation)
        .filter(order.Reservation.user_id == current_user.id)
        .all()
    )


@app.patch("/reservations/{reservation_id}/status", response_model=ReservationResponse)
def update_reservation_status(
    reservation_id: int,
    status_update: ReservationStatusUpdate,
    db: Session = Depends(get_db),
    _: user.User = Depends(require_role(["WAITER", "ADMIN"])),
):
    db_res = (
        db.query(order.Reservation)
        .filter(order.Reservation.id == reservation_id)
        .first()
    )
    if not db_res:
        raise HTTPException(status_code=404, detail="Reservation not found")

    db_res.status = status_update.status
    db.flush()

    db_table = (
        db.query(table.Table).filter(table.Table.id == db_res.table_id).first()
    )
    if db_table is not None:
        if status_update.status in {"pending", "confirmed"}:
            db_table.is_available = False
        else:
            sync_table_availability(db, db_table)

    db.commit()
    db.refresh(db_res)
    return db_res


@app.delete("/reservations/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reservation(
    reservation_id: int,
    db: Session = Depends(get_db),
    _: user.User = Depends(require_role(["WAITER", "ADMIN"])),
):
    db_res = (
        db.query(order.Reservation)
        .filter(order.Reservation.id == reservation_id)
        .first()
    )
    if not db_res:
        raise HTTPException(status_code=404, detail="Reservation not found")

    db_table = (
        db.query(table.Table).filter(table.Table.id == db_res.table_id).first()
    )
    db.delete(db_res)
    db.flush()

    sync_table_availability(db, db_table)
    db.commit()
    return None


@app.websocket("/ws/orders")
async def orders_websocket(websocket: WebSocket, db: Session = Depends(get_db)):
    token = websocket.query_params.get("token")
    current_user = authenticate_ws_token(token, db)
    if current_user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, current_user)
    try:
        await websocket.send_json(
            {
                "event": "connected",
                "user": {
                    "id": current_user.id,
                    "username": current_user.username,
                    "role": current_user.role,
                },
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
