from sqlalchemy import Column, Integer, String, Boolean
from database.db import Base

class Table(Base):
    __tablename__ = "tables"

    id = Column(Integer, primary_key=True, index=True)
    number = Column(Integer, unique=True, nullable=False)
    seats = Column(Integer, nullable=False)
    zone = Column(String, nullable=True)  
    is_available = Column(Boolean, default=True)
