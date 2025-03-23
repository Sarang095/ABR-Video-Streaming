import logging
from dotenv import load_dotenv
import os
from fastapi import FastAPI
from app.api.routes import router as api_router

load_dotenv() #loading the envirionment variables from .env file

logging.basicConfig(  #setting up the logging configuration - telling the logger what to log and how to log it 
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

app = FastAPI(
    title = "ABR Streaming Application",
    description = "This is a streaming application that allows users to upload videos and stream them.",
    version = "0.1.0",
)

app.include_router(api_router, prefix="/api")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "upload-service"}

#these startup and shutdown events are used to perform any necessary setup or cleanup tasks when the application starts up or shuts down and helps cleaning the resources used by the application.
@app.on_event("startup") # tells the application to run this function when the application starts up
async def startup_event():
    logging.info("Upload Service is starting up...")

@app.on_event("shutdown") # tells the application to run this function when the application shuts down
async def shutdown_event():
    logging.info("Upload Service is shutting down...")

