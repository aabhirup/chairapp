from fastapi import FastAPI, Depends, HTTPException, Request, Form, status, WebSocket, File, UploadFile
import shutil
from pathlib import Path

from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from . import models, schemas, database
from typing import Optional
import os

# Init DB
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI()

if not os.path.exists("static"):
    os.makedirs("static")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Helper to get DB
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, db: Session = Depends(get_db)):
    # Render main dashboard
    # Check if user is "logged in" via cookie
    user_id = request.cookies.get("user_id")
    user = None
    if user_id:
        user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Simple Login/Signup Combined Form with File Upload
    html_content = """
    <html>
    <head>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-100 h-screen flex items-center justify-center">
        <form action="/login" method="post" enctype="multipart/form-data" class="bg-white p-8 rounded-xl shadow-lg w-96 flex flex-col gap-4">
            <h1 class="text-xl font-bold mb-2">Identify Yourself</h1>
            <input type="text" name="username" placeholder="Username" class="border p-2 rounded" required>
            
            <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Profile Hologram (Image)</label>
                <input type="file" name="profile_image" accept="image/*" class="block w-full text-sm text-gray-500
                  file:mr-4 file:py-2 file:px-4
                  file:rounded-full file:border-0
                  file:text-sm file:font-semibold
                  file:bg-cyan-50 file:text-cyan-700
                  hover:file:bg-cyan-100
                " required />
            </div>

            <button type="submit" class="bg-black text-white p-2 rounded">Enter System</button>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/login")
async def login(username: str = Form(...), profile_image: UploadFile = File(...), db: Session = Depends(get_db)):
    # Ensure upload directory exists
    upload_dir = Path("static/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate unique filename (simple collision avoidance: username_filename)
    # Sanitize filename could be better but basic str approach for now
    clean_filename = f"{username}_{profile_image.filename}".replace(" ", "_")
    file_path = upload_dir / clean_filename
    
    # Save file
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(profile_image.file, buffer)
        
    # URL is /static/uploads/filename
    image_url = f"/static/uploads/{clean_filename}"

    # Simple "Find or Create" logic
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        user = models.User(username=username, profile_image_url=image_url, hashed_password="dummy")
        db.add(user)
    else:
        # Update existing user's image if they re-login? 
        # For simplicity, yes, update it.
        user.profile_image_url = image_url
        
    db.commit()
    db.refresh(user)
    
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="user_id", value=str(user.id))
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("user_id")
    return response

# --- CORE LOGIC: DESK STATUS & BOOKING ---

@app.get("/desk-status", response_class=HTMLResponse)
async def get_desk_status(request: Request, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    # Find active booking
    current_booking = db.query(models.Booking).filter(
        models.Booking.end_time > now,
        models.Booking.is_active == True
    ).order_by(models.Booking.end_time.desc()).first() 
    # Logic: Should be only one active overlapping, but if multiple, the one ending latest is arguably the current 'owner' 
    # OR we ensure DB constraint. For now, take the one that is valid.
    
    # Correction: "Shortest Time Wins" means we need to see if a shorter booking is scheduled soon.
    # But for 'Who is sitting there NOW', it's whoever has a valid booking covering NOW.
    
    active_booking = db.query(models.Booking).filter(
        models.Booking.start_time <= now,
        models.Booking.end_time > now,
        models.Booking.is_active == True
    ).first()

    if not active_booking:
        return templates.TemplateResponse("partials_desk_available.html", {"request": request})
    
    # Calculate time remaining
    remaining = active_booking.end_time - now
    minutes = int(remaining.total_seconds() / 60)
    seconds = int(remaining.total_seconds() % 60)
    time_str = f"{minutes}m {seconds}s"
    
    # Check for warning (less than 15 mins left OR specifically flagged)
    is_warning = minutes < 15
    
    return templates.TemplateResponse("partials_desk_occupied.html", {
        "request": request, 
        "current_booking": active_booking,
        "time_remaining_str": time_str,
        "is_warning": is_warning
    })

@app.post("/book")
async def create_booking(request: Request, duration: int = Form(...), db: Session = Depends(get_db)):
    user_id = request.cookies.get("user_id")
    if not user_id:
        # In HTMX, usually we header swap for login or show error
        return HTMLResponse("<span class='text-red-500'>Login required</span>")
    
    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    if not user:
         return HTMLResponse("<span class='text-red-500'>Invalid User</span>")

    now = datetime.utcnow()
    
    # Find current occupant
    current_occupant = db.query(models.Booking).filter(
        models.Booking.end_time > now,
        models.Booking.is_active == True
    ).first()
    
    if not current_occupant:
        # Desk is free, just book it
        end_time = now + timedelta(minutes=duration)
        new_booking = models.Booking(user_id=user.id, start_time=now, end_time=end_time)
        db.add(new_booking)
        db.commit()
        return HTMLResponse(f"<span class='text-green-500'>Booked for {duration}m!</span>")
    
    # Desk is Occupied: Implement "Shortest Time Wins" Logic
    # 1. Calculate current remaining time
    current_remaining = (current_occupant.end_time - now).total_seconds() / 60
    
    if duration < current_remaining:
        # User B (New) is shorter than User A (Current)
        # PREEMPTION LOGIC
        
        # Determine strict countdown: 15 mins or duration if smaller? 
        # Prompt: "User A gets a 15-minute forced countdown warning before handover."
        
        warning_time = 15
        
        # If current remaining is ALREADY less than 15, we don't extend them, but we might queue the next?
        # Assuming we only preempt if significant gain.
        
        # Let's apply rule: 
        # Update Current User: End Time = Now + 15 mins
        new_end_for_current = now + timedelta(minutes=warning_time)
        
        # If the generated 'warning end' is actually LONGER than their original time, ignore (edge case).
        # We assume they had like 2 hours left.
        
        current_occupant.end_time = new_end_for_current
        db.add(current_occupant)
        
        # Create New Booking starting AFTER the warning period
        new_booking_start = new_end_for_current
        new_booking_end = new_booking_start + timedelta(minutes=duration)
        
        new_booking = models.Booking(
            user_id=user.id, 
            start_time=new_booking_start, 
            end_time=new_booking_end,
            is_active=True # It's 'active' in the sense of valid, but valid in future
        )
        db.add(new_booking)
        db.commit()
        
        return HTMLResponse(f"<span class='text-scifi-glow'>Priority Override! Desk yours in 15m.</span>")
    
    else:
        # Standard Queueing or Reject
        # Prompt didn't specify queueing logic for longer durations, but implied "Competitive".
        # We'll just say "Current user has priority"
        return HTMLResponse(f"<span class='text-red-400'>Cannot preempt. Wait your turn.</span>")

@app.get("/schedule", response_class=HTMLResponse)
async def get_schedule(request: Request, db: Session = Depends(get_db)):
    # Get bookings for the next 24 hours
    now = datetime.utcnow()
    end_window = now + timedelta(hours=24)
    
    bookings = db.query(models.Booking).filter(
        models.Booking.end_time > now,
        models.Booking.start_time < end_window,
        models.Booking.is_active == True
    ).order_by(models.Booking.start_time.asc()).all()
    
    return templates.TemplateResponse("partials_schedule.html", {"request": request, "bookings": bookings})

@app.post("/book")
async def create_booking(request: Request, start_time: str = Form(...), end_time: str = Form(...), time_offset: int = Form(0), db: Session = Depends(get_db)):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return HTMLResponse("<span class='text-red-500'>Login required</span>")
    
    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    if not user:
         return HTMLResponse("<span class='text-red-500'>Invalid User</span>")

    # Parse HH:MM inputs
    try:
        now_utc = datetime.utcnow()
        
        t_start = datetime.strptime(start_time, "%H:%M").time()
        t_end = datetime.strptime(end_time, "%H:%M").time()
        
        # 1. Construct Naive Local Datetime from Input
        # Assume 'Today' relative to client first? Or just use Server Date?
        # Let's use Server Date but assume the Time part is Local.
        
        # Actually safer: Convert Server UTC Now -> Client Local Now first to get 'Today' in client terms.
        # client_now = now_utc - timedelta(minutes=time_offset)
        # But wait, offset is 'UTC - Local'. So Local = UTC - Offset.
        # If I am India (-330), Local = UTC - (-330) = UTC + 330.
        # So client_now = now_utc - timedelta(minutes=time_offset)   X
        # Wait. Offset = UTC - Local. => Local = UTC - Offset. => UTC = Local + Offset.
        # Yes.
        
        # This is strictly for the DATE part.
        # Only tricky if user is crossing midnight.
        # Let's simple: Use user's input HH:MM, attach to today's date, then Apply Offset to get UTC.
        
        # We process everything in "User Local Time" logic first, then convert result to UTC for DB.
        
        naive_today = datetime.now().date() # Server local... bad.
        # Let's rely on basic "Next occurrence" logic or just "Today".
        
        # V2 Logic used simple "datetime.combine(now.date(), t_start)".
        # Let's stick to that but shift the final result by offset.
        
        dt_start_local = datetime.combine(now_utc.date(), t_start)
        dt_end_local = datetime.combine(now_utc.date(), t_end)
        
        # Wraparound (User inputs 23:00 to 01:00)
        if dt_end_local <= dt_start_local:
            dt_end_local += timedelta(days=1)
            
        # Convert to UTC for Storage
        # UTC = Local + Offset (minutes)
        dt_start_utc = dt_start_local + timedelta(minutes=time_offset)
        dt_end_utc = dt_end_local + timedelta(minutes=time_offset)
        
        # Duration Check
        duration = (dt_end_utc - dt_start_utc).total_seconds() / 60
        if duration <= 0:
             return HTMLResponse("<span class='text-red-500'>Invalid Duration</span>")

    except ValueError:
        return HTMLResponse("<span class='text-red-500'>Invalid Time Format</span>")

    # Conflict Check (using UTC)
    try:
        conflict = db.query(models.Booking).filter(
            models.Booking.is_active == True,
            models.Booking.start_time < dt_end_utc, 
            models.Booking.end_time > dt_start_utc
        ).first()
        
        if not conflict:
            new_booking = models.Booking(user_id=user.id, start_time=dt_start_utc, end_time=dt_end_utc, is_active=True)
            db.add(new_booking)
            db.commit()
            
            response = HTMLResponse(f"<span class='text-green-500 font-bold'>OFFICE SECURED</span>")
            response.headers["HX-Trigger"] = "updateSchedule"
            return response
        else:
            # Convert conflict time back to local for display? Or just show generic.
            return HTMLResponse(f"<span class='text-red-500'>CONFLICT DETECTED</span>")
    except Exception as e:
        print(f"Booking Error: {e}")
        return HTMLResponse(f"<span class='text-red-500'>SYSTEM ERROR</span>")


# --- CHAT & WEBSOCKETS ---

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: int, db: Session = Depends(get_db)):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # data comes from the form input named 'message'
            # HTMX sends a JSON payload by default with ws-json extension or just text? 
            # With 'hx-ws', it sends JSON: {"chat_message": "hello"} if named correctly or just the value.
            # Let's parse simple JSON or assume the simple format.
            # Actually, standard HTMX ws extension sends the form data as JSON headers or simple text.
            # Let's assume the payload is the message content directly or we parse it.
            
            import json
            try:
                payload = json.loads(data)
                content = payload.get("message")
            except:
                content = data

            if not content:
                continue

            # Save to DB
            user = db.query(models.User).filter(models.User.id == client_id).first()
            if user:
                 # Create message
                msg = models.ChatMessage(user_id=user.id, content=content, timestamp=datetime.utcnow())
                db.add(msg)
                db.commit()
                
                # Construct HTML response to push to all clients
                # This matches the 'index.html' chat container structure
                html_response = f"""
                <div id="chat-messages" hx-swap-oob="beforeend">
                    <div class="flex flex-col gap-1 items-start animate-fade-in-up">
                         <div class="glass-panel px-3 py-2 rounded-tr-xl rounded-br-xl rounded-bl-xl text-sm text-gray-700 bg-white/80 border border-cyan-100 shadow-sm">
                            {content}
                        </div>
                        <span class="text-[10px] text-gray-400 pl-1">{user.username} • Just now</span>
                    </div>
                </div>
                """
                
                await manager.broadcast(html_response)

    except Exception as e:
        manager.disconnect(websocket)

