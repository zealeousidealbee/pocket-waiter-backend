from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel

from database.db import engine, Base, get_db
from models import user, menu, table, order
from schemas.menu import MenuItemCreate, MenuItemResponse

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pocket Waiter API", version="1.0.0")


class CategoryCreate(BaseModel):
    name: str

class CategoryResponse(CategoryCreate):
    id: int
    class Config:
        from_attributes = True

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
    table_number: int
    status: str
    total_price: float
    class Config:
        from_attributes = True



@app.get("/")
def read_root():
    return {"message": "Welcome to Pocket Waiter API! All systems go."}


@app.post("/categories/", response_model=CategoryResponse)
def create_category(category: CategoryCreate, db: Session = Depends(get_db)):
    db_cat = menu.Category(name=category.name)
    db.add(db_cat)
    db.commit()
    db.refresh(db_cat)
    return db_cat


@app.post("/menu/", response_model=MenuItemResponse)
def create_menu_item(item: MenuItemCreate, db: Session = Depends(get_db)):
    db_item = menu.MenuItem(**item.model_dump())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item

@app.get("/menu/", response_model=List[MenuItemResponse])
def get_menu(db: Session = Depends(get_db)):
    return db.query(menu.MenuItem).all()


@app.post("/tables/", response_model=TableResponse)
def create_table(tbl: TableCreate, db: Session = Depends(get_db)):
    db_table = table.Table(**tbl.model_dump())
    db.add(db_table)
    db.commit()
    db.refresh(db_table)
    return db_table

@app.get("/tables/", response_model=List[TableResponse])
def get_tables(db: Session = Depends(get_db)):
    return db.query(table.Table).all()


@app.post("/orders/", response_model=OrderResponse)
def create_order(ord_data: OrderCreate, db: Session = Depends(get_db)):
    total = 0.0
    
    db_order = order.Order(table_number=ord_data.table_number, status="NEW")
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    
    for item_data in ord_data.items:
        menu_item = db.query(menu.MenuItem).filter(menu.MenuItem.id == item_data.menu_item_id).first()
        if menu_item:
            total += menu_item.price * item_data.quantity
            db_item = order.OrderItem(
                order_id=db_order.id,
                menu_item_id=item_data.menu_item_id,
                quantity=item_data.quantity
            )
            db.add(db_item)

    db_order.total_price = total
    db.commit()
    db.refresh(db_order)
    return db_order

@app.get("/orders/", response_model=List[OrderResponse])
def get_orders(db: Session = Depends(get_db)):
    return db.query(order.Order).all()

@app.patch("/orders/{order_id}/status", response_model=OrderResponse)
def update_order_status(order_id: int, new_status: str, db: Session = Depends(get_db)):
    db_order = db.query(order.Order).filter(order.Order.id == order_id).first()
    if not db_order:
        raise HTTPException(status_code=404, detail="Order not found")
    db_order.status = new_status
    db.commit()
    db.refresh(db_order)
    return db_order
