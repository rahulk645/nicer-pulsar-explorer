import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from google import genai

# Configuration
DATA_FILE_PATH = "pulsars_data.json"
# Using https:// and compliant parameters
ARXIV_QUERY_URL = (
    "https://export.arxiv.org/api/query?search_query="
    "cat:astro-ph.HE+AND+(NICER+AND+(%22mass-radius%22+OR+%22pulse+profile%22+OR+%22X-PSI%22))"
    "&start=0&max_results=5&sortBy=submittedDate&sortOrder=descending"
)


def fetch_recent_arxiv_papers(max_retries=4, base_delay=5):
  """Fetch recent papers from arXiv API with rate-limit handling (HTTP 429)."""
  print("Fetching recent preprints from arXiv API...")

  # Generic browser User-Agents are flagged by arXiv on cloud/runner IPs.
  # An identifiable research client string avoids bot-filter rejections.
  headers = {
      "User-Agent": (
          "NICER-Pulsar-Tracker/1.0 (Astrophysics Research Bot;"
          " mailto:academic-researcher@domain.edu)"
      ),
      "Accept": "application/atom+xml",
  }
  req = urllib.request.Request(ARXIV_QUERY_URL, headers=headers)

  xml_data = None
  for attempt in range(max_retries):
    try:
      with urllib.request.urlopen(req, timeout=30) as response:
        xml_data = response.read()
        break
    except urllib.error.HTTPError as e:
      if e.code == 429:
        wait_time = base_delay * (2**attempt)
        print(
            f"arXiv API returned 429 Too Many Requests. Retrying in"
            f" {wait_time}s (attempt {attempt + 1}/{max_retries})..."
        )
        time.sleep(wait_time)
      else:
        print(f"HTTP error occurred while calling arXiv: {e}")
        raise e
    except Exception as e:
      print(f"Network error: {e}")
      raise e

  if xml_data is None:
    print(
        "Failed to fetch papers from arXiv due to persistent rate limiting."
        " Skipping this run."
    )
    return []

  root = ET.fromstring(xml_data)
  ns = {"atom": "http://www.w3.org/2005/Atom"}

  papers = []
  for entry in root.findall("atom:entry", ns):
    title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
    summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
    paper_id = entry.find("atom:id", ns).text.strip()
    published = entry.find("atom:published", ns).text.strip()[:10]

    authors = [
        a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)
    ]

    papers.append({
        "id": paper_id,
        "title": title,
        "summary": summary,
        "published": published,
        "authors": (
            ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        ),
    })

  print(f"Retrieved {len(papers)} candidate papers from arXiv.")
  return papers


def extract_metrics_with_gemini(paper, api_key):
  """Use Gemini API to check if paper has new M-R measurements and parse them."""
  client = genai.Client(api_key=api_key)

  prompt = f"""
    Analyze this astrophysics preprint abstract to check if it reports NEW NICER pulse profile modeling or Mass-Radius measurements for any of these 7 core pulsars:
    - PSR J0030+0451
    - PSR J0740+6620
    - PSR J0437-4715
    - PSR J0614-3329
    - PSR J1231-1411
    - PSR J2124-3358
    - PSR J1614-2230

    Paper Title: {paper['title']}
    Authors: {paper['authors']}
    Abstract: {paper['summary']}

    If this paper presents NEW empirical mass/radius constraints, return a JSON object with this exact structure:
    {{
      "is_nicer_mr_analysis": true,
      "pulsar_id": "j0030",
      "analysis": {{
        "author": "{paper['authors'].split(',')[0]} ({paper['published'][:4]})",
        "group": "X-PSI or IM or Independent",
        "mass": "1.40 ± 0.12",
        "radius": "11.71 ± 0.85",
        "status": "ACTIVE",
        "supersededBy": null,
        "keyFeature": "Brief summary of key method or finding (max 15 words)"
      }},
      "supersedes_previous": true
    }}

    Valid pulsar_id options: j0030, j0740, j0437, j0614, j1231, j2124, j1614.

    If it does NOT contain new empirical M-R estimates for these 7 pulsars, respond ONLY with:
    {{"is_nicer_mr_analysis": false}}
    """

  try:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return json.loads(response.text)
  except Exception as e:
    print(
        f"Error parsing Gemini output for paper '{paper.get('title', '')[:30]}...':"
        f" {e}"
    )
    return {"is_nicer_mr_analysis": False}


def update_database():
  api_key = os.environ.get("GEMINI_API_KEY")
  if not api_key:
    print("Error: GEMINI_API_KEY environment variable not set.")
    return

  # Load existing database
  if os.path.exists(DATA_FILE_PATH):
    with open(DATA_FILE_PATH, "r") as f:
      database = json.load(f)
  else:
    print(f"Database file {DATA_FILE_PATH} not found.")
    return

  papers = fetch_recent_arxiv_papers()
  if not papers:
    print("No papers retrieved or arXiv request was throttled.")
    return

  updated = False

  for paper in papers:
    print(f"Analyzing preprint: {paper['title'][:50]}...")
    extracted = extract_metrics_with_gemini(paper, api_key)

    if extracted.get("is_nicer_mr_analysis"):
      pulsar_id = extracted.get("pulsar_id")
      new_analysis = extracted.get("analysis")

      # Find target pulsar in database
      target_pulsar = next((p for p in database if p["id"] == pulsar_id), None)
      if target_pulsar and new_analysis:
        # Check if analysis already exists to prevent duplicate entries
        exists = any(
            a["author"] == new_analysis.get("author")
            for a in target_pulsar.get("analyses", [])
        )
        if not exists:
          print(
              f"Adding new analysis for {target_pulsar['name']}:"
              f" {new_analysis['author']}"
          )

          if extracted.get("supersedes_previous"):
            for old_a in target_pulsar.get("analyses", []):
              if old_a.get("status") == "ACTIVE":
                old_a["status"] = "SUPERSEDED"
                old_a["supersededBy"] = new_analysis["author"]

          target_pulsar["analyses"].insert(0, new_analysis)
          updated = True

    # Sleep 1 second between Gemini calls to stay within free-tier quota
    time.sleep(1)

  if updated:
    with open(DATA_FILE_PATH, "w") as f:
      json.dump(database, f, indent=4)
    print("Database successfully updated and saved!")
  else:
    print("No new mass-radius analyses detected today.")


if __name__ == "__main__":
  update_database()