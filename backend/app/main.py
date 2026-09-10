from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, command_profiles, credentials, device_roles, device_types, devices, jobs, snapshots, users
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="ConfigCollector", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Auth uses a Bearer token in the Authorization header, not cookies, so a
    # wildcard origin is safe here (no allow_credentials/cookie exposure).
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Content-Disposition isn't on the browser's default CORS-safelisted
    # response headers, so without this, frontend JS can read the snapshot
    # download's *body* fine but silently gets an empty string back for
    # response.headers["content-disposition"] - the filename it carries
    # (hostname, extension, optional timestamp) would never be visible to
    # the page that's supposed to use it to name the saved file.
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(credentials.router)
app.include_router(devices.router)
app.include_router(device_types.router)
app.include_router(device_roles.router)
app.include_router(command_profiles.router)
app.include_router(jobs.router)
app.include_router(snapshots.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
