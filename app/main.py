from datetime import datetime
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/current-time")
def current_time():
    return {"current_time": datetime.now().isoformat()}