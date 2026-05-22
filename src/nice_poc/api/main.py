from fastapi import FastAPI

from nice_poc import __version__
from nice_poc.api.routers import health

app = FastAPI(title="NICE PoC Backend", version=__version__)
app.include_router(health.router)
