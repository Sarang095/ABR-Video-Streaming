import os
import sys
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

logger.remove()
logger.add(
    sys.stdout,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    level="INFO"
)

def init_app():
    
    logger.info("Transcoding Service initialized successfully") 
    return True

if __name__ == "__main__":
    init_app()


#This files is just the entry point for the application. It initializes the logger and loads the environment variables. 
#Note that this isn't a web server like in the Upload Service - it's just initialization code that will be imported by the worker 