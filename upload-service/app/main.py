import logging
from dotenv import load_dotenv
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # Add this import
from app.api.routes import router as api_router

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

app = FastAPI(
    title = "ABR Streaming Application",
    description = "This is a streaming application that allows users to upload videos and stream them.",
    version = "0.1.0",
)

# Add CORS middleware - place this BEFORE including the router
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins instead of wildcard
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # Include OPTIONS
    allow_headers=["*"],  # In production, specify needed headers
)

app.include_router(api_router, prefix="/api")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "upload-service"}

@app.on_event("startup")
async def startup_event():
    logging.info("Upload Service is starting up...")

@app.on_event("shutdown")
async def shutdown_event():
    logging.info("Upload Service is shutting down...")