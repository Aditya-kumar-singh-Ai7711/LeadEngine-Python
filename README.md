# 🚀 LeadEngine

LeadEngine is a Python-based lead generation and outreach automation system that helps discover, qualify, and organize business leads from the web.

The project combines search automation, web scraping, contact extraction, lead scoring, and Excel reporting into a single workflow.

---

## Overview

Finding high-quality business leads manually is time-consuming.

LeadEngine automates the process by:

- Discovering businesses through search queries
- Extracting contact information
- Finding social profiles and contact pages
- Scoring leads based on available signals
- Organizing results into structured Excel reports
- Avoiding duplicate domains across multiple runs

The goal is to reduce manual research time and create outreach-ready lead lists.

---

## Features

### Lead Discovery

- Search-based business discovery
- Niche + location targeting
- Multiple search variations per query
- Domain deduplication

### Contact Extraction

- Email extraction
- Phone number extraction
- Contact page discovery
- LinkedIn company page detection
- Instagram profile detection

### Lead Qualification

- Lead scoring system
- Outreach angle generation
- Business signal analysis
- High-priority lead identification

### Data Management

- Persistent query history
- Duplicate prevention
- Structured lead storage
- Excel export with formatting

### Founder Enrichment

For high-scoring leads, LeadEngine attempts to identify:

- Founder
- CEO
- Owner
- LinkedIn profile

---

## Technologies Used

### Programming

- Python

### Libraries

- Pandas
- Requests
- BeautifulSoup
- CloudScraper
- OpenPyXL
- Python-Dotenv

### Concepts

- Web Scraping
- Data Processing
- Automation
- Concurrent Execution
- Lead Qualification
- Data Cleaning

---

## Project Workflow

```text
User Query
    ↓
Search Discovery
    ↓
Business Website Collection
    ↓
Contact Extraction
    ↓
Lead Qualification
    ↓
Founder Enrichment
    ↓
Excel Report Generation
```

---

## Example Output

Each lead includes:

- Company Name
- Website
- Email Address
- Phone Number
- Contact Page
- Instagram
- LinkedIn Company Page
- Founder Information
- Lead Score
- Outreach Angle

---

## Key Learnings

While building LeadEngine, I gained practical experience with:

- Python automation
- Large-scale data extraction
- Multi-threading
- API integration
- Data cleaning
- Excel automation
- Lead generation workflows

---

## Setup

### Clone Repository

```bash
git clone https://github.com/yourusername/LeadEngine.git
cd LeadEngine
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Create Environment File

Create a `.env` file:

```env
SERPER_API_KEY=your_api_key_here
```

### Run

```bash
python leadengine.py
```

---

## Project Status

✅ Working Project

🔨 Actively being improved with better filtering, qualification, and outreach intelligence.

---

## Author

**Aditya Kumar Singh**

LinkedIn:
https://linkedin.com/in/aditya-kumar-singh-0450733a7
