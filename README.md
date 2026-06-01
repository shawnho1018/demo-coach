# Demo Coach - GECX Agent Web Client

A web-based user interface to interact with GECX (Gemini Enterprise for Customer Experience) agents for both text and voice.

## Requirements
- Python >= 3.11
- `uv` package manager
- Google Cloud credentials (ADC) with access to the GECX agent deployment.

## Configuration

Before running the application, you need to configure the environment variables:

1. Copy the sample environment file to `.env`:
   ```bash
   cp .env-example .env
   ```

2. Open the newly created `.env` file and configure the target GECX agent details:
   - `GCP_PROJECT`: The GCP project ID where the GECX agent is deployed.
   - `GCP_LOCATION`: The GCP region/location of the deployment (e.g., `us`).
   - `GECX_APP_ID`: The ID of your GECX agent application.
   - `GECX_DEPLOYMENT_ID`: The deployment ID of the GECX agent.

## Running Locally

1. Authenticate with Google Cloud:
   Ensure your local environment is authenticated with Application Default Credentials (ADC) for the configured project:
   ```bash
   gcloud auth application-default login
   ```

2. Start the application using `uv`:
   ```bash
   uv run uvicorn app.main:app --reload
   ```

3. Open `http://localhost:8080` in your web browser.
