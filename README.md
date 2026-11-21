# Flight Deal Scanner

A Python scanner for finding weekend flight deals from TLV (or any origin) using Travelpayouts / Aviasales Data API.

## Features
- **Configurable Search**: Origin, trip horizon, weekend days, flex days, min/max stay duration, max stops, budget segments, destination/country exclusions, and favorite destination highlights.
- **Smart Booking Links**: Pre-filled direct booking links where supported (Wizz Air, Ryanair), Google Flights, Skyscanner, Aviasales, and airline sites.
- **Interactive Reports**: Responsive dark/light HTML report (`deals_report.html`) with live search, month/price/direct/favorites/new-drop filters, client-side sorting, airline names, flight durations, and CSV export (`deals.csv`).
- **Price-Drop & New Deal Tracking**: Tracks previous run prices in `.price_history.json` (cached across runs in GitHub Actions) to highlight `NEW` deals and price drops (`↓ $...`).

## Local Setup & Usage

1. **Clone repository**:
   ```bash
   git clone https://github.com/romanvelingard/flight_checker.git
   cd flight_checker
   ```

2. **Set up Virtual Environment**:
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .\.venv\Scripts\Activate.ps1
   ```

3. **Configure API Token**:
   Copy `config.json.example` to `config.json` and set your Travelpayouts API token:
   ```json
   {
     "api_token": "YOUR_TRAVELPAYOUTS_TOKEN_HERE"
   }
   ```
   *(Or set `export TRAVELPAYOUTS_TOKEN=your_token` / `set TRAVELPAYOUTS_TOKEN=your_token`)*

4. **Run Scanner**:
   ```bash
   python flight_deals.py
   ```

---

## Scheduled Automation on GitHub (GitHub Actions)

This repository includes a pre-configured **GitHub Actions Workflow** (`.github/workflows/scan_deals.yml`) that automatically runs the scanner every day at 06:00 UTC (9:00 AM Israel Time) and lets you trigger manual runs with 1 click.

### Setup Instructions (3 Simple Steps):

1. **Add Your API Secret to GitHub**:
   - Go to your repository on GitHub: `https://github.com/romanvelingard/flight_checker`
   - Navigate to **Settings** -> **Secrets and variables** -> **Actions**.
   - Click **New repository secret**.
   - **Name**: `TRAVELPAYOUTS_TOKEN`
   - **Secret**: Paste your Travelpayouts API token (from **Profile -> API token** on Travelpayouts).
   - Click **Add secret**.

2. **Enable Live Web Report (GitHub Pages)**:
   - In your repository, go to **Settings** -> **Pages**.
   - Under **Build and deployment** -> **Source**, select **GitHub Actions**.
   - Your live report will be published at: `https://romanvelingard.github.io/flight_checker/` (also accessible at `deals_report.html`).

3. **Manual Run anytime**:
   - Go to the **Actions** tab on GitHub -> Click **Flight Deals Scanner** -> Click **Run workflow**.
