import os
import asyncio
from fastapi import FastAPI
from loguru import logger
from dotenv import load_dotenv
from .api.routes import router
from .worker import SNSWorker

# Load environment variables from .env file if present
load_dotenv()

# Configure logger
logger.add("manifest_service.log", rotation="10 MB")

# Create FastAPI app
app = FastAPI(
    title="Manifest Service",
    description="Service for generating HLS manifests for ABR streaming",
    version="1.0.0"
)

# Include API routes
app.include_router(router, prefix="/api")

# SNS Worker instance
worker = None

@app.on_event("startup")
async def startup_event():
    """Initialize and start the SNS worker on application startup."""
    global worker
    worker = SNSWorker()
    
    # Start the worker in a background task
    asyncio.create_task(worker.start())
    logger.info("SNS worker started")

@app.on_event("shutdown")
async def shutdown_event():
    """Stop the SNS worker on application shutdown."""
    global worker
    if worker:
        worker.stop()
        logger.info("SNS worker stopped")

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}

#here in this file we are defining the routes for the api and also defining the startup and shutdown events for the application 
#it deines when the SNS worker should start and stop