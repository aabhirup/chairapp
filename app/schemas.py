from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class UserBase(BaseModel):
    username: str

class UserCreate(UserBase):
    password: str
    profile_image_url: str

class User(UserBase):
    id: int
    profile_image_url: Optional[str] = None
    
    class Config:
        from_attributes = True

class BookingBase(BaseModel):
    duration_minutes: int

class Booking(BookingBase):
    id: int
    user_id: int
    start_time: datetime
    end_time: datetime
    is_active: bool

    class Config:
        from_attributes = True

class ChatMessageBase(BaseModel):
    content: str

class ChatMessage(ChatMessageBase):
    id: int
    user_id: int
    timestamp: datetime
    username: Optional[str] = None # Calculated field for display

    class Config:
        from_attributes = True
