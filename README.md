# Flight Deal Scanner

A Python scanner for finding weekend flight deals from TLV (or any origin) using Travelpayouts / Aviasales Data API.

## Features
- **Configurable Search**: Origin, trip horizon, weekend days, flex days, min/max stay duration, max stops, and budget segments.
- **Direct Links**: Generates direct booking links for airlines (Wizz Air, Ryanair, El Al, Israir, Arkia, Aegean, etc.), Google Flights 1-click links, and Aviasales links.
- **Outputs**: Detailed HTML report (`deals_report.html`) and CSV export (`deals.csv`).

## Setup & Usage

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

Outputs `deals_report.html` and `deals.csv`.
