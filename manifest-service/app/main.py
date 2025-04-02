import os
import asyncio
from loguru import logger
from dotenv import load_dotenv
from fastapi import FastAPI
from .api.routes import router 
from .worker import SNSWorker

load_dotenv()
logger.add("manifest_service.log", rotation="10 MB")

app = FastAPI(
    title="Manifest Service",
    description="Service for generating and managing HLS manifests",
    version="1.0.0"
)

app.incluede_router(router, prefix="/api")
worker =  None  # Initialize the worker instance

@app.on_event("startup")
async def startup_event():
    """
    Initialize and start the SNS worker on application startup.
    """
    global worker
    worker = SNSWorker()

    asyncio.create_task(worker.start())
    logger.info("SNS worker started")

@app.on_event("shutdown")
async def shutdown_event():
    """
    Stop the SNS worker on application shutdown.
    """
    global worker
    if worker:
        worker.stop()
        logger.info("SNS worker stopped")

@app.get("/health")
async def health_check():
    """
    Health check endpoint to verify the service is running.
    """
    return {"status": "healthy"}


#here in this file we are defining the routes for the api and also defining the startup and shutdown events for the application 
#it deines when the SNS worker should start and stop