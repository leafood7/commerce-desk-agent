"""Run the authenticated application, independently of the legacy demo API."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("workbench.app:app", host="127.0.0.1", port=8082)
